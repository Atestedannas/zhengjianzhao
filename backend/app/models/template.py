"""规格模板表 ORM 模型."""

from sqlalchemy import String, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, IntPKMixin


class SpecTemplate(Base, IntPKMixin, TimestampMixin):
    """照片规格模板表."""

    __tablename__ = "spec_templates"

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模板名称")
    width_px: Mapped[int] = mapped_column(Integer, nullable=False, comment="目标宽度（像素）")
    height_px: Mapped[int] = mapped_column(Integer, nullable=False, comment="目标高度（像素）")
    dpi: Mapped[int] = mapped_column(Integer, default=350, nullable=False, comment="目标DPI")
    min_kb: Mapped[int] = mapped_column(Integer, nullable=False, comment="最小文件大小（KB）")
    max_kb: Mapped[int] = mapped_column(Integer, nullable=False, comment="最大文件大小（KB）")
    allowed_bg_colors: Mapped[dict] = mapped_column(JSON, nullable=False, comment="可选背景色列表")
    output_format: Mapped[str] = mapped_column(String(8), default="JPEG", nullable=False, comment="输出格式")
    physical_size_mm: Mapped[dict] = mapped_column(String(32), nullable=True, default=None, comment="物理尺寸描述")
    is_active: Mapped[bool] = mapped_column(Integer, default=1, nullable=False, comment="是否启用")
    remark: Mapped[dict] = mapped_column(Text, nullable=True, default=None, comment="备注/公告原文链接")
