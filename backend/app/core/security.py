"""JWT 签发/校验、bcrypt 密码哈希、防重放、Token 黑名单."""

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import bcrypt
from jose import JWTError, jwt
from loguru import logger

from app.config import settings

# ---------- 密码哈希 ----------
#
# 【为什么不再用 passlib】
# passlib 1.7.4（2020 年后已停止维护）与 bcrypt >= 4.1 不兼容，会导致
# 应用**启动即崩溃、容器反复重启**（gunicorn "Worker failed to boot."）：
#
#   1. bcrypt 4.1 移除了 __about__，passlib 读版本号时报
#      "module 'bcrypt' has no attribute '__about__'"（仅告警，不致命）
#
#   2. 致命的是：passlib 在初始化 bcrypt 后端时会调用 detect_wrap_bug()，
#      该探测函数**故意**传入一段 >72 字节的密码。bcrypt 4.x 起对超长密码
#      从「静默截断」改为「抛异常」，于是后端加载直接失败：
#          ValueError: password cannot be longer than 72 bytes
#
#   所以这里直接调用 bcrypt，彻底移除 passlib，从根上消除该问题。
#
# 兼容性：哈希格式仍是 bcrypt 的 $2b$...，与历史 passlib 生成的哈希完全
# 互通（checkpw 只认格式，不关心是谁生成的），老数据无需迁移。

# bcrypt 算法固有上限：只使用密码的前 72 字节
_BCRYPT_MAX_BYTES = 72
# 计算强度。12 与 passlib 的 bcrypt 默认值一致，避免老哈希验不过
_BCRYPT_ROUNDS = 12


def _to_bcrypt_bytes(password: str) -> bytes:
    """把明文转成 bcrypt 可接受的字节串（显式截断到 72 字节）.

    旧版 bcrypt/passlib 会静默截断超长密码，4.x 起改为抛异常。
    这里显式截断，既避免异常，又与历史哈希保持同一语义
    （否则原本能登录的账号会突然验不过）。

    注意必须在 UTF-8 编码之后按**字节**截断，不能按字符截断：
    一个中文字符占 3 字节，24 个中文就超过 72 字节了。
    """
    raw = password if isinstance(password, bytes) else str(password).encode("utf-8")
    return raw[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码，返回 $2b$ 开头的字符串."""
    hashed = bcrypt.hashpw(_to_bcrypt_bytes(password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码.

    任何异常（哈希格式非法、空值、脏数据）一律返回 False，
    不把异常抛给调用方——避免一个坏哈希导致整个接口 500。
    """
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            _to_bcrypt_bytes(plain_password),
            hashed_password.encode("utf-8"),
        )
    except Exception as e:
        logger.warning(f"Password verify failed (malformed hash?): {e}")
        return False


# ---------- JWT ----------

def create_access_token(user_id: int, extra_claims: Optional[dict] = None) -> str:
    """签发 access_token（有效期 2h）."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """签发 refresh_token（有效期 7d）."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """解码 access_token，返回 payload 或 None."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError as e:
        logger.debug(f"JWT decode error: {e}")
        return None


def decode_refresh_token(token: str) -> Optional[dict]:
    """解码 refresh_token，返回 payload 或 None."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError as e:
        logger.debug(f"Refresh token decode error: {e}")
        return None


# ---------- Token 黑名单 (Redis) ----------

async def add_token_to_blacklist(redis_client, token: str, ttl: int) -> None:
    """将 token 加入黑名单，TTL 设为 token 的剩余有效期."""
    if redis_client is None:
        return
    token_fingerprint = token[-20:]  # 只存后20位降低存储
    await redis_client.setex(f"blacklist:token:{token_fingerprint}", ttl, "1")


async def is_token_blacklisted(redis_client, token: str) -> bool:
    """检查 token 是否在黑名单中."""
    if redis_client is None:
        return False
    token_fingerprint = token[-20:]
    return await redis_client.exists(f"blacklist:token:{token_fingerprint}") > 0


# ---------- 防重放 ----------

async def check_replay(redis_client, nonce: str, timestamp: str) -> bool:
    """
    防重放检查:
    - 时间戳与当前时间差 <= 5 分钟
    - nonce 在 60s 内未被使用过
    返回 True 表示通过检查.
    """
    if redis_client is None:
        return True

    try:
        req_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if abs((now - req_time).total_seconds()) > 300:
            logger.warning(f"Replay attack: timestamp {timestamp} too old")
            return False

        # Redis SADD: 如果 key 已存在返回 0（重复）
        added = await redis_client.sadd("replay_nonces", nonce)
        if not added:
            logger.warning(f"Replay attack: nonce {nonce} already used")
            return False

        # 设置 nonce 集合的 TTL
        await redis_client.expire("replay_nonces", 60)
        return True
    except Exception as e:
        logger.error(f"Replay check error: {e}")
        return True  # 降级放行
