"""增强人脸检测模块：DNN 检测 + 性别估计 + 人脸对齐."""

from typing import Tuple, Optional, List
from dataclasses import dataclass

import numpy as np
from PIL import Image
from loguru import logger

from app.core.image_engine.cascade import frontal_face_cascade


@dataclass
class FaceInfo:
    """人脸信息."""
    x: int
    y: int
    width: int
    height: int
    confidence: float = 1.0
    gender: Optional[str] = None  # 'male' / 'female'
    age_group: Optional[str] = None  # 'child' / 'youth' / 'adult' / 'senior'


def detect_faces(
    image: Image.Image,
    use_dnn: bool = False,
    min_confidence: float = 0.5,
) -> List[FaceInfo]:
    """
    检测图片中的人脸.

    参数:
        image: PIL.Image 输入图片
        use_dnn: 是否使用 DNN 模型（更准确但更慢）
        min_confidence: 最小置信度

    返回:
        FaceInfo 列表（按置信度降序排列）
    """
    import cv2

    img_cv = np.array(image.convert("RGB"))
    img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # minSize 随图像尺寸动态计算：小图/小脸不漏检，大图保持合理阈值避免误检
    # （与 beautify 包的皮肤区域蒙版采用同一动态阈值方案）
    h, w = gray.shape[:2]
    min_size = max(30, min(w, h) // 15)

    faces = []

    # 【中文路径修复】不要直接拼 cv2.data.haarcascades：在含非 ASCII 字符的
    # 安装路径下 CascadeClassifier 会静默变成 empty()，detectMultiScale 抛异常，
    # 最终表现为「0 张脸 → 对齐被跳过 → 美颜在小图上降级」。统一走 cascade.py
    # 的加载器（失败时复制到 ASCII 临时目录），且加载失败会明确抛错而不是静默。
    cascade = frontal_face_cascade()
    if use_dnn:
        # 保留 DNN 开关的旧参数语义（更严格的 minNeighbors），模型依旧是 Haar；
        # 这里不再重复加载，避免两处路径不一致导致「开了 DNN 反而检不到脸」。
        detected = cascade.detectMultiScale(
            gray, scaleFactor=1.05, minNeighbors=8, minSize=(min_size, min_size)
        )
    else:
        detected = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
        )

    if len(detected) == 0:
        logger.warning(
            f"No face detected by Haar cascade (image {w}x{h}, minSize={min_size})"
        )

    for x, y, w, h in detected:
        faces.append(FaceInfo(
            x=int(x), y=int(y),
            width=int(w), height=int(h),
            confidence=1.0,
        ))

    # 按面积降序排列（最大脸优先）
    faces.sort(key=lambda f: f.width * f.height, reverse=True)

    logger.debug(f"Detected {len(faces)} faces")
    return faces


def get_face_center(image: Image.Image) -> Tuple[int, int]:
    """
    获取主要人脸中心坐标.
    如果未检测到人脸，返回图像中心.
    """
    faces = detect_faces(image)

    if faces:
        face = faces[0]  # 取最大脸
        cx = face.x + face.width // 2
        cy = face.y + face.height // 2
        logger.debug(f"Face center: ({cx}, {cy})")
        return cx, cy

    logger.debug("No face detected, using image center.")
    return image.width // 2, image.height // 2


def estimate_gender(image: Image.Image) -> Optional[str]:
    """
    基于面部宽高比粗略估计性别.
    男性：面部宽高比通常 > 0.78
    女性：面部宽高比通常 < 0.78

    注意：这是粗略估计，准确率约 70%。如需更高精度，需集成专门的性别分类模型.
    """
    faces = detect_faces(image)

    if not faces:
        logger.debug("Cannot estimate gender: no face detected.")
        return None

    face = faces[0]
    ratio = face.width / face.height

    if ratio > 0.80:
        gender = "male"
    elif ratio < 0.74:
        gender = "female"
    else:
        gender = "unknown"

    logger.debug(f"Gender estimate: {gender} (face ratio: {ratio:.2f})")
    return gender


