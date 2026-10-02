"""图像处理流水线编排：crop → beautify → resize → dpi → background → compress."""

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from PIL import Image
from loguru import logger

from app.core.image_engine.crop import resize_crop
from app.core.image_engine.beautify import apply_beautify, BeautyLevel
from app.core.image_engine.resize import resize_image, ResizeMode
from app.core.image_engine.face_detect import (
    align_face, detect_faces, FaceInfo, get_id_photo_params,
    ID_PHOTO_STANDARDS, MIN_OUTPUT_DPI,
)
from app.core.image_engine.dpi import adjust_dpi
from app.core.image_engine.background import replace_background
from app.core.image_engine.compressor import binary_compress
from app.core.image_engine.format_converter import convert_format


@dataclass
class ProcessParams:
    """图像处理参数（扩展版）."""

    # === 尺寸 ===
    width: int = 0
    height: int = 0
    resize_mode: str = "crop"  # crop | exact | fit | fill | by_width | by_height
    upscale: bool = True       # 是否允许放大

    # === 证件照 ===
    dpi: int = MIN_OUTPUT_DPI  # 350 → 300：PRD 四「输出不得低于 300dpi」
    bg_color: str = "white"
    output_format: str = "JPEG"

    # === 美颜（证件照只允许「自然微调」COMPLIANT 档）===
    beautify_level: int = BeautyLevel.COMPLIANT  # 0=OFF, 1=自然微调, 2/3=内部测试
    beautify_smooth: bool = True
    beautify_brighten: bool = True
    beautify_blemish: bool = True

    # === 证件照对齐 ===
    id_photo_align: bool = False   # 是否启用证件照人脸对齐
    gender: Optional[str] = None   # male / female / None

    # === 压缩 ===
    min_kb: int = 0
    max_kb: int = 100


@dataclass
class ProcessResult:
    """图像处理结果（扩展版）."""
    data: bytes
    result_size_kb: float
    result_pixels: str
    result_dpi: int
    actual_quality: int = 100
    output_format: str = "JPEG"
    mime_type: str = "image/jpeg"
    warnings: list = field(default_factory=list)
    faces_detected: int = 0
    processing_time_ms: int = 0


# 全部标准证件照像素集合（用于合规判定）
_STANDARD_PX = {tuple(v["px"]) for v in ID_PHOTO_STANDARDS.values()}
# 面积最小的标准规格（小一寸 260×378）；低于它就一定达不到打印标准
_MIN_STD_AREA = min(w * h for w, h in _STANDARD_PX)


def _quality_warnings(
    width: int, height: int, dpi: int, is_id_photo: bool,
) -> list:
    """按 PRD 四/五 生成出图质量提示.

    只提示、不阻断出图。这些文案最终会透传到前端「下载页」，
    对应 PRD 五「在下载页明确提示分辨率可能影响打印效果」。
    """
    warns = []

    if dpi < MIN_OUTPUT_DPI:
        warns.append(
            f"当前输出 DPI={dpi}，低于印刷标准 {MIN_OUTPUT_DPI}dpi，可能影响打印效果"
        )

    if not width or not height:
        warns.append(
            "未指定证件照标准尺寸，已按原图尺寸输出；"
            "如需打印请选择「一寸 295×413」或「二寸 413×579」等标准规格"
        )
    else:
        if is_id_photo and (width, height) not in _STANDARD_PX:
            warns.append(
                f"当前尺寸 {width}×{height}px 非标准证件照像素，"
                f"标准值：一寸 295×413 / 二寸 413×579 / 小一寸 260×378 / 大一寸 390×567"
            )
        # 比最小的标准规格还小 → 一定达不到高清打印标准
        if width * height < _MIN_STD_AREA:
            warns.append(
                f"当前生成分辨率较低（{width}×{height}px），"
                f"建议使用高清原图以保证打印质量"
            )

    return warns


