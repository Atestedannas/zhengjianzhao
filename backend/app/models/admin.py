"""管理员表 ORM 模型."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Enum as SAEnum, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IntPKMixin, TimestampMixin


class AdminUser(Base, IntPKMixin, TimestampMixin):
    """管理员表."""

    __tablename__ = "admin_users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="管理员用户名")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False, comment="bcrypt密码哈希")
    role: Mapped[str] = mapped_column(
        String(32), default="normal_admin", nullable=False, comment="角色"
    )
    is_active: Mapped[bool] = mapped_column(Integer, default=1, nullable=False, comment="是否启用")
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, comment="最近登录时间")