# ============================================================ 证件照标准规格
# 依据 PRD《四、尺寸与排版输出规范》
ID_PHOTO_STANDARDS = {
    "一寸":   {"px": (295, 413), "mm": "25×35"},
    "二寸":   {"px": (413, 579), "mm": "35×49"},
    "小一寸": {"px": (260, 378), "mm": "22×32"},
    "大一寸": {"px": (390, 567), "mm": "33×48"},
    "小二寸": {"px": (413, 531), "mm": "35×45"},
}

# 构图红线：越界即判定为不合规证件照。
#
# 【口径统一（2026-11 眼位构图专项）】所有纵向比例一律从照片【上边沿】起算。
# 依据：《出入境证件相片照相指引》（国家移民局 / ICAO DOC 9303）——
#   成人「眼睛距相片上边缘 0.3H ~ 0.5H」、儿童上限 0.6H；
#   居民身份证照片「眼睛距相片下边沿 ≥207px（441px 高）」≈ 距顶部 ≤0.53H。
# 旧口径把眼位写成「距顶部 50%~60%」并配 COMPOSE_EYE_LINE=0.56，
# 等价于要求脸高占画面 56%，正是 2026-10 用户反馈「肩膀被裁没、变大头贴」
# 的成因；也与上述国家标准的 0.5H 上限冲突。头肩保护修复后实际眼位约 36%，
# 落在合规区间内，但常量 / 文档 / 日志 / 测试断言一直没同步，形成「验收永远
# 失败、日志自相矛盾」的假红线。此处把口径一次性对齐到实现与标准。
COMPOSE_TOP_MARGIN = 0.08        # 头顶发迹距上边沿：5%~10%
COMPOSE_TOP_MARGIN_MIN = 0.05
COMPOSE_TOP_MARGIN_MAX = 0.10
# 0.3633 = top_margin 0.08 + (eye_y-head_top)/(3×脸高) = 0.08 + 0.85/3。
# 与下方「头肩保护 min_crop_h = 3×脸高」是同一个几何解，双锚点不再和保护
# 逻辑互相打架（实测修正前后所有真实样张出图像素级 maxdiff=0）。
COMPOSE_EYE_LINE = 0.3633        # 眼睛距上边沿：0.30H ~ 0.50H（成人）
COMPOSE_EYE_LINE_MIN = 0.30
COMPOSE_EYE_LINE_MAX = 0.50
# 60%~70% 是标准里的「头部宽度（含发）」口径；本模块日志回算用的是 Haar
# 人脸框宽（眉弓→下巴的方形框），真实样张实测约 0.47，二者不可直接比较。
COMPOSE_FACE_WIDTH = 0.65        # 头部宽度目标：60%~70%
COMPOSE_FACE_WIDTH_MIN = 0.60
COMPOSE_FACE_WIDTH_MAX = 0.70

# 输出 DPI 下限（PRD 四：不得低于 300dpi）
MIN_OUTPUT_DPI = 300


