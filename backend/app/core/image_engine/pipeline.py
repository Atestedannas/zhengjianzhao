"""图像处理流水线编排：crop → beautify → upscale sharpen → background → output sharpen → dpi → compress."""

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from PIL import Image, ImageFilter
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

    # === 美颜（证件照只允许「自然微调」COMPLIANT 档；艺术档仅非证件流程）===
    beautify_level: int = BeautyLevel.COMPLIANT  # 0=OFF, 1=自然微调, 2/3=内部测试, 4=艺术档(非证件)
    beautify_smooth: bool = True
    beautify_brighten: bool = True
    beautify_blemish: bool = True
    beautify_adaptive: bool = True   # ⑤ 按肤质/曝光/清晰度/人脸占比自适应缩放强度

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


def _upscale_sharpen(image: Image.Image, target_w: int, target_h: int,
                     pre_resize_size: Optional[Tuple[int, int]] = None) -> Image.Image:
    """放大补偿锐化：只在图像被放大时补一点微观对比度.

    【背景】用户原图常常小于证件照规格（如 231x268 → 295x413，放大 1.3~1.5 倍）。
    LANCZOS 放大后会"发软"，毛孔/细纹的明暗过渡被插值平滑，肉眼看起来
    就是"塑料感、假面、没有颜肉质感"。这不是美颜磨皮造成的（实测美颜对
    高频能量的损失只有 2~3%），而是放大插值本身造成的。

    【做法】按放大倍数给一层 UnsharpMask：1.0~1.5 倍轻度、1.5~2.0 倍加强，
            ≥2 倍封顶（再锐化也是无米之炊，反而出振铃）。缩小（<1.0）不锐化
            （缩小路径的锐度补偿由 _output_sharpen 在换底之后统一做）。
    【实测】真实用户原图 A/B：放大 1.3x 时高频能量 +32%、局部对比度 +8%；
            等尺寸时高频 +16%、局部对比度 +10%。观感从"假面"回到"肌肤"。
    【threshold=0】阈值会把 ≤N 级的微观对比直接跳过（旧值 2 正好卡掉毛孔级
            起伏），是「放大后塑料感」的来源之一；皮肤锐化阈值必须为 0。
    """
    # 没有 resize 前的尺寸就无法判断是否放大，安全起见不锐化
    if not pre_resize_size:
        return image
    base_w, base_h = pre_resize_size
    zoom = max(target_w / base_w, target_h / base_h)
    if zoom <= 1.02:
        return image

    # 幂函数：1.3x → 约 59%，1.5x → 约 72%，2x → 封顶 95%
    amount = min(0.95, 0.70 * (zoom - 1.0) ** 0.6 + 0.25)
    radius = 1.2 if zoom < 1.5 else 1.8
    out = image.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 100), threshold=0))
    w, h = image.size
    logger.info(f"Upscale sharpen: {base_w}x{base_h} -> {w}x{h} (zoom {zoom:.2f}) amount={amount:.2f}")
    return out


def _output_sharpen(image: Image.Image, target_w: int, target_h: int,
                    pre_resize_size: Optional[Tuple[int, int]] = None) -> Image.Image:
    """输出锐化：缩小/换底之后补偿锐度（对应 PS 修图流程的最后一步「输出锐化」）.

    【背景】_upscale_sharpen 只覆盖放大路径（zoom>1.02）；而证件照主路径是
    高清原图缩到 295×413 这类小规格 —— LANCZOS 缩图 + 抠图换底的发丝羽化
    都会让画面系统性偏软，这是「美颜后发糊」的最大单一来源。PS 流程里
    「输出锐化」永远是最后一步，这里在换底之后、编码之前补上同一环节。

    【做法】只锐化 LAB 的 L 通道（避免饱和边缘产生色边），UnsharpMask 半径
            随输出尺寸取 0.7~1.0px、幅度 60%、threshold=0（不跳过毛孔级
            微观对比）。仅当确实发生了缩小（zoom<0.98）才执行：
              * zoom>1.02 → 放大路径，已由 _upscale_sharpen 处理（互斥不叠加）；
              * zoom≈1   → 没有发生插值，没有软化，不锐化。
    """
    if not pre_resize_size:
        return image
    base_w, base_h = pre_resize_size
    zoom = max(target_w / base_w, target_h / base_h)
    if zoom > 1.02 or zoom >= 0.98:
        return image

    import cv2
    import numpy as np

    w, h = image.size
    bgr = np.ascontiguousarray(np.array(image.convert("RGB"))[:, :, ::-1])
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan = lab[:, :, 0].astype(np.float32)
    radius = 0.7 if min(w, h) <= 600 else 1.0
    blur = cv2.GaussianBlur(l_chan, (0, 0), radius)
    lab[:, :, 0] = np.clip(l_chan + (l_chan - blur) * 0.60, 0.0, 255.0).astype(np.uint8)
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    logger.info(f"Output sharpen: zoom={zoom:.2f} radius={radius} "
                f"(downscale/bg-soften compensation)")
    return Image.fromarray(np.ascontiguousarray(out[:, :, ::-1]))


