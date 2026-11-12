"""图像处理流水线编排：几何到工作分辨率 → 美颜 → 缩到目标像素 → 换底 → 单次锐化 → dpi → compress.

【2026-11 P0 顺序修复】美颜原本放在缩放后的 295×413 小图上执行：整套算法依赖
MediaPipe 关键点 + SegFormer 人脸解析（512 人脸裁图训练），在目标分辨率上瞳距
只有 17~50px，关键点经常检不出、皮肤解析覆盖率仅 1%~10%，只能走 fallback
蒙版 —— 这是「美颜几乎看不出变化」和「时好时坏」的结构性根因。
现在统一在**工作分辨率**（短边 1024~1536、脸宽 60~70%、瞳距 ≥100px）上完成
几何与美颜，再一次性缩回目标像素；baseline 走完全相同的几何链路，控制变量不变。
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from PIL import Image, ImageFilter
from loguru import logger

from app.core.image_engine.crop import resize_crop, face_aware_crop
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

# 受证件照合规红线钳制的档位（见 docs/IMAGE_QUALITY_STANDARD.md）：
#   OFF / COMPLIANT(=LIGHT=1) / 内部测试档 2、3。
# ARTISTIC(4) 与 STUDIO(5) 都不在此集合内 —— 它们是非报名场景的强效档，
# 出图不受「磨皮 ≤12%、纹理保留 ≥90%」等红线约束。
_COMPLIANT_LEVELS = frozenset({
    BeautyLevel.OFF, BeautyLevel.COMPLIANT, BeautyLevel.MEDIUM, BeautyLevel.HIGH,
})

# ---- 美颜工作分辨率（P0：保证「脸足够大」这个前置条件）----
# 关键点/皮肤解析模型是在 512 人脸裁图上训练的，瞳距 ≥100px 才稳定。
# 工作短边取 1024~1536，且**不超过原图短边**（放大超过原图不会增加信息）。
# 放大倍率不足 2× 时不启用工作图，避免无意义的插值往返。
WORK_SHORT_MIN = 1024
WORK_SHORT_MAX = 1536
WORK_MIN_SCALE = 2.0


def _working_geometry(
    orig_size: Tuple[int, int], target_w: int, target_h: int,
) -> Optional[Tuple[int, int]]:
    """算出「美颜应在其上执行」的工作尺寸；不划算时返回 None（保持旧口径）."""
    short = min(target_w, target_h)
    orig_short = min(orig_size)
    if short <= 0 or orig_short <= 0:
        return None
    ideal = max(WORK_SHORT_MIN, short * 3)
    work_short = min(WORK_SHORT_MAX, ideal, orig_short)
    if work_short < short * WORK_MIN_SCALE:
        return None
    scale = work_short / float(short)
    return max(target_w, int(round(target_w * scale))), \
           max(target_h, int(round(target_h * scale)))


def _use_working_resolution(
    params: "ProcessParams", orig_size: Tuple[int, int], has_both_dims: bool,
) -> Optional[Tuple[int, int]]:
    """有明确目标尺寸 + 目标尺寸偏小 → 走工作分辨率.

    【故意不看 beautify_level】render_baseline 会把 level 置 0，如果这里按
    「美颜开启」判定，基线就会走另一条缩放链路，两张图虽然同像素但插值次数
    不同 —— 控制变量被破坏，前端对比会出现「基线更锐」的假差异。
    几何决策只依赖尺寸，与是否美颜无关。
    """
    if not has_both_dims:
        return None
    if min(params.width, params.height) >= 600:
        return None   # 目标本身已是大图，美颜在目标分辨率上就有足够尺度
    return _working_geometry(orig_size, params.width, params.height)


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

    # === 美颜（证件照只允许「自然微调」COMPLIANT 档；艺术/精修档仅非报名场景）===
    beautify_level: int = BeautyLevel.COMPLIANT  # 0=OFF, 1=自然微调, 2/3=内部测试, 4=艺术档, 5=影楼精修
    beautify_smooth: bool = True
    beautify_brighten: bool = True
    beautify_blemish: bool = True
    beautify_adaptive: bool = True   # ⑤ 按肤质/曝光/清晰度/人脸占比自适应缩放强度
    # 分区域处理系统（⑦⑧⑨）：默认全开，关掉即退回「整脸滤镜」旧行为
    beautify_tone: bool = True       # ⑦ 分区域低频肤色修正（额头/两颊/下颌/脖子）
    beautify_texture: bool = True    # ⑧ 纹理回注（磨皮拿走的毛孔细节补回来）
    beautify_feature: bool = True    # ⑨ 五官轮廓带局部增强（眼/眉/鼻翼/唇缘）
    beautify_spot: bool = True       # ⑮ 局部色斑抑制（痘印/红斑的 a*/b* 小尺度异常，P1 新增）
    retouch_strength: float = 1.0    # ⑥ 用户「还原度」滑杆 0~1（默认 1=完整档位，0=不修图）

    # === 证件照对齐 ===
    # 【2026-11 P0】默认开启：不开启时 resize_crop 只居中不放大，全身照的脸
    # 缩到 295×413 后只有约 45px，关键点检不出 → 美颜静默降级。
    id_photo_align: bool = True   # 是否启用证件照人脸对齐
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
    # 美颜生效报告（mode / parse / iod / mask_coverage / skin_diff / identity）
    # —— 「没生效」必须能被系统自己发现并透传给前端，不再静默出图。
    beautify_report: dict = field(default_factory=dict)


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
                    pre_resize_size: Optional[Tuple[int, int]] = None,
                    skin_aware: bool = True) -> Image.Image:
    """**唯一一次**输出锐化：所有插值/换底软化之后、DPI/编码之前执行.

    【2026-11 P0】旧链路有两级锐化（_upscale_sharpen + 本函数），它们与
    beautify 的纹理回注共抢同一份「细节预算」：实测合规档净高频比 1.01~1.04，
    即「越修越锐」，磨皮全被锐化吃回去。现在合并为一次，并对皮肤区降权：
      * 非皮肤（发丝/衣领/背景边缘）保持 0.55 幅度，出图不糊；
      * 皮肤区幅度 ×0.15，给磨皮留下可感知的空间；
      * 放大路径用 0.40（放大插值本身偏软，但不需要两级叠加）。
    目标净高频比 0.92~0.96：皮肤确实被修饰，五官/发丝仍然清楚。
    """
    if not pre_resize_size:
        return image
    base_w, base_h = pre_resize_size
    zoom = max(target_w / base_w, target_h / base_h)
    if 0.98 <= zoom <= 1.02:
        return image   # 没有发生插值，没有软化，不锐化

    import cv2
    import numpy as np

    w, h = image.size
    bgr = np.ascontiguousarray(np.array(image.convert("RGB"))[:, :, ::-1])
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan = lab[:, :, 0].astype(np.float32)
    radius = 0.7 if min(w, h) <= 600 else 1.0
    amount = 0.55 if zoom < 0.98 else 0.40
    blur = cv2.GaussianBlur(l_chan, (0, 0), radius)

    if skin_aware:
        # YCrCb 肤色椭圆 → 软掩膜（0~1）；只降权皮肤，不碰发丝/衣领的结构边缘
        ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        cr = ycrcb[:, :, 1].astype(np.float32)
        cb = ycrcb[:, :, 2].astype(np.float32)
        yy = ycrcb[:, :, 0].astype(np.float32)
        skin = ((cr >= 133) & (cr <= 180) & (cb >= 77) & (cb <= 127) & (yy > 60))
        skin = cv2.GaussianBlur(skin.astype(np.float32), (0, 0), 1.5)
        gain = amount * (1.0 - 0.85 * skin)
        skin_pct = float(skin.mean())
    else:
        gain = amount
        skin_pct = 0.0

    lab[:, :, 0] = np.clip(l_chan + (l_chan - blur) * gain, 0.0, 255.0).astype(np.uint8)
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    logger.info(
        f"Output sharpen (single pass): zoom={zoom:.2f} radius={radius} "
        f"amount={amount:.2f} skin_aware={skin_aware} skin={skin_pct:.1%}"
    )
    return Image.fromarray(np.ascontiguousarray(out[:, :, ::-1]))


def _beautify_warnings(report: dict) -> list:
    """把美颜生效报告翻译成用户能看懂的告警（不再静默出「没美颜的图」）."""
    if not report:
        return []
    out = []
    if not report.get("applied"):
        out.append(
            f"美颜未生效（{report.get('reason', 'unknown')}）：本次出图等同原图，"
            "建议换正面、清晰、脸部占比更大的照片后重试"
        )
        return out
    if report.get("mode") == "fallback":
        out.append(
            "美颜已降级：未检出人脸关键点，仅使用基础肤色蒙版，修饰效果有限；"
            "建议换正面、清晰的照片"
        )
    coverage = float(report.get("mask_coverage", 0.0) or 0.0)
    if 0.0 < coverage < 0.03:
        out.append(
            f"美颜生效面积偏小（{coverage:.1%}），效果可能不明显；建议使用脸部占比更大的原图"
        )
    iod = float(report.get("iod_px", 0.0) or 0.0)
    if 0.0 < iod < 40.0:
        out.append(
            f"人脸尺度偏小（瞳距约 {iod:.0f}px），修饰精度受限；建议使用更高分辨率原图"
        )
    sim = report.get("identity_sim")
    if sim is not None and float(sim) < 0.90:
        out.append(f"身份校验偏低（{float(sim):.3f}），建议降低美颜强度后重试")
    return out


def render_baseline(image: Image.Image, params: ProcessParams) -> ProcessResult:
    """生成「与效果图同几何」的原图基线（对比用，不做美颜/不换背景）.

    【为什么必须由后端出这张图】用户对比「原图 / 效果图」时，最致命的问题
    不是美颜强弱，而是**两张图的几何不一致**：效果图是「裁剪 + 证件照对齐 +
    缩放到目标像素」之后的产物，而上传的原图还是手机原片（比例、人脸位置、
    缩放全都不一样）。把这两张图并排放在一起，用户看到的第一个差异永远是
    「右边脸怎么这么大」，根本没法判断肤色/质感的变化。

    正确做法是让后端用**完全相同的几何链路**（同样的裁剪、同样的对齐、
    同样的输出像素与 DPI）再渲染一张「只关掉美颜、只关掉换底」的图，
    前端拿这两张同尺寸图做对比 —— 缩放比例、中心点、裁切窗口天然一致，
    差异只剩美颜本身。这就是「控制变量」。

    实现上直接复用 process_image：把 beautify_level 置 0、bg_color 置 keep，
    走的是同一段裁剪/对齐/锐化/编码代码，几何不可能跑偏（不复制粘贴逻辑）。
    """
    from dataclasses import replace

    baseline_params = replace(params, beautify_level=BeautyLevel.OFF, bg_color="keep")
    result = process_image(image, baseline_params)
    logger.info(
        f"Baseline rendered: {result.result_pixels}@{result.result_dpi}DPI "
        f"{result.result_size_kb:.1f}KB (same geometry as result)"
    )
    return result


def process_image(
    image: Image.Image, params: ProcessParams,
) -> ProcessResult:
    """
    图像处理流水线（P0 顺序：几何 → 美颜 → 缩放 → 换底 → 单次锐化 → 编码）:

    Step 1: 人脸检测（获取人脸信息，用于后续步骤）
    Step 2a: crop/align/resize 到 **工作分辨率**（短边 1024~1536，脸宽 60~70%）
    Step 2b: beautify —— 在原生尺度上执行（关键点/皮肤解析模型需要足够大的脸）
    Step 2c: 一次性 LANCZOS 缩回目标像素（295×413 等）
    Step 3: background —— AI 抠图换底
    Step 4: output sharpen —— 唯一一次输出锐化（皮肤区降权）
    Step 5: dpi → compress → format

    render_baseline 走完全相同的几何链路（只关美颜/换底），因此两张图的
    裁剪窗口、对齐方式、缩放比例严格一致，差异只剩美颜本身。

    返回 ProcessResult
    """
    start_time = time.time()
    warnings = []
    faces_count = 0
    beautify_report: dict = {}

    # Step 1: 人脸检测
    try:
        faces = detect_faces(image)
        faces_count = len(faces)
        logger.debug(f"Detected {faces_count} face(s)")
    except Exception as e:
        logger.warning(f"Face detection failed: {e}")
        faces = []

    # ---- Step 2a: 几何到「工作分辨率」----
    # 【P0 顺序修复】美颜必须在「脸足够大」的图上执行。旧顺序先缩到 295×413，
    # 瞳距只剩 17~50px，关键点与皮肤解析模型全部失效 → fallback 蒙版覆盖
    # 1.4%，效果归零。现在几何先做到短边 1024~1536（脸宽 60~70%），
    # 美颜跑完再一次性缩回目标像素。
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

    work_size = _use_working_resolution(params, pre_resize_size, has_both_dims)
    geom_w, geom_h = work_size if work_size else (params.width, params.height)
    if work_size:
        logger.info(
            f"Beautify working resolution: {pre_resize_size} -> {work_size} "
            f"-> target {(params.width, params.height)}"
        )

    if has_both_dims:
        if params.id_photo_align and faces_count > 0:
            # 证件照人脸对齐模式（对齐到工作分辨率，构图比例不变）
            try:
                align_kwargs = {}
                if params.gender:
                    idp = get_id_photo_params(params.gender)
                    align_kwargs["top_margin_ratio"] = idp.get("top_margin", 0.25)
                    warnings.append(
                        f"证件照推荐服装({params.gender}): {idp.get('clothing', '深色有领上衣')}"
                    )
                image = align_face(image, geom_w, geom_h, **align_kwargs)
            except Exception as e:
                warnings.append(f"Face alignment failed: {e}")
                # 降级也必须保证「脸足够大」，不能退回纯中心裁切
                image = face_aware_crop(image, geom_w, geom_h)
        elif params.resize_mode == "crop":
            # 未开启对齐时用「按人脸比例放大」的裁切，避免全身照里脸过小
            image = (face_aware_crop(image, geom_w, geom_h) if work_size
                     else resize_crop(image, params.width, params.height))
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
            image = resize_image(image, geom_w, geom_h, mode=mode, upscale=params.upscale)
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

    # ---- Step 2b: 美颜（在原生/工作分辨率上执行）----
    # 艺术档（4）与影楼精修档（5）都是「不受证件照合规红线约束」的档位：
    # 显式关闭 compliant 钳制；其余档位（含误传的 2/3）仍默认走红线钳制。
    if params.beautify_level > BeautyLevel.OFF:
        report: dict = {}
        try:
            image = apply_beautify(
                image,
                level=params.beautify_level,
                smooth=params.beautify_smooth,
                brighten=params.beautify_brighten,
                blemish=params.beautify_blemish,
                adaptive=params.beautify_adaptive,
                # 【为什么是「不在合规白名单」而不是「等于 ARTISTIC」】
                # 以后每加一个非合规档位都要改这行，漏改的后果是「选了强档位却
                # 出合规图」——静默降级，最难排查。反过来写：只有明确合规的
                # OFF/COMPLIANT/内部测试档走红线，其余一律放开。
                compliant=params.beautify_level in _COMPLIANT_LEVELS,
                strength=params.retouch_strength,
                tone=params.beautify_tone,
                texture=params.beautify_texture,
                feature=params.beautify_feature,
                spot=params.beautify_spot,
                report=report,
            )
        except Exception as e:
            report = {"applied": False, "reason": "exception", "error": str(e)}
            warnings.append(f"美颜未生效（处理异常，已按原图出图）：{e}")
            logger.warning(f"Beautify failed, continuing: {e}")
        beautify_report = report
        warnings.extend(_beautify_warnings(report))
        logger.info(f"Beautify report: {report}")

    # ---- Step 2c: 一次性缩回目标像素 ----
    # baseline 走同一段代码（level=OFF 但 work_size 判定一致），几何严格对齐。
    if work_size and has_both_dims:
        image = image.resize((params.width, params.height), Image.LANCZOS)
        logger.info(f"Working -> target downscale: {work_size} -> {image.size}")

    # 【已移除】旧 Step 3.5 的 _upscale_sharpen：与 _output_sharpen 两级叠加
    # 会吃光磨皮（实测净高频比 >1.0，越修越锐）。现在全链路只锐化一次。

    # Step 3: 背景替换（AI 抠图失败时降级保留原图，避免整单裸 500）
    if params.bg_color and params.bg_color != "keep":
        try:
            image = replace_background(image, params.bg_color)
        except Exception as e:
            warnings.append(f"AI抠图失败(2004)，保留原背景: {e}")
            logger.warning(f"Background replacement failed, keeping original: {e}")

    # Step 4: 输出锐化 —— 全链路唯一一次，放在所有软化环节之后、编码之前。
    # 皮肤区降权，给磨皮留出可感知的空间（详见 _output_sharpen 注释）。
    if params.width > 0 and params.height > 0:
        image = _output_sharpen(image, params.width, params.height, pre_resize_size)

    # Step 5: DPI 设置
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
            # id_photo_align 现在默认为 True，不能再拿它判断「是不是证件照」，
            # 否则自定义尺寸会平白收到「非标准证件照像素」告警。
            is_id_photo=(params.width, params.height) in _STANDARD_PX,
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
        beautify_report=beautify_report,
    )

    logger.info(
        f"Pipeline done: {result.result_pixels}@{result.result_dpi}DPI "
        f"→ {result.result_size_kb:.1f}KB {result.output_format} q={quality} "
        f"beauty={params.beautify_level} faces={faces_count} "
        f"beautify_applied={beautify_report.get('applied')} "
        f"in {elapsed_ms}ms"
    )
    return result