def align_face(
    image: Image.Image,
    target_width: int = 295,
    target_height: int = 413,
    top_margin_ratio: float = COMPOSE_TOP_MARGIN,
    shoulder_ratio: float = 0.55,
    eye_line_ratio: float = COMPOSE_EYE_LINE,
    face_width_ratio: float = COMPOSE_FACE_WIDTH,
) -> Image.Image:
    """证件照人脸对齐：按构图红线裁剪到标准证件照构图.

    构图约束（所有纵向比例均从【上边沿】起算）:
      * 头顶发迹距上边沿 5%~10%（默认 8%）
      * 眼睛距上边沿 0.30H~0.50H（默认 0.3633，成人国家标准）
      * 头部宽度（含发）占照片宽度 60%~70%（默认 65%，温和校正）
      * 人物水平居中、双肩完整可见

    实现:
      Haar 人脸框 ≈ 眉弓→下巴，据此推算:
          眼位   eye_y    ≈ face.y + 0.30 * face.height
          头顶   head_top ≈ face.y - 0.55 * face.height（含发际线以上头发）
      竖直方向由「头顶留白 + 眼位」双锚点解出缩放比，再用头部宽度比例做
      温和修正（权重 0.25）。随后**头肩保护**是最终裁决：裁剪框高度不得
      小于 3×脸高，否则会把肩膀裁成中间的大头贴。由于
      0.08 + 0.85/3 = 0.3633，双锚点解与头肩保护天然一致，不会互相覆盖。
      裁剪框超出原图时收缩到原图内（宁可留白不足，绝不复制边缘像素把人
      拉宽）；源图不足以留出头顶空白时会打 warning，不再静默出图。

    参数:
        image: 输入图片
        target_width / target_height: 目标像素尺寸
        top_margin_ratio: 头顶留白比例（会被钳制到 5%~10%）
        shoulder_ratio: 肩部区域比例（保留参数，用于日志与兼容旧调用）
        eye_line_ratio: 眼位距上边沿比例（会被钳制到 0.30~0.50）
        face_width_ratio: 头部宽度目标比例（默认 65%）

    返回:
        对齐后的 PIL.Image
    """
    faces = detect_faces(image)

    if not faces:
        logger.warning("No face detected for alignment, using center crop.")
        from app.core.image_engine.crop import resize_crop
        return resize_crop(image, target_width, target_height)

    face = faces[0]

    # --- Haar 框 → 估计头部关键位置 ---
    eye_y = face.y + 0.30 * face.height
    head_top = max(0.0, face.y - 0.55 * face.height)
    face_cx = face.x + face.width / 2.0

    top_margin = float(min(COMPOSE_TOP_MARGIN_MAX,
                           max(COMPOSE_TOP_MARGIN_MIN, top_margin_ratio)))
    eye_ratio = float(min(COMPOSE_EYE_LINE_MAX,
                          max(COMPOSE_EYE_LINE_MIN, eye_line_ratio)))
    W, H = float(target_width), float(target_height)
    span = max(eye_y - head_top, 1.0)

    # --- 竖直双锚点解出缩放比 ---
    scale = (eye_ratio - top_margin) * H / span

    # --- 横向脸宽温和校正，结果仍锁在眼位允许带内 ---
    if face.width > 0:
        scale_w = face_width_ratio * W / float(face.width)
        lo = (COMPOSE_EYE_LINE_MIN - top_margin) * H / span
        hi = (COMPOSE_EYE_LINE_MAX - top_margin) * H / span
        scale = float(np.clip(0.75 * scale + 0.25 * scale_w, lo, hi))

    scale = max(scale, 1e-3)
    crop_w = W / scale
    crop_h = H / scale

    crop_left = int(round(face_cx - crop_w / 2.0))
    crop_top = int(round(head_top - top_margin * crop_h))
    crop_right = crop_left + int(round(crop_w))
    crop_bottom = crop_top + int(round(crop_h))

    # ---- 头肩保护（2026-10 用户反馈：对齐把肩膀裁没、变成大头贴）----
    # 合规构图必须露出完整双肩：头发顶部到下巴的竖直跨度约 1.55×脸高，
    # 再加上肩线到下缘的空间，合理的裁高不应小于约 3×脸高（脸高占构图 ~33%）。
    # 若双锚点解出的裁高比这还小，说明对齐放大过度 → 按头肩构图重新放大裁剪框。
    min_crop_h = face.height * 3.0
    if crop_bottom - crop_top < min_crop_h:
        crop_h = min_crop_h
        # 目标比例锁死，裁宽必须跟着裁高走（否则会拉伸变形）
        crop_w = crop_h * (W / H)
        crop_left = int(round(face_cx - crop_w / 2.0))
        # 竖直位置：头顶仍留白 ~8%，不再使用「眼位锚定」
        crop_top = int(round(head_top - top_margin * crop_h))
        crop_right = crop_left + int(round(crop_w))
        crop_bottom = crop_top + int(round(crop_h))
        logger.info(
            f"Align guarded: face {face.width}x{face.height} would over-zoom "
            f"(crop_h={min_crop_h:.0f}), widened to include head & shoulders"
        )

    # ---- 【关键修复】裁剪框不得超出原图，否则会横向拉伸 ----
    # 【问题现象】用户实测：295×413 的原图（脸已顶满画面）出图后人物明显变宽、
    # 肩膀被拉长，脸型失真。对比图里右侧人物比左侧宽约 30%。
    #
    # 【根因】上面的头肩保护会把 crop_h 撑到 face.height*3。本例原图 295×413、
    # 检测到的脸 179×179 → min_crop_h=537、crop_w=384，**都超过原图 413/295**。
    # 旧代码对越界部分做 np.pad(mode="edge")：把最外一列/行像素整条复制出去，
    # 相当于凭空造出一圈"被拉伸的边缘"。裁剪窗口比原图大 30%，再缩回目标尺寸，
    # 净效果就是把人物横向拉宽 30% —— 这不是美颜问题，是几何变形。
    #
    # 【修法】越界时把裁剪框**收缩到原图能提供的最大范围**（保持目标长宽比），
    # 然后只在原图内部裁剪。宁可构图略微偏离理想眼位，也绝不拉伸像素。
    # 只有「原图本身就比裁剪框小很多」这种极端情况才退化为整图等比缩放。
    img_w, img_h = image.size
    need_w = crop_right - crop_left
    need_h = crop_bottom - crop_top
    if need_w > img_w or need_h > img_h:
        # 按目标比例求出原图能容纳的最大裁剪框
        fit_scale = min(img_w / float(need_w), img_h / float(need_h))
        if fit_scale < 1.0:
            new_w = max(32.0, need_w * fit_scale)
            new_h = max(32.0, need_h * fit_scale)
            logger.info(
                f"Align clamped: crop {need_w}x{need_h} exceeds image {img_w}x{img_h}, "
                f"shrink to {new_w:.0f}x{new_h:.0f} (avoids edge-pad stretching)"
            )
            # 保持人物中心不变，只缩窗口
            cx = (crop_left + crop_right) / 2.0
            cy = (crop_top + crop_bottom) / 2.0
            crop_w, crop_h = new_w, new_h
            crop_left = int(round(cx - new_w / 2.0))
            crop_top = int(round(cy - new_h / 2.0))
            crop_right = crop_left + int(round(new_w))
            crop_bottom = crop_top + int(round(new_h))
            # 平移回图内（不缩放，纯平移，零变形）
            if crop_left < 0:
                crop_right -= crop_left
                crop_left = 0
            if crop_top < 0:
                crop_bottom -= crop_top
                crop_top = 0
            if crop_right > img_w:
                crop_left -= (crop_right - img_w)
                crop_right = img_w
            if crop_bottom > img_h:
                crop_top -= (crop_bottom - img_h)
                crop_bottom = img_h
            crop_left = max(0, crop_left)
            crop_top = max(0, crop_top)

    if crop_right - crop_left < 32 or crop_bottom - crop_top < 32:
        logger.warning("Face alignment produced invalid crop, using full image resize.")
        return image.resize((target_width, target_height), Image.LANCZOS)

    # --- 越界部分用边缘像素外扩（避免黑边；换底会覆盖这些区域）---
    # 注意：上面的 clamp 已保证正常情况下不会越界；这里只兜「几乎没越界」的
    # 亚像素情形（<2px），此时边缘外扩不会造成可感知的拉伸。
    pad_l = max(0, -crop_left)
    pad_t = max(0, -crop_top)
    pad_r = max(0, crop_right - image.width)
    pad_b = max(0, crop_bottom - image.height)

    if pad_l or pad_t or pad_r or pad_b:
        arr = np.array(image.convert("RGB"))
        arr = np.pad(arr, ((pad_t, pad_b), (pad_l, pad_r), (0, 0)), mode="edge")
        src = Image.fromarray(arr)
        crop_left += pad_l
        crop_right += pad_l
        crop_top += pad_t
        crop_bottom += pad_b
        logger.debug(f"Alignment padded source by L{pad_l} T{pad_t} R{pad_r} B{pad_b}")
    else:
        src = image

    cropped = src.crop((crop_left, crop_top, crop_right, crop_bottom))
    result = cropped.resize((target_width, target_height), Image.LANCZOS)

    # 回算实际构图，便于验收核对。
    # 【2026-11 P0 修复】旧代码用双锚点解出的 `scale` 回算，但上面的
    # 「头肩保护 / 越界 clamp」都会改变裁剪框尺寸，scale 早已失效 ——
    # 日志因此报出「头顶留白 13%、脸宽 75.5%」这类自相矛盾的数字，
    # 让验收误以为构图越了红线。构图比例只取决于「源坐标 / 实际裁剪框」，
    # 与 scale 无关，直接用实际 crop 尺寸计算才是可信口径。
    crop_w_actual = max(1.0, float(crop_right - crop_left))
    crop_h_actual = max(1.0, float(crop_bottom - crop_top))
    head_top_out = (head_top + pad_t - crop_top) / crop_h_actual
    eye_out = (eye_y + pad_t - crop_top) / crop_h_actual
    face_w_out = face.width / crop_w_actual
    # 【眼位构图专项】源图不足时不再静默出一张「头顶被切平」的照片。
    # 越界 clamp 会为了不拉伸像素而收缩/平移裁剪框，代价可能就是留白归零；
    # 这里只告警，不改几何（改几何会让全部 A/B 基线失效，需单独重标定）。
    if head_top_out < COMPOSE_TOP_MARGIN_MIN - 0.005:
        logger.warning(
            f"Composition warning: top margin {head_top_out:.1%} < "
            f"{COMPOSE_TOP_MARGIN_MIN:.0%}; source {image.width}x{image.height} cannot hold "
            "a compliant crop without stretching. Please use a photo with more headroom."
        )
    logger.info(
        f"Face aligned: face@{face.x},{face.y} {face.width}x{face.height} "
        f"-> crop ({crop_left},{crop_top})-({crop_right},{crop_bottom}) "
        f"-> {target_width}x{target_height} | "
        f"头顶留白={head_top_out:.1%}(需{COMPOSE_TOP_MARGIN_MIN:.0%}-{COMPOSE_TOP_MARGIN_MAX:.0%}) "
        f"眼位={eye_out:.1%}(距上边沿,需{COMPOSE_EYE_LINE_MIN:.0%}-{COMPOSE_EYE_LINE_MAX:.0%}) "
        f"Haar框宽占比={face_w_out:.1%}(头部宽度目标{COMPOSE_FACE_WIDTH_MIN:.0%}-{COMPOSE_FACE_WIDTH_MAX:.0%}) "
        f"shoulder={shoulder_ratio}"
    )
    return result


