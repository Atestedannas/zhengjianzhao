"""系统配置表 ORM 模型 — free_count_config 与 system_config."""

from sqlalchemy import String, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IntPKMixin


class FreeCountConfig(Base, IntPKMixin):
    """免费次数配置表."""

    __tablename__ = "free_count_config"

    config_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="配置键")
    config_value: Mapped[str] = mapped_column(String(128), nullable=False, comment="配置值")
    description: Mapped[str] = mapped_column(String(256), default=None, nullable=True, comment="说明")
    updated_at: Mapped[str] = mapped_column(String(32), default=None, nullable=True, comment="更新时间")


class SystemConfig(Base, IntPKMixin):
    """系统配置表."""

    __tablename__ = "system_config"

    config_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="配置键")
    config_value: Mapped[str] = mapped_column(Text, nullable=False, comment="配置值")
    config_type: Mapped[str] = mapped_column(
        SAEnum("string", "int", "json", "bool", name="config_type_enum"),
        default="string", nullable=False,
    )
    description: Mapped[str] = mapped_column(String(256), default=None, nullable=True, comment="说明")
    updated_at: Mapped[str] = mapped_column(String(32), default=None, nullable=True, comment="更新时间")
