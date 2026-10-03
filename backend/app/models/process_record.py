"""处理记录表 ORM 模型."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DECIMAL, DateTime, JSON, Enum as SAEnum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ProcessRecord(Base):
    """处理记录表."""

    __tablename__ = "process_records"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="记录ID"
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), default=None, comment="用户ID"
    )
    template_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("spec_templates.id", ondelete="SET NULL"), default=None, comment="模板ID"
    )
    request_params: Mapped[dict] = mapped_column(JSON, nullable=False, comment="处理参数快照")
    is_paid: Mapped[bool] = mapped_column(Integer, default=0, nullable=False, comment="是否付费")
    paid_amount: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 2), default=None, comment="实付金额")
    payment_trade_no: Mapped[Optional[str]] = mapped_column(String(64), default=None, comment="支付单号")
    original_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="原始文件大小（Byte）")
    result_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="处理后文件大小（Byte）")
    result_pixels: Mapped[Optional[str]] = mapped_column(String(16), default=None, comment="输出像素")
    result_dpi: Mapped[Optional[int]] = mapped_column(Integer, default=None, comment="输出DPI")
    bg_color: Mapped[Optional[str]] = mapped_column(String(16), default=None, comment="使用的背景色")
    processing_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="处理耗时（毫秒）")
    status: Mapped[str] = mapped_column(
        SAEnum("pending", "processing", "success", "failed", name="process_status_enum"),
        default="pending", nullable=False, comment="处理状态（异步任务：pending→processing→success/failed）"
    )
    error_message: Mapped[Optional[str]] = mapped_column(String(512), default=None, comment="错误信息")
    thumb_path: Mapped[Optional[str]] = mapped_column(String(512), default=None, comment="缩略图路径")
    original_path: Mapped[Optional[str]] = mapped_column(
        String(512), default=None, comment="原图暂存路径（celery 任务读取，处理完删除）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="创建时间", default=datetime.utcnow
    )

    # 关系
    user = relationship("User", backref="process_records")
    template = relationship("SpecTemplate", backref="process_records")