def process_image(
    image: Image.Image, params: ProcessParams,
) -> ProcessResult:
    """
    图像处理流水线（增强版）:

    Step 1: 人脸检测（获取人脸信息，用于后续步骤）
    Step 2: beautify — 美颜（磨皮+提亮+去瑕疵）
    Step 3: crop/align/resize — 裁剪/证件照对齐/尺寸调整
    Step 4: dpi — 设置 DPI
    Step 5: background — AI 抠图换底
    Step 6: compress — 压缩到目标大小
    Step 7: format — 格式转换（JPEG/PNG/PDF 等）

    返回 ProcessResult
    """
    start_time = time.time()
    warnings = []
    faces_count = 0

    # Step 1: 人脸检测
    try:
        faces = detect_faces(image)
        faces_count = len(faces)
        logger.debug(f"Detected {faces_count} face(s)")
    except Exception as e:
        logger.warning(f"Face detection failed: {e}")
        faces = []

    # Step 2: 美颜处理
    if params.beautify_level > BeautyLevel.OFF:
        try:
            image = apply_beautify(
                image,
                level=params.beautify_level,
                smooth=params.beautify_smooth,
                brighten=params.beautify_brighten,
                blemish=params.beautify_blemish,
            )
        except Exception as e:
            warnings.append(f"Beautify failed: {e}")
            logger.warning(f"Beautify failed, continuing: {e}")

    # Step 3: 裁剪/对齐/尺寸调整
    if params.width > 0 and params.height > 0:
        if params.id_photo_align and faces_count > 0:
            # 证件照人脸对齐模式
            try:
                align_kwargs = {}
                if params.gender:
                    idp = get_id_photo_params(params.gender)
                    align_kwargs["top_margin_ratio"] = idp.get("top_margin", 0.25)
                    warnings.append(
                        f"证件照推荐服装({params.gender}): {idp.get('clothing', '深色有领上衣')}"
                    )
                image = align_face(image, params.width, params.height, **align_kwargs)
            except Exception as e:
                warnings.append(f"Face alignment failed: {e}")
                # 回退到普通裁剪
                image = resize_crop(image, params.width, params.height)
        elif params.resize_mode == "crop":
            # 原有裁剪模式（人脸检测辅助）
            image = resize_crop(image, params.width, params.height)
        else:
            # 纯缩放模式（不裁剪，保持比例）
            mode_map = {
                "exact": ResizeMode.EXACT,
                "fit": ResizeMode.FIT,
                "fill": ResizeMode.FILL,
                "by_width": ResizeMode.BY_WIDTH,
                "by_height": ResizeMode.BY_HEIGHT,
            }
            mode = mode_map.get(params.resize_mode, ResizeMode.EXACT)
            image = resize_image(image, params.width, params.height, mode=mode, upscale=params.upscale)
    else:
        warnings.append("Skipped resize: target width/height is 0")

    # Step 4: DPI 设置
    image = adjust_dpi(image, params.dpi)

    # Step 5: 背景替换（AI 抠图失败时降级保留原图，避免整单裸 500）
    if params.bg_color and params.bg_color != "keep":
        try:
            image = replace_background(image, params.bg_color)
        except Exception as e:
            warnings.append(f"AI抠图失败(2004)，保留原背景: {e}")
            logger.warning(f"Background replacement failed, keeping original: {e}")

    # Step 6/7: 压缩 + 格式转换
    output_format = params.output_format.upper()
    if output_format == "JPEG":
        # JPEG：二分压缩控制目标大小
        result_bytes, quality, comp_warning = binary_compress(
            image, params.min_kb, params.max_kb, fmt="JPEG",
        )
        if comp_warning:
            warnings.append(comp_warning)
        mime_type = "image/jpeg"
    else:
        # 非 JPEG：直接做一次格式转换，避免先压缩再转换的重复编码
        try:
            result_bytes, mime_type = convert_format(image, output_format, quality=95)
        except Exception as e:
            warnings.append(f"Format conversion to {output_format} failed: {e}")
            # 转换失败回退 JPEG 压缩
            result_bytes, quality, comp_warning = binary_compress(
                image, params.min_kb, params.max_kb, fmt="JPEG",
            )
            if comp_warning:
                warnings.append(comp_warning)
            output_format = "JPEG"
            mime_type = "image/jpeg"
        else:
            quality = 100

    # 出图质量提示（PRD 四/五）：不阻断出图，随结果透传到前端下载页
    warnings.extend(
        _quality_warnings(
            width=params.width,
            height=params.height,
            dpi=params.dpi,
            is_id_photo=bool(params.id_photo_align),
        )
    )

    elapsed_ms = int((time.time() - start_time) * 1000)

    result = ProcessResult(
        data=result_bytes,
        result_size_kb=len(result_bytes) / 1024,
        result_pixels=f"{image.width}x{image.height}",
        result_dpi=params.dpi,
        actual_quality=quality,
        output_format=output_format,
        mime_type=mime_type,
        warnings=warnings,
        faces_detected=faces_count,
        processing_time_ms=elapsed_ms,
    )

    logger.info(
        f"Pipeline done: {result.result_pixels}@{result.result_dpi}DPI "
        f"→ {result.result_size_kb:.1f}KB {result.output_format} q={quality} "
        f"beauty={params.beautify_level} faces={faces_count} "
        f"in {elapsed_ms}ms"
    )
    return result