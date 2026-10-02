"""管理后台 — 财务报表."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin
from app.models.order import Order
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("/report", response_model=ResponseModel)
async def finance_report(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """按日期聚合收入报表."""
    data = []
    for i in range(days - 1, -1, -1):
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)
        day_end = day_start + timedelta(days=1)

        # 当日收入
        income_result = await db.execute(
            select(func.coalesce(func.sum(Order.amount), 0)).where(
                Order.status == "paid",
                Order.paid_at >= day_start,
                Order.paid_at < day_end,
            )
        )
        income = float(income_result.scalar() or 0)

        # 当日支付笔数
        paid_result = await db.execute(
            select(func.count(Order.id)).where(
                Order.status == "paid",
                Order.paid_at >= day_start,
                Order.paid_at < day_end,
            )
        )
        paid_count = paid_result.scalar() or 0

        # 当日退款
        refund_amount_result = await db.execute(
            select(func.coalesce(func.sum(Order.refund_amount), 0)).where(
                Order.status == "refunded",
                Order.refund_at >= day_start,
                Order.refund_at < day_end,
            )
        )
        refund_amount = float(refund_amount_result.scalar() or 0)

        refund_count_result = await db.execute(
            select(func.count(Order.id)).where(
                Order.status == "refunded",
                Order.refund_at >= day_start,
                Order.refund_at < day_end,
            )
        )
        refund_count = refund_count_result.scalar() or 0

        data.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "income": income,
            "paid_count": paid_count,
            "refund_amount": refund_amount,
            "refund_count": refund_count,
        })

    return ResponseModel(code=200, data={"items": data})
