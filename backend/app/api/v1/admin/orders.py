"""管理后台 — 订单管理."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import select, func, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin, require_super_admin
from app.models.order import Order
from app.core.payment.wechat_pay import WechatPayment
from app.core.payment.alipay import AlipayPayment
from app.schemas.common import ResponseModel

router = APIRouter()

wechat_pay = WechatPayment()
alipay_pay = AlipayPayment()


@router.get("", response_model=ResponseModel)
async def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    size: int = Query(None, include_in_schema=False),
    status: str = Query(None),
    pay_method: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """订单列表（分页+筛选）."""
    if size is not None:
        page_size = size
    conditions = []
    if status:
        conditions.append(Order.status == status)
    if pay_method:
        conditions.append(Order.pay_method == pay_method)
    if start_date:
        conditions.append(Order.created_at >= start_date)
    if end_date:
        conditions.append(Order.created_at <= end_date + " 23:59:59")

    base_query = select(Order).where(and_(*conditions)) if conditions else select(Order)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    result = await db.execute(
        base_query.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    orders = result.scalars().all()

    items = []
    for o in orders:
        items.append({
            "id": o.id,
            "order_no": o.order_no,
            "user_id": o.user_id,
            "amount": float(o.amount),
            "pay_method": o.pay_method.value if hasattr(o.pay_method, 'value') else str(o.pay_method),
            "trade_no": o.trade_no,
            "status": o.status.value if hasattr(o.status, 'value') else str(o.status),
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "refund_amount": float(o.refund_amount) if o.refund_amount else None,
            "refund_at": o.refund_at.isoformat() if o.refund_at else None,
            "expire_at": o.expire_at.isoformat() if o.expire_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    return ResponseModel(
        code=200,
        data={"items": items, "total": total, "page": page, "size": page_size},
    )


@router.post("/{order_id}/refund", response_model=ResponseModel)
async def refund_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """发起退款（超级管理员）."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status.value != "paid":
        raise HTTPException(status_code=400, detail="仅已支付订单可退款")

    # 调用支付平台退款
    refund_amount = float(order.amount)
    if order.pay_method and "wechat" in order.pay_method.value:
        refund_result = await wechat_pay.refund(
            out_trade_no=order.trade_no,
            refund_amount=refund_amount,
            total_amount=refund_amount,
        )
    elif order.pay_method and order.pay_method.value == "alipay":
        refund_result = await alipay_pay.refund(
            out_trade_no=order.trade_no,
            refund_amount=refund_amount,
            total_amount=refund_amount,
        )
    else:
        raise HTTPException(status_code=400, detail="不支持的支付方式")

    if not refund_result.get("success"):
        raise HTTPException(status_code=500, detail=refund_result.get("message", "退款失败"))

    # 更新订单状态
    await db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(
            status="refunded",
            refund_amount=refund_amount,
            refund_at=datetime.now(),
            refund_trade_no=refund_result.get("refund_trade_no", ""),
        )
    )
    await db.commit()

    logger.info(f"Admin {admin.id} refunded order {order.order_no}")

    return ResponseModel(code=200, message="退款成功")
