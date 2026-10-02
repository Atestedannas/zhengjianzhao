"""订单相关 Pydantic 模型."""

from typing import Optional
from pydantic import BaseModel


class OrderOut(BaseModel):
    """订单响应."""
    id: int
    order_no: str
    user_id: int
    amount: float
    pay_method: Optional[str] = None
    trade_no: Optional[str] = None
    status: str
    paid_at: Optional[str] = None
    refund_amount: Optional[float] = None
    refund_at: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class OrderListQuery(BaseModel):
    """订单列表查询参数."""
    page: int = 1
    page_size: int = 20
    user_id: Optional[int] = None
    status: Optional[str] = None
    pay_method: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
