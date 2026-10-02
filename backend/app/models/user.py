"""用户表 ORM 模型."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, String, Integer, DECIMAL, DateTime, Enum as SAEnum, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, IntPKMixin


class User(Base, IntPKMixin, TimestampMixin):
    """用户表."""

    __tablename__ = "users"

    openid: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="微信/支付宝 openid"
    )
    unionid: Mapped[Optional[str]] = mapped_column(
        String(64), default=None, comment="微信 unionid（跨应用）"
    )
    platform: Mapped[str] = mapped_column(
        SAEnum("wechat_mini", "wechat_web", "alipay_web", name="user_platform_enum"),
        nullable=False,
        comment="注册来源平台",
    )
    nickname: Mapped[str] = mapped_column(
        String(128), default="", comment="昵称"
    )
    avatar_url: Mapped[str] = mapped_column(
        String(512), default="", comment="头像URL"
    )
    free_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="剩余免费次数"
    )
    free_count_total: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="累计获得免费次数"
    )
    balance: Mapped[float] = mapped_column(
        DECIMAL(10, 2), default=0.00, nullable=False, comment="账户余额"
    )
    total_spent: Mapped[float] = mapped_column(
        DECIMAL(10, 2), default=0.00, nullable=False, comment="累计消费金额"
    )
    is_active: Mapped[bool] = mapped_column(
        Integer, default=1, nullable=False, comment="是否启用（0=禁用）"
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=None, comment="最近登录时间"
    )

    __table_args__ = (
        # uk_openid_platform 在 alembic 迁移中创建
    )