# 证件照背景色（扩展版）
ID_PHOTO_BG_COLORS = {
    "white":      (255, 255, 255),  # 白色（最常用）
    "blue":       (67, 142, 219),   # 蓝色（标准证件照蓝）
    "red":        (213, 43, 30),    # 红色（标准证件照红）
    "light_blue": (173, 216, 230),  # 浅蓝
    "dark_blue":  (25, 55, 128),    # 深蓝（护照蓝）
    "gray":       (192, 192, 192),  # 灰色
    "light_gray": (220, 220, 220),  # 浅灰
    "green":      (0, 128, 0),      # 绿色（部分国家签证）
    "custom":     None,              # 自定义颜色（由调用方传入 RGB）
}

# 证件照服装建议（男女）
CLOTHING_SUGGESTIONS = {
    "male": {
        "formal": "深色西装 + 白衬衫 + 领带",
        "casual": "深色有领衬衫",
        "colors": ["深蓝", "深灰", "黑色", "白色"],
    },
    "female": {
        "formal": "深色西装 + 浅色衬衫",
        "casual": "深色有领上衣",
        "colors": ["深蓝", "深灰", "酒红", "白色"],
    },
}


def get_id_photo_params(gender: Optional[str] = None) -> dict:
    """
    根据性别获取推荐的证件照参数.

    构图值直接取自 PRD 四 的合规红线（不再是旧版的 0.25 留白）.

    返回:
        dict: 包含 bg_color, clothing_suggestion, top_margin, eye_line 等
    """
    params = {
        "bg_color": "white",
        "clothing": "深色有领上衣",
        "face_ratio": 0.65,                    # 人脸占照片高度的比例
        "top_margin": COMPOSE_TOP_MARGIN,      # 头顶留白 8%（距上边沿 5%~10%）
        "eye_line": COMPOSE_EYE_LINE,          # 眼位 36.3%（距上边沿 30%~50%）
        "eye_line_basis": "from_top",          # 明确口径，避免调用方再次误读
        "face_width": COMPOSE_FACE_WIDTH,      # 头部宽度 65%（目标 60%~70%）
        "min_dpi": MIN_OUTPUT_DPI,             # 输出 DPI 下限 300
    }

    if gender == "male":
        params["clothing"] = "深色西装 + 白衬衫"
    elif gender == "female":
        params["clothing"] = "深色西装 + 浅色衬衫"

    return params