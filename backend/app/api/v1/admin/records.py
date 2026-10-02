"""管理后台 — 处理记录管理."""

import json
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin
from app.models.process_record import ProcessRecord
from app.models.user import User
from app.models.template import SpecTemplate
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("", response_model=ResponseModel)
async def list_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    size: int = Query(None, include_in_schema=False),
    template_id: int = Query(None),
    status: str = Query(None),
    is_paid: bool = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """处理记录列表（分页+筛选）."""
    if size is not None:
        page_size = size
    conditions = []
    if template_id is not None:
        conditions.append(ProcessRecord.template_id == template_id)
    if status:
        conditions.append(ProcessRecord.status == status)
    if is_paid is not None:
        conditions.append(ProcessRecord.is_paid == is_paid)
    if start_date:
        conditions.append(ProcessRecord.created_at >= start_date)
    if end_date:
        conditions.append(ProcessRecord.created_at <= end_date + " 23:59:59")

    base_query = select(ProcessRecord).where(and_(*conditions)) if conditions else select(ProcessRecord)

    # 总数
    count_result = await db.execute(
        select(func.count(ProcessRecord.id)).where(and_(*conditions))
    )
    total = count_result.scalar() or 0

    # 分页数据（联表补充用户昵称、模板名称）
    result = await db.execute(
        select(ProcessRecord, User.nickname, SpecTemplate.name)
        .outerjoin(User, ProcessRecord.user_id == User.id)
        .outerjoin(SpecTemplate, ProcessRecord.template_id == SpecTemplate.id)
        .where(and_(*conditions))
        .order_by(ProcessRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.all()

    items = []
    for r, user_nickname, template_name in rows:
        items.append({
            "id": r.id,
            "user_id": r.user_id,
            "user_nickname": user_nickname,
            "template_id": r.template_id,
            "template_name": template_name,
            "request_params": r.request_params,
            "original_size": r.original_size,
            "result_size": r.result_size,
            "result_pixels": r.result_pixels,
            "result_dpi": r.result_dpi,
            "bg_color": r.bg_color,
            "is_paid": r.is_paid,
            "paid_amount": float(r.paid_amount) if r.paid_amount else None,
            "payment_trade_no": r.payment_trade_no,
            "processing_time_ms": r.processing_time_ms,
            "status": r.status,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return ResponseModel(
        code=200,
        data={"items": items, "total": total, "page": page, "size": page_size},
    )


@router.get("/export", response_model=ResponseModel)
async def export_records(
    start_date: str = Query(None),
    end_date: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """导出记录为 CSV."""
    conditions = []
    if start_date:
        conditions.append(ProcessRecord.created_at >= start_date)
    if end_date:
        conditions.append(ProcessRecord.created_at <= end_date + " 23:59:59")

    base_query = select(ProcessRecord).where(and_(*conditions)) if conditions else select(ProcessRecord)
    result = await db.execute(base_query.order_by(ProcessRecord.created_at.desc()).limit(10000))
    records = result.scalars().all()

    import csv
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "用户ID", "模板ID", "是否付费", "原始大小", "输出大小", "像素", "DPI", "背景色", "耗时(ms)", "状态", "时间"])
    for r in records:
        writer.writerow([
            r.id, r.user_id, r.template_id, r.is_paid,
            r.original_size, r.result_size, r.result_pixels, r.result_dpi,
            r.bg_color, r.processing_time_ms, r.status,
            r.created_at.isoformat() if r.created_at else "",
        ])

    csv_data = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([csv_data]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=process_records_export.csv"},
    )


@router.get("/{record_id}", response_model=ResponseModel)
async def get_record_detail(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """记录详情."""
    result = await db.execute(select(ProcessRecord).where(ProcessRecord.id == record_id))
    record = result.scalar()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found.")

    # 补充用户昵称、模板名称（前端详情页依赖）
    user_nickname = None
    if record.user_id is not None:
        user = await db.get(User, record.user_id)
        user_nickname = user.nickname if user else None
    template_name = None
    if record.template_id is not None:
        template = await db.get(SpecTemplate, record.template_id)
        template_name = template.name if template else None

    return ResponseModel(
        code=200,
        data={
            "id": record.id,
            "user_id": record.user_id,
            "user_nickname": user_nickname,
            "template_id": record.template_id,
            "template_name": template_name,
            "request_params": record.request_params,
            "original_size": record.original_size,
            "result_size": record.result_size,
            "result_pixels": record.result_pixels,
            "result_dpi": record.result_dpi,
            "bg_color": record.bg_color,
            "is_paid": record.is_paid,
            "paid_amount": float(record.paid_amount) if record.paid_amount else None,
            "payment_trade_no": record.payment_trade_no,
            "processing_time_ms": record.processing_time_ms,
            "status": record.status,
            "error_message": record.error_message,
            "thumb_path": record.thumb_path,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        },
    )
