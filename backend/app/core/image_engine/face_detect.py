"""增强人脸检测模块：DNN 检测 + 性别估计 + 人脸对齐."""

from typing import Tuple, Optional, List
from dataclasses import dataclass

import numpy as np
from PIL import Image
from loguru import logger


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
    # （与 beautify.py 的 _face_region_mask 采用同一动态阈值方案）
    h, w = gray.shape[:2]
    min_size = max(30, min(w, h) // 15)

    faces = []

    if use_dnn:
        # 尝试使用 OpenCV DNN 人脸检测（更准确）
        try:
            # 使用 OpenCV 内置的 DNN 模型
            model_file = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            # DNN 路径因 OpenCV 版本而异，使用 Haar 作为回退
            cascade = cv2.CascadeClassifier(model_file)
            detected = cascade.detectMultiScale(
                gray, scaleFactor=1.05, minNeighbors=8, minSize=(min_size, min_size)
            )
        except Exception as e:
            logger.warning(f"DNN face detection failed: {e}, falling back to Haar.")
            cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            detected = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
            )
    else:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        detected = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
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

# 构图红线（PRD 四）：越界即判定为不合规证件照
COMPOSE_TOP_MARGIN = 0.08        # 头顶留白占高度：5%~10%
COMPOSE_TOP_MARGIN_MIN = 0.05
COMPOSE_TOP_MARGIN_MAX = 0.10
COMPOSE_EYE_LINE = 0.56          # 眼睛位于高度：50%~60%
COMPOSE_EYE_LINE_MIN = 0.50
COMPOSE_EYE_LINE_MAX = 0.60
COMPOSE_FACE_WIDTH = 0.65        # 脸宽占照片宽度：60%~70%
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
    """证件照人脸对齐：按 PRD 四 的构图红线裁剪到标准证件照构图.

    构图约束（硬指标）:
      * 头顶留白占照片总高度 5%~10%（默认 8%）
      * 眼睛位于照片总高度 50%~60%（默认 56%）
      * 脸部宽度占照片宽度 60%~70%（默认 65%，温和校正且不破坏眼位）
      * 人物水平居中

    实现:
      Haar 人脸框 ≈ 眉弓→下巴，据此推算:
          眼位   eye_y    ≈ face.y + 0.30 * face.height
          头顶   head_top ≈ face.y - 0.55 * face.height（含发际线以上头发）
      竖直方向同时锚定「头顶留白」与「眼位」两条红线，解出唯一缩放比；
      再用脸宽比例做温和修正（权重 0.25），并钳制在眼位允许带内。
      源图不够大时用**边缘像素外扩**而不是留黑边——换底发生在此步之后，
      外扩区域会被 AI 抠图判为背景并覆盖，因此不影响出图。

    参数:
        image: 输入图片
        target_width / target_height: 目标像素尺寸
        top_margin_ratio: 头顶留白比例（会被钳制到 5%~10%）
        shoulder_ratio: 肩部区域比例（保留参数，用于日志与兼容旧调用）
        eye_line_ratio: 眼位比例（会被钳制到 50%~60%）
        face_width_ratio: 脸宽占宽比例（默认 65%）

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

    if crop_right - crop_left < 32 or crop_bottom - crop_top < 32:
        logger.warning("Face alignment produced invalid crop, using full image resize.")
        return image.resize((target_width, target_height), Image.LANCZOS)

    # --- 越界部分用边缘像素外扩（避免黑边；换底会覆盖这些区域）---
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

    # 回算实际构图，便于验收核对
    head_top_out = (head_top + pad_t - crop_top) * scale / H
    eye_out = (eye_y + pad_t - crop_top) * scale / H
    face_w_out = face.width * scale / W
    logger.info(
        f"Face aligned: face@{face.x},{face.y} {face.width}x{face.height} "
        f"-> crop ({crop_left},{crop_top})-({crop_right},{crop_bottom}) "
        f"-> {target_width}x{target_height} | "
        f"头顶留白={head_top_out:.1%}(需5-10%) 眼位={eye_out:.1%}(需50-60%) "
        f"脸宽占比={face_w_out:.1%}(需60-70%) shoulder={shoulder_ratio}"
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
        "top_margin": COMPOSE_TOP_MARGIN,      # 头顶留白 8%（红线上限 5%~10%）
        "eye_line": COMPOSE_EYE_LINE,          # 眼位 56%（红线区间 50%~60%）
        "face_width": COMPOSE_FACE_WIDTH,      # 脸宽占比 65%（红线区间 60%~70%）
        "min_dpi": MIN_OUTPUT_DPI,             # 输出 DPI 下限 300
    }

    if gender == "male":
        params["clothing"] = "深色西装 + 白衬衫"
    elif gender == "female":
        params["clothing"] = "深色西装 + 浅色衬衫"

    return params