def process_image(
    image: Image.Image, params: ProcessParams,
) -> ProcessResult:
    """
    图像处理流水线（增强版）:

    Step 1: 人脸检测（获取人脸信息，用于后续步骤）
    Step 2: crop/align/resize — 裁剪/证件照对齐/尺寸调整
    Step 3: beautify — 美颜（在目标分辨率上执行，半径与输出匹配）
    Step 3.5: 放大补偿锐化（仅放大时）
    Step 4: dpi — 设置 DPI
    Step 5: background — AI 抠图换底
    Step 5.5: output sharpen — 输出锐化（缩小/换底后补偿锐度，仅缩图路径）
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

    # Step 2: 裁剪/对齐/尺寸调整（先 resize）
    # 【顺序说明】美颜放在 resize 之后：美颜半径按瞳距换算，在小原图上
    # （iod 仅 50px）半径被压到 3~4px 几乎不起作用；放到目标分辨率上执行，
    # 半径与最终输出匹配，且省掉"先磨皮再放大"的二次插值损失。
    pre_resize_size = image.size
    # 「按宽度 / 按高度」只需要一个维度：单独给出时应仍然生效，
    # 否则前端选「按宽度」只填宽度时会被静默跳过（输出原尺寸）。
    has_both_dims = params.width > 0 and params.height > 0
    single_axis_mode = None
    if not has_both_dims:
        if params.resize_mode == "by_width" and params.width > 0:
            single_axis_mode = ResizeMode.BY_WIDTH
        elif params.resize_mode == "by_height" and params.height > 0:
            single_axis_mode = ResizeMode.BY_HEIGHT

    if has_both_dims:
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
    elif single_axis_mode is not None:
        # 单维度等比缩放：未给出的那一维传 0，resize_image 会按原图比例算出。
        image = resize_image(
            image,
            params.width if single_axis_mode is ResizeMode.BY_WIDTH else 0,
            params.height if single_axis_mode is ResizeMode.BY_HEIGHT else 0,
            mode=single_axis_mode,
            upscale=params.upscale,
        )
    else:
        warnings.append("Skipped resize: target width/height is 0")

    # Step 3: 美颜处理（在目标分辨率上执行）
    # 艺术档（4）是非证件照流程的强效档：显式关闭合规红线钳制；
    # 其余档位（含误传的 2/3）仍默认 compliant=True 走红线钳制。
    if params.beautify_level > BeautyLevel.OFF:
        try:
            image = apply_beautify(
                image,
                level=params.beautify_level,
                smooth=params.beautify_smooth,
                brighten=params.beautify_brighten,
                blemish=params.beautify_blemish,
                adaptive=params.beautify_adaptive,
                compliant=(params.beautify_level != BeautyLevel.ARTISTIC),
            )
        except Exception as e:
            warnings.append(f"Beautify failed: {e}")
            logger.warning(f"Beautify failed, continuing: {e}")

    # Step 3.5: 放大补偿 —— 小图放大后 LANCZOS 偏软，观感像"塑料/假面"，
    # 补一层轻锐化把肌肤微观对比度找回来。只在放大时启用，缩小不锐化。
    if params.width > 0 and params.height > 0:
        image = _upscale_sharpen(image, params.width, params.height, pre_resize_size)

    # Step 5: 背景替换（AI 抠图失败时降级保留原图，避免整单裸 500）
    if params.bg_color and params.bg_color != "keep":
        try:
            image = replace_background(image, params.bg_color)
        except Exception as e:
            warnings.append(f"AI抠图失败(2004)，保留原背景: {e}")
            logger.warning(f"Background replacement failed, keeping original: {e}")

    # Step 5.5: 输出锐化 —— 缩图与换底羽化都会软化画面；PS 流程的「输出锐化」
    # 必须放在所有软化环节之后、DPI/编码之前。与放大路径互斥（不叠加锐化）。
    if params.width > 0 and params.height > 0:
        image = _output_sharpen(image, params.width, params.height, pre_resize_size)

    # Step 6: DPI 设置
    # 【顺序】必须在抠图/锐化之后、压缩之前：前面的步骤都返回新 Image 对象，
    # 会丢掉 info['dpi']。放最后设置，再由 binary_compress 显式写进 JPEG。
    image = adjust_dpi(image, params.dpi)

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