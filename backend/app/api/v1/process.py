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
    # 空串 = 调用方没有显式指定背景色，由模板默认值 / 白色兜底决定。
    # 【为什么默认值不能直接写 "white"】以前默认 "white"，代码就无法区分
    # 「用户主动选了白底」和「用户根本没传这个字段」，于是用
    # `bg_color = colors[0] if bg_color == "white" else bg_color` 猜意图 ——
    # 结果用户明确点「白底」时被模板的 colors[0] 覆盖掉：
    #   教师招聘2寸 allowed=[blue, white] → 选白底却出蓝底
    #   征兵照片   allowed=[red, white, blue] → 选白底却出红底
    # 这正是「选白色背景却不能正确换白色背景」的直接原因。
    bg_color: str = Form(""),
    output_format: str = Form("JPEG"),
    # === 美颜参数（证件照只允许「自然微调」）===
    beautify_level: int = Form(BeautyLevel.COMPLIANT),
    beautify_smooth: bool = Form(True),
    beautify_brighten: bool = Form(True),
    beautify_blemish: bool = Form(True),
    beautify_adaptive: bool = Form(True),
    # 分区域处理系统的三个开关（默认全开；关掉即回到「整脸滤镜」的旧行为）
    beautify_tone: bool = Form(True),
    beautify_texture: bool = Form(True),
    beautify_feature: bool = Form(True),
    retouch_strength: float = Form(1.0),
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
      （1/2/3 均会被证件照红线钳制：磨皮≤12%、保留≥90%原生纹理、不改骨相）；
      retouch_strength 还原度滑杆 0~1（默认 1，前端默认 0.7 轻修）
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

    # 背景色归一化：空串 / "default" / "auto" / 缺省 → 交给下面的模板或白色兜底决定
    requested_bg = (bg_color or "").strip()
    if requested_bg.lower() in ("", "default", "auto"):
        requested_bg = ""

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
        # custom:#RRGGBB 是用户自由取色（简历/工牌照等模板外场景），放行；
        # 真正的非法色名仍拒绝。bg_color 的最终解析在 background.parse_bg_color。
        is_custom = requested_bg.startswith("custom:")
        # 【为什么按「色调分组」而不是字面色名放行】
        # 模板里写的是组级意图（"blue" = 蓝底），而色板里给了 5 种蓝供用户
        # 按报考公告挑 HEX（#438EDB / #0066CC / #1D8DD5 / #00BFF3 / #64C5FF）。
        # 若只认字面量，用户点「考公深蓝」会被 400 拒绝 —— 明明模板要的就是蓝底。
        # 所以同一分组视为合法；跨分组（模板要白底却提交深蓝）仍然拒绝。
        if requested_bg and not is_custom and requested_bg not in colors:
            from app.core.image_engine.background import same_color_family
            if not any(same_color_family(requested_bg, c) for c in colors):
                raise HTTPException(
                    status_code=400,
                    detail=f"模板 {tmpl.name} 不支持背景色 '{requested_bg}'，仅支持: {', '.join(colors)}",
                )
        # 【关键修复】只有调用方**没有指定**背景色时才用模板默认值（colors[0]）。
        # 以前写的是 `colors[0] if bg_color == "white" else bg_color`，把「用户明确
        # 选了白底」也当成「没指定」，于是白底被换成蓝底/红底 —— 用户点白底，
        # 出来的永远是模板首色，这就是「选白色背景不能正确换白」的直接原因。
        bg_color = requested_bg or colors[0]
        output_format = tmpl.output_format
        resize_mode = "crop"
    else:
        # 非模板模式：没有显式指定就用白底（保持旧版默认语义）。
        # 这里必须兜底，否则空串会一路传到 parse_bg_color，被当成未知色名
        # 再降级成 white —— 虽然结果一样，但日志里会多一条误导性的 warning。
        bg_color = requested_bg or "white"

    # 预设尺寸
    if width == 0 and height == 0:
        # 未指定尺寸不做调整
        pass

    # === 参数范围校验（非法参数返回明确业务错误，而非裸 500） ===
    if not (0 <= beautify_level <= 5):
        raise HTTPException(status_code=400,
                            detail=f"beautify_level 必须在 0-5 之间（4=艺术档，5=影楼精修；"
                                   f"4/5 不受证件照合规红线约束，仅非报名场景使用），"
                                   f"当前: {beautify_level}")
    if not (0.0 <= retouch_strength <= 1.0):
        raise HTTPException(status_code=400,
                            detail=f"retouch_strength 必须在 0-1 之间（还原度滑杆），当前: {retouch_strength}")
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
        beautify_adaptive=beautify_adaptive,
        beautify_tone=beautify_tone,
        beautify_texture=beautify_texture,
        beautify_feature=beautify_feature,
        retouch_strength=retouch_strength,
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
            # 提交阶段还没有基线；轮询到 success 后由 /status 带回
            "baseline_url": None,
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
    meta = _result_meta(record)

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
            # 对比基线：与效果图同几何的原图渲染；没有时为 None，前端回退到上传原图
            "baseline_url": (
                f"/api/v1/process/{record.id}/baseline"
                if meta.get("baseline_path") and os.path.exists(meta["baseline_path"])
                else None
            ),
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
    """获取支持的背景色列表（按底色分组 + 标准色号标注）.

    【为什么带 group/label/hex/usage】
    证件照报名系统的背景审核是按像素 RGB 卡阈值的，用户需要知道
    「我选的这个蓝到底是 #438EDB 还是 #0066CC」，光看色块分不出来。
    所以每个色都下发：分组（白/蓝/红/灰）、中文名、标准 HEX 色号、
    适用场景说明（usage）。

    【为什么同时下发 standard / all】
    * standard：有明确使用场景的标准色号（前端默认铺这批，见 STANDARD_BG_COLORS）；
    * all     ：全量色板（含历史键与扩展色，供历史记录回显 / 需要时展开）。
    两个列表都按「色值去重」后给出，避免用户看到两个一模一样的色块。

    【为什么按色值去重】
    新色板里 blue_std 与历史键 blue 是同一个 #438EDB，white_std 与 white
    同理。两者都要保留（老订单靠历史键复现），但色板上只应出现一次，
    否则用户会看到两个一模一样的蓝块。
    """
    from app.core.image_engine.background import (
        BG_COLOR_MAP, BG_COLOR_META, BG_COLOR_GROUP_ORDER, STANDARD_BG_COLORS,
    )

    seen = {}          # rgb -> 保留的条目
    for name, rgb in BG_COLOR_MAP.items():
        meta = BG_COLOR_META.get(name, {})
        group = meta.get("group", "其他")
        label = meta.get("label", name)
        hexval = meta.get("hex", "")
        usage = meta.get("usage", "")

        # keep / custom 是特殊项，不做色值去重（rgb 是占位值 -1）
        if name in ("keep", "custom"):
            seen[name] = {"name": name, "rgb": list(rgb), "group": group,
                          "label": label, "hex": hexval, "usage": usage}
            continue

        key = tuple(rgb)
        if key in seen:
            continue   # 同色值只保留第一个（历史键在前，保证老记录回显一致）
        seen[name] = {"name": name, "rgb": list(rgb), "group": group,
                      "label": label, "hex": hexval, "usage": usage}

    # 标准色板：按 STANDARD_BG_COLORS 的业务顺序取，过滤掉被去重掉的别名，
    # 首位固定「保留原背景」，末位固定「自定义」（前端据此渲染首尾两项）。
    standard = []
    for name in ["keep"] + STANDARD_BG_COLORS + ["custom"]:
        item = seen.get(name)
        if item and item["name"] not in {c["name"] for c in standard}:
            standard.append(item)

    colors = list(seen.values())
    # 按分组顺序稳定排序，组内保持 BG_COLOR_MAP 的定义顺序
    order = {g: i for i, g in enumerate(BG_COLOR_GROUP_ORDER)}
    colors.sort(key=lambda c: order.get(c["group"], 99))

    return ResponseModel(
        code=200,
        data={
            "colors": colors,
            "standard": standard,
            "groups": BG_COLOR_GROUP_ORDER,
        },
    )


