"""管理后台 — 模板管理."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin, require_super_admin, redis_client
from app.models.template import SpecTemplate
from app.schemas.admin import TemplateCreateRequest, TemplateUpdateRequest, TemplateToggleRequest
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("", response_model=ResponseModel)
async def list_templates(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """模板列表（含禁用模板）."""
    result = await db.execute(select(SpecTemplate).order_by(SpecTemplate.id))
    templates = result.scalars().all()

    items = []
    for t in templates:
        items.append({
            "id": t.id,
            "name": t.name,
            "width_px": t.width_px,
            "height_px": t.height_px,
            "dpi": t.dpi,
            "min_kb": t.min_kb,
            "max_kb": t.max_kb,
            "allowed_bg_colors": json.loads(t.allowed_bg_colors) if isinstance(t.allowed_bg_colors, str) else t.allowed_bg_colors,
            "output_format": t.output_format,
            "physical_size_mm": t.physical_size_mm,
            "is_active": t.is_active,
            "remark": t.remark,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        })

    # 前端期望直接返回数组（与 admin/src/api/templates.ts 的 Template[] 对齐）
    return ResponseModel(code=200, data=items)


@router.post("", response_model=ResponseModel)
async def create_template(
    payload: TemplateCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """新增模板（超级管理员）."""
    template = SpecTemplate(
        name=payload.name,
        width_px=payload.width_px,
        height_px=payload.height_px,
        dpi=payload.dpi,
        min_kb=payload.min_kb,
        max_kb=payload.max_kb,
        allowed_bg_colors=payload.allowed_bg_colors,
        output_format=payload.output_format,
        physical_size_mm=payload.physical_size_mm,
        remark=payload.remark,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    # 清除 Redis 模板缓存
    if redis_client:
        await redis_client.delete("templates:active:cache")

    return ResponseModel(code=200, data={"id": template.id}, message="模板创建成功")


@router.put("/{template_id}", response_model=ResponseModel)
async def update_template(
    template_id: int,
    payload: TemplateUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """编辑模板（超级管理员）."""
    result = await db.execute(select(SpecTemplate).where(SpecTemplate.id == template_id))
    template = result.scalar()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(template, key, value)

    await db.commit()

    if redis_client:
        await redis_client.delete("templates:active:cache")

    return ResponseModel(code=200, message="模板更新成功")


@router.delete("/{template_id}", response_model=ResponseModel)
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """删除模板（超级管理员）."""
    result = await db.execute(select(SpecTemplate).where(SpecTemplate.id == template_id))
    template = result.scalar()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    await db.delete(template)
    await db.commit()

    if redis_client:
        await redis_client.delete("templates:active:cache")

    return ResponseModel(code=200, message="模板已删除")


@router.patch("/{template_id}/toggle", response_model=ResponseModel)
async def toggle_template(
    template_id: int,
    payload: TemplateToggleRequest = None,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """启用/禁用（若传入 is_active 则设为指定状态，否则翻转）."""
    result = await db.execute(select(SpecTemplate).where(SpecTemplate.id == template_id))
    template = result.scalar()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    if payload is not None and payload.is_active is not None:
        template.is_active = payload.is_active
    else:
        template.is_active = not template.is_active
    await db.commit()

    if redis_client:
        await redis_client.delete("templates:active:cache")

    return ResponseModel(code=200, data={"is_active": template.is_active}, message="状态已切换")
