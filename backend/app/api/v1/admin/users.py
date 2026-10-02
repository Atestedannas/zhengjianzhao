"""管理后台 — 用户管理."""

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin, require_super_admin
from app.models.user import User
from app.models.process_record import ProcessRecord
from app.models.template import SpecTemplate
from app.models.order import Order
from app.schemas.admin import AdjustFreeCountRequest, UserToggleRequest
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("", response_model=ResponseModel)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    size: int = Query(None, include_in_schema=False),
    keyword: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """用户列表（分页+搜索）."""
    if size is not None:
        page_size = size
    conditions = []
    if keyword:
        conditions.append(
            User.nickname.contains(keyword)
        )
    if start_date:
        conditions.append(User.created_at >= start_date)
    if end_date:
        conditions.append(User.created_at <= end_date + " 23:59:59")

    base_query = select(User).where(and_(*conditions)) if conditions else select(User)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    result = await db.execute(
        base_query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    users = result.scalars().all()

    items = []
    for u in users:
        items.append({
            "id": u.id,
            "openid": u.openid[:8] + "****" if u.openid else "",
            "platform": u.platform.value if hasattr(u.platform, 'value') else str(u.platform),
            "nickname": u.nickname,
            "avatar_url": u.avatar_url,
            "free_count": u.free_count,
            "free_count_total": u.free_count_total,
            "balance": float(u.balance),
            "total_spent": float(u.total_spent),
            "is_active": u.is_active,
            "last_login_at": u.last_login_at,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return ResponseModel(
        code=200,
        data={"items": items, "total": total, "page": page, "size": page_size},
    )


@router.get("/{user_id}", response_model=ResponseModel)
async def get_user_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """用户详情."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    # 处理记录（前端详情页依赖 template_name / status / created_at）
    rec_result = await db.execute(
        select(ProcessRecord, SpecTemplate.name)
        .outerjoin(SpecTemplate, ProcessRecord.template_id == SpecTemplate.id)
        .where(ProcessRecord.user_id == user_id)
        .order_by(ProcessRecord.created_at.desc())
        .limit(50)
    )
    records = [
        {
            "id": r.id,
            "template_id": r.template_id,
            "template_name": tname,
            "status": r.status,
            "is_paid": r.is_paid,
            "processing_time_ms": r.processing_time_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r, tname in rec_result.all()
    ]

    # 订单明细（前端详情页依赖 order_no / amount / pay_method / status）
    ord_result = await db.execute(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(50)
    )
    orders = [
        {
            "id": o.id,
            "order_no": o.order_no,
            "amount": float(o.amount),
            "pay_method": o.pay_method.value if hasattr(o.pay_method, "value") else o.pay_method,
            "status": o.status.value if hasattr(o.status, "value") else o.status,
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in ord_result.scalars().all()
    ]

    return ResponseModel(
        code=200,
        data={
            "id": user.id,
            "openid": user.openid[:8] + "****",
            "platform": user.platform.value if hasattr(user.platform, 'value') else str(user.platform),
            "nickname": user.nickname,
            "avatar_url": user.avatar_url,
            "free_count": user.free_count,
            "free_count_total": user.free_count_total,
            "balance": float(user.balance),
            "total_spent": float(user.total_spent),
            "is_active": user.is_active,
            "last_login_at": user.last_login_at,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
            "records": records,
            "orders": orders,
        },
    )


@router.post("/{user_id}/free-count", response_model=ResponseModel)
async def adjust_free_count(
    user_id: int,
    payload: AdjustFreeCountRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """手动调整用户免费次数.

    mode=delta（默认）：count 为增量，可正可负；
    mode=set：count 为目标值（后台「调整为」弹窗用这个语义）。
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    requested = payload.count
    reason = payload.reason

    if payload.mode == "set":
        target = max(0, requested)
        delta = target - user.free_count
        user.free_count = target
    else:
        delta = requested
        user.free_count = max(0, user.free_count + delta)

    await db.commit()

    logger.info(
        f"Admin {admin.id} adjusted free count for user {user_id}: "
        f"mode={payload.mode}, count={requested}, delta={delta:+d}, reason={reason}"
    )

    if payload.mode == "set":
        message = f"免费次数已设为 {user.free_count}"
    else:
        message = f"免费次数已调整（{delta:+d}）"

    return ResponseModel(
        code=200,
        data={"free_count": user.free_count, "delta": delta, "mode": payload.mode},
        message=message,
    )


@router.patch("/{user_id}/toggle", response_model=ResponseModel)
async def toggle_user(
    user_id: int,
    payload: UserToggleRequest = None,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """启用/禁用用户（若传入 is_active 则设为指定状态，否则翻转）."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if payload is not None and payload.is_active is not None:
        user.is_active = payload.is_active
    else:
        user.is_active = not user.is_active
    await db.commit()

    return ResponseModel(
        code=200,
        data={"is_active": user.is_active},
        message="用户状态已切换",
    )


@router.post("/{user_id}/set-unlimited", response_model=ResponseModel)
async def set_unlimited_count(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """设置/取消用户无限免费次数（free_count = -1 表示无限）."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.free_count == -1:
        # 取消无限 → 恢复为 0 次
        user.free_count = 0
        message = "已取消无限次数，免费次数重置为 0"
        is_unlimited = False
    else:
        # 设置为无限
        user.free_count = -1
        message = "已设置为无限免费次数"
        is_unlimited = True

    await db.commit()

    logger.info(f"Admin {admin.id} set unlimited={is_unlimited} for user {user_id}")

    return ResponseModel(
        code=200,
        data={
            "free_count": user.free_count,
            "unlimited": is_unlimited,
        },
        message=message,
    )
