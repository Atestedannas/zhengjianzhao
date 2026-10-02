"""鉴权接口：小程序静默登录、Web扫码登录、Token刷新."""

import secrets
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, get_current_user
from app.core.security import (
    create_access_token, create_refresh_token,
    decode_refresh_token, add_token_to_blacklist,
)
from app.core.pricing_config import get_register_bonus
from app.models.user import User
from app.schemas.user import (
    WechatLoginRequest, WebLoginCallbackRequest as WebLoginRequest,
    TokenResponse, RefreshTokenRequest as RefreshRequest,
)
from app.schemas.common import ResponseModel
from pydantic import BaseModel


class WebLoginRequest(BaseModel):
    password: str

router = APIRouter()


def _generate_state(provider: str) -> str:
    """生成防 CSRF 的 state，并带上平台前缀.

    回调页只能拿到 state，用它就能知道该调哪个回调接口。
    """
    return f"{provider}.{secrets.token_urlsafe(24)}"


async def _remember_state(state: str) -> None:
    """把 state 写进 Redis，回调时校验并一次性删除."""
    from app.dependencies import get_redis

    r = await get_redis()
    if r is None:
        logger.warning("Redis 不可用，扫码登录的 state 校验被跳过")
        return
    await r.setex(f"oauth:state:{state}", 300, "1")


async def _consume_state(state: str) -> None:
    """校验并消费 state（一次性，5 分钟有效）."""
    from app.dependencies import get_redis

    r = await get_redis()
    if r is None:
        logger.warning("Redis 不可用，扫码登录的 state 校验被跳过")
        return
    if await r.get(f"oauth:state:{state}") is None:
        raise HTTPException(status_code=400, detail="登录状态已失效，请重新扫码")
    await r.delete(f"oauth:state:{state}")


ALIPAY_GATEWAY = "https://openapi.alipay.com/gateway.do"


def _alipay_sign(params: dict) -> str:
    """RSA2 签名（规则与 app/core/payment/alipay.py 保持一致）."""
    import base64
    import os

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    path = settings.ALIPAY_PRIVATE_KEY_PATH
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=503, detail="支付宝扫码登录未配置（缺少应用私钥文件）")

    with open(path, "rb") as f:
        key_data = f.read()

    # 按 key 字母序拼接，排除空值和 sign 本身
    sign_str = "&".join(
        f"{k}={v}" for k, v in sorted(params.items(), key=lambda x: x[0]) if v and k != "sign"
    )

    try:
        private_key = serialization.load_pem_private_key(key_data, password=None)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"支付宝应用私钥无法解析: {e}")

    signature = private_key.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


