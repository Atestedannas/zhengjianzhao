"""支付接口 — 创建订单、回调处理、订单查询."""

import json
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, get_current_user
from app.core.payment.wechat_pay import WechatPayment
from app.core.payment.alipay import AlipayPayment
from app.models.user import User
from app.models.order import Order
from app.models.system_config import FreeCountConfig
from app.schemas.payment import CreatePaymentRequest
from app.schemas.common import ResponseModel
from app.utils.error_codes import ErrorCode

router = APIRouter()

wechat_pay = WechatPayment()
alipay_pay = AlipayPayment()


def _generate_order_no() -> str:
    """生成内部订单号."""
    now = datetime.now()
    return f"PZ{now.strftime('%Y%m%d%H%M%S')}{int(time.time() * 1000) % 100000:05d}"


async def _get_unit_price(db: AsyncSession) -> float:
    """获取单次处理价格."""
    result = await db.execute(
        select(FreeCountConfig.config_value).where(
            FreeCountConfig.config_key == "unit_price"
        )
    )
    val = result.scalar()
    return float(val) if val else 0.99


@router.post("/create", response_model=ResponseModel)
async def create_payment(
    req: CreatePaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建支付订单并返回支付参数."""
    amount = await _get_unit_price(db)
    order_no = _generate_order_no()
    description = "照片合规处理服务"
    expire_at = datetime.now() + timedelta(minutes=15)

    # 创建订单
    order = Order(
        order_no=order_no,
        user_id=current_user.id,
        amount=amount,
        pay_method=req.pay_method,
        status="pending",
        expire_at=expire_at,
    )
    db.add(order)
    await db.flush()

    notify_url = settings.WECHAT_NOTIFY_URL if "wechat" in req.pay_method else settings.ALIPAY_NOTIFY_URL

    if req.pay_method in ("wechat_jsapi", "wechat_native"):
        pay_type = "jsapi" if req.pay_method == "wechat_jsapi" else "native"
        result = await wechat_pay.create_order(
            out_trade_no=order_no,
            amount=amount,
            description=description,
            notify_url=settings.WECHAT_NOTIFY_URL,
            pay_type=pay_type,
            openid=current_user.openid if pay_type == "jsapi" else None,
        )
    elif req.pay_method == "alipay":
        result = await alipay_pay.create_order(
            out_trade_no=order_no,
            amount=amount,
            description=description,
            notify_url=settings.ALIPAY_NOTIFY_URL,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported payment method: {req.pay_method}")

    await db.commit()

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "支付下单失败"))

    return ResponseModel(
        code=200,
        data={
            "order_no": order_no,
            "amount": amount,
            "pay_params": result.get("pay_params"),
        },
    )


@router.post("/wechat-notify")
async def wechat_payment_notify(request: Request, db: AsyncSession = Depends(get_db)):
    """微信支付回调通知."""
    body = await request.body()
    headers = dict(request.headers)

    verified_data = await wechat_pay.verify_notify(body, headers)
    if verified_data is None:
        logger.error("WeChat notify signature verification failed.")
        return {"code": "FAIL", "message": "Signature verification failed"}

    out_trade_no = verified_data.get("out_trade_no", "")
    trade_no = verified_data.get("trade_no", "")

    # 幂等处理：订单已支付则直接返回成功
    result = await db.execute(select(Order).where(Order.order_no == out_trade_no))
    order = result.scalar()
    if order is None:
        logger.error(f"WeChat notify: order {out_trade_no} not found.")
        return {"code": "SUCCESS", "message": "OK"}
    if order.status != "pending":
        return {"code": "SUCCESS", "message": "OK"}

    # 更新订单状态
    await db.execute(
        update(Order)
        .where(Order.order_no == out_trade_no)
        .values(status="paid", trade_no=trade_no, paid_at=datetime.now())
    )
    await db.commit()
    logger.info(f"WeChat payment success: order={out_trade_no} trade_no={trade_no}")
    return {"code": "SUCCESS", "message": "OK"}


@router.post("/alipay-notify")
async def alipay_payment_notify(request: Request, db: AsyncSession = Depends(get_db)):
    """支付宝支付回调通知."""
    body = await request.body()
    headers = dict(request.headers)

    verified_data = await alipay_pay.verify_notify(body, headers)
    if verified_data is None:
        logger.error("Alipay notify signature verification failed.")
        return "failure"

    out_trade_no = verified_data.get("out_trade_no", "")
    trade_no = verified_data.get("trade_no", "")

    result = await db.execute(select(Order).where(Order.order_no == out_trade_no))
    order = result.scalar()
    if order is None:
        logger.error(f"Alipay notify: order {out_trade_no} not found.")
        return "success"
    if order.status != "pending":
        return "success"

    await db.execute(
        update(Order)
        .where(Order.order_no == out_trade_no)
        .values(status="paid", trade_no=trade_no, paid_at=datetime.now())
    )
    await db.commit()
    logger.info(f"Alipay payment success: order={out_trade_no} trade_no={trade_no}")
    return "success"


@router.get("/query/{order_no}", response_model=ResponseModel)
async def query_payment(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询订单支付状态."""
    result = await db.execute(select(Order).where(Order.order_no == order_no))
    order = result.scalar()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")

    return ResponseModel(
        code=200,
        data={
            "order_no": order.order_no,
            "amount": float(order.amount),
            "status": order.status,
            "trade_no": order.trade_no,
            "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        },
    )
