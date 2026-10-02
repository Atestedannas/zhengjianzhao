"""订单表 ORM 模型."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, DECIMAL, DateTime, Enum as SAEnum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Order(Base):
    """订单表."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="订单ID"
    )
    order_no: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, comment="内部订单号"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="用户ID"
    )
    amount: Mapped[float] = mapped_column(
        DECIMAL(10, 2), nullable=False, comment="订单金额（元）"
    )
    pay_method: Mapped[Optional[str]] = mapped_column(
        SAEnum("wechat_jsapi", "wechat_native", "alipay", name="pay_method_enum"),
        default=None, comment="支付方式"
    )
    trade_no: Mapped[Optional[str]] = mapped_column(
        String(64), default=None, comment="支付平台交易号"
    )
    status: Mapped[str] = mapped_column(
        SAEnum("pending", "paid", "refunded", "closed", name="order_status_enum"),
        default="pending", nullable=False, comment="订单状态"
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, comment="支付成功时间")
    refund_amount: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 2), default=None, comment="退款金额")
    refund_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, comment="退款时间")
    refund_trade_no: Mapped[Optional[str]] = mapped_column(String(64), default=None, comment="退款交易号")
    expire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="订单过期时间")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="创建时间", default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="更新时间", default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = relationship("User", backref="orders")
