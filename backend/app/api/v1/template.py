"""模板接口 — 查询可用模板."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_optional_user
from app.models.template import SpecTemplate
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("/", response_model=ResponseModel)
async def get_templates(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取所有启用模板列表（登录可选：首页/营销页未登录也要能看规格）."""
    result = await db.execute(
        select(SpecTemplate).where(SpecTemplate.is_active == True).order_by(SpecTemplate.id)
    )
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
            "remark": t.remark,
        })

    return ResponseModel(code=200, data={"items": items, "count": len(items)})


@router.get("/{template_id}", response_model=ResponseModel)
async def get_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取模板详情（登录可选）."""
    result = await db.execute(
        select(SpecTemplate).where(SpecTemplate.id == template_id)
    )
    template = result.scalar()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    return ResponseModel(
        code=200,
        data={
            "id": template.id,
            "name": template.name,
            "width_px": template.width_px,
            "height_px": template.height_px,
            "dpi": template.dpi,
            "min_kb": template.min_kb,
            "max_kb": template.max_kb,
            "allowed_bg_colors": json.loads(template.allowed_bg_colors) if isinstance(template.allowed_bg_colors, str) else template.allowed_bg_colors,
            "output_format": template.output_format,
            "physical_size_mm": template.physical_size_mm,
            "is_active": template.is_active,
            "remark": template.remark,
        },
    )