def _result_meta(record: ProcessRecord) -> dict:
    """从记录里取 _result 快照（结果元信息：warnings/格式/基线路径等）."""
    raw = record.request_params if isinstance(record.request_params, dict) else {}
    meta = raw.get("_result")
    return meta if isinstance(meta, dict) else {}


@router.get("/{record_id}/baseline")
async def baseline_result(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取「对比基线」：与效果图**同裁剪/同缩放/同像素**的原图.

    【为什么需要这个接口】前端对比「原图 / 效果图」时，如果左边放的是用户
    上传的原始照片、右边放的是「裁剪 + 对齐 + 缩放」后的结果，两张图的缩放
    比例与中心点天然不同 —— 用户看到的第一眼永远是「右边脸怎么这么大」，
    根本没法判断美颜效果。这个接口返回的是后端用**同一条几何链路**重新渲染
    的原图（只关掉美颜与换底），所以两张图逐像素对齐，差异只剩美颜本身。

    没有基线（没开美颜 / 基线渲染失败 / 老记录）时返回 404，前端自动回退到
    上传原图做对比（并提示几何可能不一致）。
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
    if record.status != "success":
        raise HTTPException(status_code=409, detail="照片还在处理中，请稍候再试")

    path = _result_meta(record).get("baseline_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Baseline not available.")

    mime_type = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    return FileResponse(path, media_type=mime_type)


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