async def _alipay_call(method: str, response_key: str, **extra) -> dict:
    """调用支付宝开放平台接口（自动补公共参数 + 签名）."""
    params = {
        "app_id": settings.ALIPAY_APP_ID,
        "method": method,
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
    }
    params.update(extra)
    params["sign"] = _alipay_sign(params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(ALIPAY_GATEWAY, data=params)
    data = resp.json()

    payload = data.get(response_key)
    if not isinstance(payload, dict):
        err = data.get("error_response") or {}
        detail = err.get("sub_msg") or err.get("msg") or "未知错误"
        raise HTTPException(status_code=400, detail=f"支付宝扫码登录失败: {detail}")
    return payload


@router.post("/web-login", response_model=ResponseModel)
async def web_password_login(
    req: WebLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Web 桌面版密码登录（无需 OAuth）.
    使用 .env 中 WEB_LOGIN_PASSWORD 配置的密码登录.
    """
    if not settings.WEB_LOGIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Web 登录未配置（请在 .env 中设置 WEB_LOGIN_PASSWORD）")

    if req.password != settings.WEB_LOGIN_PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")

    # 查找或创建一个 web 用户
    result = await db.execute(
        select(User).where(User.platform == "wechat_web")
    )
    user = result.scalar()

    if user is None:
        # 初始免费次数与其它登录渠道一致，读后台「新用户注册赠送次数」
        # （历史 bug：这里硬编码 5，改后台的 register_bonus 对 PC 密码登录无效）
        bonus = await get_register_bonus(db)
        user = User(
            openid="web_user_" + secrets.token_hex(8),
            platform="wechat_web",
            nickname="Web用户",
            free_count=bonus,
            free_count_total=bonus,
        )
        db.add(user)
        await db.flush()
        logger.info(f"New web user created: id={user.id} bonus={bonus}")

    user.last_login_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return ResponseModel(
        code=200,
        message="登录成功",
        data=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            is_new_user=False,
        ).model_dump(),
    )


@router.post("/wechat-login", response_model=ResponseModel)
async def wechat_mini_login(req: WechatLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    小程序静默登录.
    接收 code → 微信换取 openid + session_key → 查/创用户 → 签发 JWT.
    """
    # 用 code 向微信换取 openid
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": settings.WECHAT_MINI_APPID,
                "secret": settings.WECHAT_MINI_SECRET,
                "js_code": req.code,
                "grant_type": "authorization_code",
            },
        )
        wx_data = resp.json()

    openid = wx_data.get("openid")
    if not openid:
        logger.error(f"WeChat login failed: {wx_data}")
        raise HTTPException(status_code=400, detail=f"微信登录失败: {wx_data.get('errmsg', '未知错误')}")

    unionid = wx_data.get("unionid")
    is_new_user = False

    # 查找或创建用户
    result = await db.execute(
        select(User).where(User.openid == openid, User.platform == "wechat_mini")
    )
    user = result.scalar()

    if user is None:
        # 新用户：获取注册赠送次数（统一走 pricing_config，默认值只有一处）
        bonus = await get_register_bonus(db)

        user = User(
            openid=openid,
            unionid=unionid,
            platform="wechat_mini",
            free_count=bonus,
            free_count_total=bonus,
        )
        db.add(user)
        await db.flush()
        is_new_user = True
        logger.info(f"New user created: openid={openid} bonus={bonus}")

    # 更新最后登录时间
    user.last_login_at = datetime.utcnow()  # type: ignore[assignment]
    await db.commit()
    await db.refresh(user)

    # 签发 Token
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return ResponseModel(
        code=200,
        message="登录成功",
        data=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            is_new_user=is_new_user,
        ).model_dump(),
    )


@router.post("/wechat-web-qrcode", response_model=ResponseModel)
async def wechat_web_qrcode():
    """生成微信 Web 扫码登录授权 URL（前端据此渲染二维码）."""
    if not settings.WECHAT_WEB_APPID:
        raise HTTPException(status_code=503, detail="微信扫码登录未配置（缺少 WECHAT_WEB_APPID）")

    redirect_uri = settings.web_oauth_redirect_uri("wechat")
    if not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="微信扫码登录未配置（请设置 WECHAT_WEB_REDIRECT_URI 或 PUBLIC_BASE_URL）",
        )

    state = _generate_state("wechat")
    auth_url = (
        f"https://open.weixin.qq.com/connect/qrconnect"
        f"?appid={settings.WECHAT_WEB_APPID}"
        # redirect_uri 必须整体 urlencode，否则带端口/路径的回调地址会被平台拒绝
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope=snsapi_login"
        f"&state={state}"
        f"#wechat_redirect"
    )

    await _remember_state(state)

    return ResponseModel(
        code=200,
        data={"auth_url": auth_url, "state": state},
    )


@router.post("/wechat-web-callback", response_model=ResponseModel)
async def wechat_web_callback(req: WebLoginRequest, db: AsyncSession = Depends(get_db)):
    """微信 Web 扫码回调."""
    # 校验 state
    await _consume_state(req.state)

    # code 换 access_token
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.weixin.qq.com/sns/oauth2/access_token",
            params={
                "appid": settings.WECHAT_WEB_APPID,
                "secret": settings.WECHAT_WEB_SECRET,
                "code": req.code,
                "grant_type": "authorization_code",
            },
        )
        wx_data = resp.json()

    openid = wx_data.get("openid")
    if not openid:
        raise HTTPException(status_code=400, detail=f"微信扫码登录失败: {wx_data.get('errmsg', '未知错误')}")

    access_token_wx = wx_data.get("access_token", "")
    is_new_user = False

    # 获取用户信息（昵称/头像）
    nickname, avatar_url = "", ""
    if access_token_wx:
        try:
            async with httpx.AsyncClient() as client:
                info_resp = await client.get(
                    "https://api.weixin.qq.com/sns/userinfo",
                    params={
                        "access_token": access_token_wx,
                        "openid": openid,
                    },
                )
                info_data = info_resp.json()
                nickname = info_data.get("nickname", "")
                avatar_url = info_data.get("headimgurl", "")
        except Exception as e:
            logger.warning(f"Failed to get WeChat userinfo: {e}")

    # 查找或创建用户
    result = await db.execute(
        select(User).where(User.openid == openid, User.platform == "wechat_web")
    )
    user = result.scalar()

    if user is None:
        bonus = await get_register_bonus(db)

        user = User(
            openid=openid,
            platform="wechat_web",
            nickname=nickname,
            avatar_url=avatar_url,
            free_count=bonus,
            free_count_total=bonus,
        )
        db.add(user)
        await db.flush()
        is_new_user = True

    user.last_login_at = datetime.utcnow()  # type: ignore[assignment]
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return ResponseModel(
        code=200,
        message="登录成功",
        data=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            is_new_user=is_new_user,
        ).model_dump(),
    )


