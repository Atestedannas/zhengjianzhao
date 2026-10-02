"""公共依赖注入 — DB session、JWT用户/管理员解析、Redis."""

from typing import AsyncGenerator, Optional

from fastapi import Depends, Header, HTTPException
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from jose import JWTError, jwt

from app.config import settings
from app.utils.error_codes import ErrorCode, make_response

# ---------- 数据库引擎 ----------
_db_url = settings.DATABASE_URL_FINAL
_is_sqlite = "sqlite" in _db_url

_engine_kwargs = dict(
    echo=settings.DEBUG,
)
if not _is_sqlite:
    _engine_kwargs.update(pool_size=20, max_overflow=10, pool_recycle=3600)

engine = create_async_engine(_db_url, **_engine_kwargs)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取异步数据库 session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------- Redis ----------
redis_client: Optional[Redis] = None
_redis_failed = False


async def get_redis() -> Redis:
    """获取 Redis 客户端（单例）."""
    global redis_client, _redis_failed
    if redis_client is None and not _redis_failed:
        try:
            redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
            await redis_client.ping()
        except Exception:
            _redis_failed = True
            redis_client = None
    return redis_client


# ---------- JWT 用户解析 ----------
async def get_current_user(
    authorization: str = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """解析 Bearer Token，返回 User ORM 对象."""
    from app.models.user import User
    from sqlalchemy import select

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="未提供认证凭据"))

    token_str = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token_str, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="无效的Token"))
        user_id = int(user_id)
    except HTTPException:
        raise
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="Token已过期或无效"))

    r = await get_redis()
    if r is not None:
        token_fp = token_str[-20:]
        if await r.exists(f"blacklist:token:{token_fp}"):
            raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="Token已失效"))

    # 每日免费次数：每天第一次带 token 的请求自动补发（后台可开关，幂等）。
    # 必须放在加载 User 之前 —— 发放函数会先 commit，
    # 否则 MySQL REPEATABLE READ 下随后读到的仍是发放前的旧次数。
    # 发放异常已在函数内部吞掉并回滚，不会影响正常接口。
    from app.core.daily_bonus import grant_daily_bonus_on_request

    await grant_daily_bonus_on_request(db, user_id)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="用户不存在"))
    if not user.is_active:
        raise HTTPException(status_code=403, detail=make_response(ErrorCode.USER_DISABLED, message="用户已被禁用"))
    return user


async def get_optional_user(
    authorization: str = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """解析 Bearer Token（可选）：未携带 / 无效 / 已失效时一律返回 None，不抛 401.

    用于「登录可选」的公开接口（如模板列表），
    让未登录用户也能正常浏览首页，而不是被 401 弹去登录。
    """
    from app.models.user import User
    from sqlalchemy import select

    if not authorization or not authorization.startswith("Bearer "):
        return None

    token_str = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token_str, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None

    r = await get_redis()
    if r is not None:
        token_fp = token_str[-20:]
        if await r.exists(f"blacklist:token:{token_fp}"):
            return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def get_current_admin(
    authorization: str = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """解析管理员 Bearer Token，返回 AdminUser ORM 对象."""
    from app.models.admin import AdminUser
    from sqlalchemy import select

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="未提供认证凭据"))

    token_str = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token_str, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        admin_id: int = payload.get("sub")
        scope: str = payload.get("scope", "")
        if admin_id is None or "admin" not in scope:
            raise HTTPException(status_code=403, detail=make_response(ErrorCode.ADMIN_PERMISSION_DENIED, message="非管理员凭据"))
    except JWTError:
        raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="Token已过期或无效"))

    r = await get_redis()
    if r is not None:
        token_fp = token_str[-20:]
        if await r.exists(f"blacklist:token:{token_fp}"):
            raise HTTPException(status_code=401, detail=make_response(ErrorCode.UNAUTHORIZED, message="Token已失效"))

    result = await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    admin = result.scalar_one_or_none()
    if admin is None or not admin.is_active:
        raise HTTPException(status_code=403, detail=make_response(ErrorCode.ADMIN_PERMISSION_DENIED, message="管理员不存在或已禁用"))
    return admin


async def require_super_admin(admin=Depends(get_current_admin)):
    """要求超级管理员角色."""
    if admin.role != "super_admin":
        raise HTTPException(status_code=403, detail=make_response(ErrorCode.ADMIN_PERMISSION_DENIED, message="需要超级管理员权限"))
    return admin
