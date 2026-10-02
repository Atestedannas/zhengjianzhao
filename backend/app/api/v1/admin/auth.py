"""管理员登录/登出/刷新 Token."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, redis_client as rc
from app.core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    add_token_to_blacklist,
    hash_password as get_password_hash,
)
from app.models.admin import AdminUser
from app.schemas.admin import AdminLoginRequest, AdminRefreshRequest
from app.schemas.common import ResponseModel

router = APIRouter()


@router.post("/login", response_model=ResponseModel)
async def admin_login(
    payload: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """管理员登录."""
    result = await db.execute(
        select(AdminUser).where(AdminUser.username == payload.username)
    )
    admin = result.scalar()
    if admin is None or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not admin.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    admin.last_login_at = datetime.utcnow()
    await db.commit()

    # 签发 JWT（在 sub 中携带 role 标识）
    extra_claims = {"role": admin.role, "is_admin": True, "scope": "admin"}
    access_token = create_access_token(admin.id, extra_claims=extra_claims)
    refresh_token = create_refresh_token(admin.id)

    logger.info(f"Admin login: {admin.username}")

    return ResponseModel(
        code=200,
        data={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "username": admin.username,
            "role": admin.role,
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        },
    )


@router.post("/refresh", response_model=ResponseModel)
async def admin_refresh(
    payload: AdminRefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """刷新管理员 access_token（使用 refresh_token 换取新 token）."""
    data = decode_refresh_token(payload.refresh_token)
    if data is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

    admin_id = int(data.get("sub", 0))
    result = await db.execute(
        select(AdminUser).where(AdminUser.id == admin_id, AdminUser.is_active == True)  # noqa: E712
    )
    admin = result.scalar()
    if admin is None:
        raise HTTPException(status_code=401, detail="Admin not found or disabled.")

    # 将旧 refresh_token 加入黑名单，防止重放
    if rc:
        await add_token_to_blacklist(rc, payload.refresh_token, settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400)

    extra_claims = {"role": admin.role, "is_admin": True, "scope": "admin"}
    new_access = create_access_token(admin.id, extra_claims=extra_claims)
    new_refresh = create_refresh_token(admin.id)

    return ResponseModel(
        code=200,
        data={
            "access_token": new_access,
            "refresh_token": new_refresh,
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        },
    )


@router.post("/logout", response_model=ResponseModel)
async def admin_logout():
    """管理员退出登录."""
    return ResponseModel(code=200, message="已退出登录")