@router.post("/alipay-web-qrcode", response_model=ResponseModel)
async def alipay_web_qrcode():
    """生成支付宝 Web 扫码登录授权 URL."""
    if not settings.ALIPAY_APP_ID:
        raise HTTPException(status_code=503, detail="支付宝扫码登录未配置（缺少 ALIPAY_APP_ID）")

    redirect_uri = settings.web_oauth_redirect_uri("alipay")
    if not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="支付宝扫码登录未配置（请设置 ALIPAY_REDIRECT_URI 或 PUBLIC_BASE_URL）",
        )

    state = _generate_state("alipay")

    auth_url = (
        f"https://openauth.alipay.com/oauth2/publicAppAuthorize.htm"
        f"?app_id={settings.ALIPAY_APP_ID}"
        f"&scope=auth_user"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&state={state}"
    )

    await _remember_state(state)

    return ResponseModel(
        code=200,
        data={"auth_url": auth_url, "state": state},
    )


@router.post("/alipay-web-callback", response_model=ResponseModel)
async def alipay_web_callback(req: WebLoginRequest, db: AsyncSession = Depends(get_db)):
    """支付宝 Web 扫码回调."""
    await _consume_state(req.state)

    # 支付宝返回的授权码参数名是 auth_code，前端回调页统一转成 code
    token_payload = await _alipay_call(
        "alipay.system.oauth.token",
        "alipay_system_oauth_token_response",
        grant_type="authorization_code",
        code=req.code,
    )

    alipay_user_id = token_payload.get("user_id", "")
    if not alipay_user_id:
        raise HTTPException(status_code=400, detail="支付宝扫码登录失败：未返回 user_id")

    # 昵称/头像（拿不到也不影响登录）
    nickname, avatar_url = "", ""
    ali_access_token = token_payload.get("access_token", "")
    if ali_access_token:
        try:
            info = await _alipay_call(
                "alipay.user.info.share",
                "alipay_user_info_share_response",
                auth_token=ali_access_token,
            )
            nickname = info.get("nick_name") or ""
            avatar_url = info.get("avatar") or ""
        except Exception as e:
            logger.warning(f"Failed to get Alipay userinfo: {e}")

    is_new_user = False
    result = await db.execute(
        select(User).where(User.openid == alipay_user_id, User.platform == "alipay_web")
    )
    user = result.scalar()

    if user is None:
        bonus = await get_register_bonus(db)

        user = User(
            openid=alipay_user_id,
            platform="alipay_web",
            nickname=nickname,
            avatar_url=avatar_url,
            free_count=bonus,
            free_count_total=bonus,
        )
        db.add(user)
        await db.flush()
        is_new_user = True
    else:
        # 老用户回填一次头像/昵称
        if nickname and not user.nickname:
            user.nickname = nickname
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url

    user.last_login_at = datetime.utcnow()  # type: ignore[assignment]
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return ResponseModel(
        code=200,
        message="登录成功",
        data=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            is_new_user=is_new_user,
        ).model_dump(),
    )


@router.post("/refresh", response_model=ResponseModel)
async def refresh_token(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """刷新 access_token."""
    payload = decode_refresh_token(req.refresh_token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

    user_id = int(payload.get("sub", 0))
    # 验证用户存在且未禁用
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found or disabled.")

    # 将旧 refresh_token 加入黑名单
    from app.dependencies import redis_client as rc
    if rc:
        await add_token_to_blacklist(rc, req.refresh_token, settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400)

    new_access = create_access_token(user_id)
    new_refresh = create_refresh_token(user_id)

    return ResponseModel(
        code=200,
        data=TokenResponse(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        ).model_dump(),
    )
