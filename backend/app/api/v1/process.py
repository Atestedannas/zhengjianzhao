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
from app.core.image_engine.pipeline import ProcessParams
from app.core.image_engine.validator import validate_image
from app.core.image_engine.beautify import BeautyLevel
from app.core.image_engine.resize import SIZE_PRESETS, get_preset
from app.core.image_engine.format_converter import SUPPORTED_OUTPUT_FORMATS
from app.core.billing import process_with_billing, refund_free_count
from app.models.user import User
from app.models.template import SpecTemplate
from app.models.process_record import ProcessRecord
from app.schemas.process import ProcessHistoryItem
from app.schemas.common import ResponseModel
from app.utils.error_codes import ErrorCode, NeedPaymentException
from app.utils.file_utils import generate_unique_filename as generate_temp_filename

router = APIRouter()


def _save_pending_original(file_bytes: bytes, ext: str) -> str:
    """把上传的原图暂存到 web 与 celery worker 共享的目录.

    必须落在 PROCESS_PENDING_DIR（TEMP_FILE_DIR 的子目录）：
    cleanup_expired_files 用的是非递归 glob（只删 temp 根目录下的图片），
    所以子目录里的原图不会在排队期间被定时任务删掉。
    """
    os.makedirs(settings.PROCESS_PENDING_DIR, exist_ok=True)
    path = os.path.join(settings.PROCESS_PENDING_DIR, generate_temp_filename(ext or "jpg"))
    with open(path, "wb") as f:
        f.write(file_bytes)
    return path


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

    流程（异步，重活不在 web worker 里跑）:
    1. 读取上传文件 → 校验
    2. 模板参数覆盖（如指定 template_id）
    3. 计费判断（扣费）
    4. 原图暂存到共享目录 + 建 status=pending 的处理记录
    5. 投递 Celery 任务 → **立即返回 record_id**
    6. 前端轮询 GET /api/v1/process/{record_id}/status

    为什么不同步处理：图像流水线要加载 100MB~3GB 的 ONNX 模型，
    放在 web worker 里会（a）阻塞事件循环数十秒，（b）把只有几 GB 内存的
    机器直接拖进内核 OOM（worker 被 SIGKILL → nginx 502）。
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

    # === 原图暂存到共享目录（celery worker 要能读到）===
    upload_ext = os.path.splitext(file.filename or "")[1].lstrip(".").lower()
    pending_ext = upload_ext if upload_ext.isalnum() and len(upload_ext) <= 5 else (fmt or "jpg").lower()
    if pending_ext == "jpeg":
        pending_ext = "jpg"
    try:
        original_path = _save_pending_original(file_bytes, pending_ext)
    except OSError as e:
        logger.exception(f"暂存上传文件失败: {e}")
        # 扣费已经发生，落盘失败要把次数退回去
        if billing_result.get("free_used"):
            await refund_free_count(db, current_user.id)
            await db.commit()
        raise HTTPException(status_code=500, detail="服务器暂存文件失败，本次未扣次数，请重试")

    # === 建 pending 记录：真正出图交给 celery worker ===
    record = ProcessRecord(
        user_id=current_user.id,
        template_id=template_id,
        request_params=json.dumps(params.__dict__, default=str),
        is_paid=not billing_result["free_used"],
        paid_amount=0,
        original_size=original_size,
        result_size=0,
        processing_time_ms=0,
        bg_color=bg_color,
        status="pending",
        original_path=original_path,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # === 投递任务（队列不可用时退次数并明确报错，而不是静默失败）===
    try:
        from app.tasks.process_task import process_photo as process_photo_task

        process_photo_task.delay(record.id)
    except Exception as e:  # noqa: BLE001 —— broker 不可用 / 序列化异常等
        logger.exception(f"投递处理任务失败: {e}")
        record.status = "failed"
        record.error_message = "任务投递失败（处理队列不可用）"[:500]
        await db.commit()
        if billing_result.get("free_used"):
            await refund_free_count(db, current_user.id)
            await db.commit()
        raise HTTPException(status_code=503, detail="处理队列暂时不可用，本次未扣次数，请稍后重试")

    logger.info(f"ProcessRecord {record.id} 已入队（user={current_user.id}, template={template_id}）")

    return ResponseModel(
        code=200,
        message="已提交处理",
        data={
            "record_id": record.id,
            "status": "pending",
            "status_url": f"/api/v1/process/{record.id}/status",
            "result_url": f"/api/v1/process/{record.id}/preview",
            "download_url": f"/api/v1/process/{record.id}/download",
            "free_used": billing_result["free_used"],
            # 无限次数时业务层会返回 -1（前端显示为 ∞）；.get 兜底避免 KeyError 500
            "remaining_free_count": billing_result.get("remaining_free_count", current_user.free_count),
            "unlimited": billing_result.get("unlimited", False),
        },
    )


@router.get("/{record_id}/status", response_model=ResponseModel)
async def get_process_status(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询处理任务状态（前端轮询用）.

    status=success 时返回的字段与旧版 POST /process/ 成功返回**完全同名**，
    前端拿到 success 后可以直接复用原来的成功处理逻辑。
    status=failed 时 error 是失败原因；后端已自动退还扣掉的免费次数，
    remaining_free_count 就是退还后的值。
    """
    result = await db.execute(
        select(ProcessRecord).where(
            ProcessRecord.id == record_id,
            ProcessRecord.user_id == current_user.id,
        )
    )
    record = result.scalar()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found.")

    raw_params = record.request_params if isinstance(record.request_params, dict) else {}
    meta = raw_params.get("_result") if isinstance(raw_params.get("_result"), dict) else {}

    # 输出格式以实际落盘文件为准（格式转换可能回退）
    _, ext = os.path.splitext(record.thumb_path or "")
    ext = ext.lower()
    output_format = meta.get("output_format") or (raw_params.get("output_format") or "JPEG")
    mime_type = meta.get("mime_type") or {
        ".png": "image/png", ".pdf": "application/pdf", ".webp": "image/webp",
        ".bmp": "image/bmp", ".tiff": "image/tiff", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    }.get(ext, "image/jpeg")

    # 剩余次数与记录读取在同一事务快照里，保证「看到 failed」时也一定看得到已退还的次数
    remaining = (
        await db.execute(select(User.free_count).where(User.id == current_user.id))
    ).scalar()
    if remaining is None:
        remaining = current_user.free_count

    return ResponseModel(
        code=200,
        message="ok",
        data={
            "record_id": record.id,
            "status": record.status,
            "error": record.error_message if record.status == "failed" else None,
            "result_url": f"/api/v1/process/{record.id}/preview",
            "download_url": f"/api/v1/process/{record.id}/download",
            "file_size_kb": round((record.result_size or 0) / 1024, 1),
            "pixels": record.result_pixels,
            "dpi": record.result_dpi,
            "output_format": output_format,
            "mime_type": mime_type,
            "warnings": meta.get("warnings") or [],
            "faces_detected": meta.get("faces_detected") or 0,
            "processing_time_ms": record.processing_time_ms or 0,
            "remaining_free_count": remaining,
            "unlimited": remaining == -1,
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
    if record.status in ("pending", "processing"):
        raise HTTPException(status_code=409, detail="照片还在处理中，请稍候再试")
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
    if record.status in ("pending", "processing"):
        raise HTTPException(status_code=409, detail="照片还在处理中，请稍候再试")
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