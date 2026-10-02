"""用户相关 Pydantic 模型."""

from typing import Optional
from pydantic import BaseModel, Field


class WechatLoginRequest(BaseModel):
    """小程序静默登录请求."""
    code: str = Field(..., description="wx.login 返回的 code")


class WebLoginCallbackRequest(BaseModel):
    """Web 扫码登录回调."""
    code: str = Field(..., description="授权 code")
    state: str = Field(..., description="防CSRF state")


class TokenResponse(BaseModel):
    """Token 返回体."""
    access_token: str
    refresh_token: str
    expires_in: int = 7200
    is_new_user: bool = False


class RefreshTokenRequest(BaseModel):
    """刷新 token 请求."""
    refresh_token: str


class UserProfile(BaseModel):
    """用户信息."""
    id: int
    nickname: str = ""
    avatar_url: str = ""
    free_count: int = 0
    balance: float = 0.0
    total_spent: float = 0.0
    platform: str = ""
    created_at: Optional[str] = None
    last_login_at: Optional[str] = None

    class Config:
        from_attributes = True


class FreeCountResponse(BaseModel):
    """免费次数查询响应."""
    free_count: int
    has_free: bool
