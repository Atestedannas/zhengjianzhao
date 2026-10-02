"""照片处理接口 — 上传、处理、预览、下载、历史记录（增强版）."""

import json
import os
import time
import uuid
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from loguru import logger
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, get_current_user
from app.core.image_engine.pipeline import process_image, ProcessParams, ProcessResult
from app.core.image_engine.validator import validate_image
from app.core.image_engine.beautify import BeautyLevel
from app.core.image_engine.resize import SIZE_PRESETS, get_preset
from app.core.image_engine.format_converter import SUPPORTED_OUTPUT_FORMATS
from app.core.billing import process_with_billing
from app.models.user import User
from app.models.template import SpecTemplate
from app.models.process_record import ProcessRecord
from app.schemas.process import ProcessHistoryItem
from app.schemas.common import ResponseModel
from app.utils.error_codes import ErrorCode, NeedPaymentException
from app.utils.file_utils import generate_unique_filename as generate_temp_filename

router = APIRouter()


@router.post("/", response_model=ResponseModel)
async def process_photo(
    file: UploadFile = File(...),
    template_id: int = Form(None),
    # === 尺寸参数 ===
    width: int = Form(0),
    height: int = Form(0),
    resize_mode: str = Form("crop"),
    upscale: bool = Form(True),
    # === 输出参数 ===
    dpi: int = Form(300),
    min_kb: int = Form(0),
    max_kb: int = Form(100),
    bg_color: str = Form("white"),
    output_format: str = Form("JPEG"),
    # === 美颜参数（证件照只允许「自然微调」）===
    beautify_level: int = Form(BeautyLevel.COMPLIANT),
    beautify_smooth: bool = Form(True),
    beautify_brighten: bool = Form(True),
    beautify_blemish: bool = Form(True),
    # === 证件照 ===
    id_photo_align: bool = Form(False),
    gender: str = Form(None),
    # === 依赖 ===
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    照片处理接口（增强版）.

    新增功能:
    - 尺寸调整: resize_mode 支持 crop/exact/fit/fill/by_width/by_height
    - 格式转换: 支持 JPEG/PNG/PDF/WEBP/BMP/TIFF
    - 美颜: beautify_level 0=OFF 1=自然微调(COMPLIANT, 默认) 2/3=内部测试档
      （1/2/3 均会被证件照红线钳制：磨皮≤20%、保留≥85%原生纹理、不改骨相）
    - 证件照: id_photo_align 自动人脸对齐, gender 男女切换
    - 背景色: 支持 16 种颜色

    流程:
    1. 读取上传文件 → 校验
    2. 模板参数覆盖（如指定 template_id）
    3. 计费判断
    4. 图像处理流水线（人脸检测→美颜→裁剪→DPI→换底→压缩→格式转换）
    5. 保存结果 + 记录
    """
    file_bytes = await file.read()

    # 模板参数覆盖
    if template_id:
        result = await db.execute(select(SpecTemplate).where(SpecTemplate.id == template_id))
        tmpl = result.scalar()
        if tmpl is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found.")
        if not tmpl.is_active:
            raise HTTPException(status_code=400, detail=f"Template {tmpl.name} is disabled.")
        width = tmpl.width_px
        height = tmpl.height_px
        dpi = tmpl.dpi
        min_kb = tmpl.min_kb
        max_kb = tmpl.max_kb
        colors = tmpl.allowed_bg_colors
        if isinstance(colors, str):
            colors = json.loads(colors)
        if not colors:
            colors = ["white", "blue", "red"]
        if bg_color != "white" and bg_color not in colors:
            raise HTTPException(
                status_code=400,
                detail=f"模板 {tmpl.name} 不支持背景色 '{bg_color}'，仅支持: {', '.join(colors)}",
            )
        bg_color = colors[0] if bg_color == "white" else bg_color
        output_format = tmpl.output_format
        resize_mode = "crop"

    # 预设尺寸
    if width == 0 and height == 0:
        # 未指定尺寸不做调整
        pass

    # === 参数范围校验（非法参数返回明确业务错误，而非裸 500） ===
    if not (0 <= beautify_level <= 3):
        raise HTTPException(status_code=400, detail=f"beautify_level 必须在 0-3 之间，当前: {beautify_level}")
    if width != 0 and not (1 <= width <= 8000):
        raise HTTPException(status_code=400, detail=f"width 必须在 1-8000 之间，当前: {width}")
    if height != 0 and not (1 <= height <= 8000):
        raise HTTPException(status_code=400, detail=f"height 必须在 1-8000 之间，当前: {height}")
    if not (72 <= dpi <= 1200):
        raise HTTPException(status_code=400, detail=f"dpi 必须在 72-1200 之间，当前: {dpi}")
    if min_kb < 0 or max_kb < 0:
        raise HTTPException(status_code=400, detail="min_kb / max_kb 不能为负数")
    if min_kb > 0 and max_kb > 0 and min_kb > max_kb:
        raise HTTPException(status_code=400, detail=f"min_kb({min_kb}) 不能大于 max_kb({max_kb})")
    if resize_mode not in ("crop", "exact", "fit", "fill", "by_width", "by_height"):
        raise HTTPException(status_code=400, detail=f"resize_mode 非法: {resize_mode}")

    # 计费判断
    try:
        billing_result = await process_with_billing(db, current_user.id)
    except NeedPaymentException as e:
        return JSONResponse(
            status_code=402,
            content={
                "code": ErrorCode.PAYMENT_NEEDED,
                "message": "无可用免费次数，请先支付",
                "data": {
                    "order_id": e.order_id,
                    "order_no": e.order_no,
                    "amount": e.amount,
                    "need_pay": True,
                },
            },
        )

    # 校验图片
    try:
        image, fmt = validate_image(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    original_size = len(file_bytes)

    # 处理参数
    params = ProcessParams(
        width=width,
        height=height,
        resize_mode=resize_mode,
        upscale=upscale,
        dpi=dpi,
        min_kb=min_kb,
        max_kb=max_kb,
        bg_color=bg_color,
        output_format=output_format,
        beautify_level=beautify_level,
        beautify_smooth=beautify_smooth,
        beautify_brighten=beautify_brighten,
        beautify_blemish=beautify_blemish,
        id_photo_align=id_photo_align,
        gender=gender if gender and gender != "None" else None,
    )

    # 执行流水线
    try:
        result: ProcessResult = process_image(image, params)
    except Exception as e:
        logger.exception(f"Image processing failed: {e}")
        record = ProcessRecord(
            user_id=current_user.id,
            template_id=template_id,
            request_params=json.dumps(params.__dict__, default=str),
            is_paid=not billing_result["free_used"],
            original_size=original_size,
            result_size=0,
            processing_time_ms=0,
            status="failed",
            error_message=str(e),
        )
        db.add(record)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"图像处理失败: {e}")

    # 保存结果文件
    ext = output_format.lower()
    if ext == "jpeg":
        ext = "jpg"
    temp_filename = generate_temp_filename(ext)
    temp_path = os.path.join(settings.TEMP_FILE_DIR, temp_filename)
    os.makedirs(settings.TEMP_FILE_DIR, exist_ok=True)
    with open(temp_path, "wb") as f:
        f.write(result.data)

    # 写入处理记录
    record = ProcessRecord(
        user_id=current_user.id,
        template_id=template_id,
        request_params=json.dumps(params.__dict__, default=str),
        is_paid=not billing_result["free_used"],
        paid_amount=0,
        original_size=original_size,
        result_size=len(result.data),
        result_pixels=result.result_pixels,
        result_dpi=result.result_dpi,
        bg_color=bg_color,
        processing_time_ms=result.processing_time_ms,
        status="success",
        thumb_path=temp_path,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return ResponseModel(
        code=200,
        message="处理成功",
        data={
            "record_id": record.id,
            "result_url": f"/api/v1/process/{record.id}/preview",
            "download_url": f"/api/v1/process/{record.id}/download",
            "file_size_kb": round(result.result_size_kb, 1),
            "pixels": result.result_pixels,
            "dpi": result.result_dpi,
            "output_format": result.output_format,
            "mime_type": result.mime_type,
            "warnings": result.warnings,
            "faces_detected": result.faces_detected,
            "processing_time_ms": result.processing_time_ms,
            "free_used": billing_result["free_used"],
            # 无限次数时业务层会返回 -1（前端显示为 ∞）；.get 兜底避免 KeyError 500
            "remaining_free_count": billing_result.get("remaining_free_count", current_user.free_count),
            "unlimited": billing_result.get("unlimited", False),
        },
    )


@router.get("/presets", response_model=ResponseModel)
async def get_size_presets(
    current_user=Depends(get_current_user),
):
    """获取所有尺寸预设（网站/平台图片尺寸 + 证件照尺寸）."""
    return ResponseModel(
        code=200,
        data={
            "presets": [
                {"name": name, "width": w, "height": h}
                for name, (w, h) in SIZE_PRESETS.items()
            ],
            "count": len(SIZE_PRESETS),
        },
    )


@router.get("/formats", response_model=ResponseModel)
async def get_output_formats(
    current_user=Depends(get_current_user),
):
    """获取支持的输出格式列表."""
    return ResponseModel(
        code=200,
        data={
            "formats": [
                {"format": k, "ext": v["ext"], "mime": v["mime"], "description": v["description"]}
                for k, v in SUPPORTED_OUTPUT_FORMATS.items()
            ],
        },
    )


@router.get("/bg-colors", response_model=ResponseModel)
async def get_bg_colors(
    current_user=Depends(get_current_user),
):
    """获取支持的背景色列表."""
    from app.core.image_engine.background import BG_COLOR_MAP
    return ResponseModel(
        code=200,
        data={
            "colors": [
                {"name": name, "rgb": list(rgb)}
                for name, rgb in BG_COLOR_MAP.items()
            ],
        },
    )


@router.get("/{record_id}/preview")
async def preview_result(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取处理结果预览."""
    result = await db.execute(
        select(ProcessRecord).where(
            ProcessRecord.id == record_id,
            ProcessRecord.user_id == current_user.id,
        )
    )
    record = result.scalar()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    if record.status != "success" or not record.thumb_path:
        raise HTTPException(status_code=404, detail="No preview available.")
    if not os.path.exists(record.thumb_path):
        raise HTTPException(status_code=404, detail="Preview file expired.")

    # 根据保存的格式确定 MIME 类型
    mime_type = "image/jpeg"
    if record.thumb_path.endswith(".png"):
        mime_type = "image/png"
    elif record.thumb_path.endswith(".pdf"):
        mime_type = "application/pdf"
    elif record.thumb_path.endswith(".webp"):
        mime_type = "image/webp"

    return FileResponse(record.thumb_path, media_type=mime_type)


@router.get("/{record_id}/download")
async def download_result(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载处理结果."""
    result = await db.execute(
        select(ProcessRecord).where(
            ProcessRecord.id == record_id,
            ProcessRecord.user_id == current_user.id,
        )
    )
    record = result.scalar()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    if record.status != "success" or not record.thumb_path:
        raise HTTPException(status_code=404, detail="File not found.")
    if not os.path.exists(record.thumb_path):
        raise HTTPException(status_code=404, detail="File expired.")

    # 确定扩展名与 MIME 类型（与 preview 一致，按保存格式动态设置）
    _, ext = os.path.splitext(record.thumb_path)
    filename = f"photo_{record.result_pixels}{ext if ext else '.jpg'}"

    mime_type = "image/jpeg"
    if record.thumb_path.endswith(".png"):
        mime_type = "image/png"
    elif record.thumb_path.endswith(".pdf"):
        mime_type = "application/pdf"
    elif record.thumb_path.endswith(".webp"):
        mime_type = "image/webp"
    elif record.thumb_path.endswith(".bmp"):
        mime_type = "image/bmp"
    elif record.thumb_path.endswith(".tiff"):
        mime_type = "image/tiff"

    return FileResponse(
        record.thumb_path,
        media_type=mime_type,
        filename=filename,
    )


@router.get("/history", response_model=ResponseModel)
async def get_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户最近 10 条处理历史."""
    from sqlalchemy.orm import joinedload

    result = await db.execute(
        select(ProcessRecord)
        .options(joinedload(ProcessRecord.template))
        .where(ProcessRecord.user_id == current_user.id)
        .order_by(desc(ProcessRecord.created_at))
        .limit(10)
    )
    records = result.unique().scalars().all()

    items = []
    for r in records:
        items.append({
            "id": r.id,
            "template_id": r.template_id,
            "template_name": r.template.name if r.template else None,
            "original_size": r.original_size,
            "result_size": r.result_size,
            "result_pixels": r.result_pixels,
            "bg_color": r.bg_color,
            "status": r.status,
            "processing_time_ms": r.processing_time_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "has_preview": r.status == "success" and bool(r.thumb_path),
            "result_url": f"/api/v1/process/{r.id}/preview" if r.status == "success" and r.thumb_path else None,
            "thumb_url": f"/api/v1/process/{r.id}/preview" if r.status == "success" and r.thumb_path else None,
        })

    return ResponseModel(
        code=200,
        data={"items": items, "count": len(items)},
    )