"""背景替换模块 — BiRefNet ONNX 抠图 + 边缘精修 + 环境光融合 + 软投影.

相比旧方案 rembg/u2net_human_seg，BiRefNet 采用双边参考机制，
在头发丝、绒毛、半透明边缘上分割质量显著更优（CAAI AIR 2024）。
模型文件：backend/models/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx (214MB, MIT)
推理后端：onnxruntime CPU（无新增重依赖，离线运行）。

在 BiRefNet 之后额外做了四项「出图质量」处理（对应 PRD 二/三）：
    1. 引导滤波贴合真实轮廓        → 消除 1024→原图 的插值锯齿
    2. 形态学腐蚀割污染带          → 消除白边 / 黑边 / 色晕
    3. 发丝级高斯羽化 + 环境光融合 → 颈部肩部自然过渡，不再「硬切」
    4. 贴合轮廓的软投影            → 产生空间纵深，消除「剪纸漂浮感」
"""

import io
import os
from functools import lru_cache

import numpy as np
from PIL import Image
from loguru import logger

# 标准证件照背景色映射（扩展版）
BG_COLOR_MAP = {
    "white":      (255, 255, 255),   # 白色
    "blue":       (67, 142, 219),    # 标准蓝
    "red":        (213, 43, 30),     # 标准红
    "light_blue": (173, 216, 230),   # 浅蓝
    "dark_blue":  (25, 55, 128),     # 深蓝（护照蓝）
    "gray":       (192, 192, 192),   # 灰色
    "light_gray": (220, 220, 220),   # 浅灰
    "green":      (0, 128, 0),       # 绿色
    "pink":       (255, 192, 203),   # 粉色
    "cream":      (255, 253, 208),   # 米色
    "sky_blue":   (135, 206, 235),   # 天蓝
    "navy":       (0, 0, 128),       # 藏青
    "maroon":     (128, 0, 0),       # 深红
    "teal":       (0, 128, 128),     # 青色
    "lavender":   (230, 230, 250),   # 淡紫
}

# BiRefNet 模型文件名（backend/models/ 目录）
BIRefNET_MODEL_FILE = "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx"

# 推理输入分辨率（模型固定 1024x1024）
_INPUT_SIZE = 1024

# ImageNet 归一化参数
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)


def _model_candidates(model_name: str) -> list:
    """返回模型候选搜索路径（项目 models 目录 / rembg 下载目录 / 工作目录）."""
    backend_models = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "models",
    )
    return [
        os.path.join(backend_models, model_name),
        os.path.expanduser(f"~/.u2net/{model_name}.onnx"),
        os.path.join(os.getcwd(), f"{model_name}.onnx"),
        os.path.join("/root/.u2net", f"{model_name}.onnx"),
    ]


def _resolve_model_path(model_name: str):
    """按候选路径查找模型文件，返回存在的第一个绝对路径，找不到返回 None."""
    return next((p for p in _model_candidates(model_name) if os.path.exists(p)), None)


@lru_cache(maxsize=2)
def _get_session(model_path: str):
    """模块级单例缓存 onnxruntime session（onnxruntime session 可并发复用）."""
    import onnxruntime as ort
    logger.info(f"Loading BiRefNet model session from: {model_path}")
    # 动态选择可用 provider（CPU 兜底，有 GPU 自动加速）
    available = set(ort.get_available_providers())
    providers = [p for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider", "CPUExecutionProvider")
                 if p in available]
    if not providers:
        providers = ["CPUExecutionProvider"]
    sess = ort.InferenceSession(model_path, providers=providers)
    # 记录输入/输出名称（不同导出版本命名可能不同，动态获取）
    _get_session.input_name = sess.get_inputs()[0].name
    _get_session.output_name = sess.get_outputs()[0].name
    _get_session.sess = sess
    return sess


def _run_birefnet(image: Image.Image) -> np.ndarray:
    """
    使用 BiRefNet 推理得到前景 alpha 蒙版.

    返回:
        np.ndarray: float32, 0~1, 尺寸与原图一致 (H, W)，1=前景 0=背景.
    """
    import cv2

    model_path = _resolve_model_path(BIRefNET_MODEL_FILE)
    if model_path is None:
        raise FileNotFoundError(
            f"Model file not found. Looked in: {_model_candidates(BIRefNET_MODEL_FILE)}. "
            f"Please place {BIRefNET_MODEL_FILE} in backend/models/."
        )

    sess = _get_session(model_path)

    # 预处理：RGB 归一化 resize 到 1024x1024
    rgb = np.array(image.convert("RGB"), dtype=np.float32) / 255.0          # HWC
    rgb_resized = cv2.resize(rgb, (_INPUT_SIZE, _INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    chw = np.transpose(rgb_resized, (2, 0, 1))[None, ...]                    # 1,3,H,W
    x = (chw - _MEAN) / _STD

    input_name = _get_session.input_name
    output_name = _get_session.output_name
    out = sess.run([output_name], {input_name: x.astype(np.float32)})[0]     # 1,1,1024,1024

    mask = np.squeeze(out)                                                   # 1024,1024
    if mask.max() > 1.0 or mask.min() < 0.0:
        # 输出为 logits 时做 sigmoid
        mask = 1.0 / (1.0 + np.exp(-mask))

    # 缩回原图尺寸作为 alpha
    h, w = image.size[1], image.size[0]
    alpha = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    alpha = np.clip(alpha, 0.0, 1.0).astype(np.float32)
    return alpha


# ============================================================ 边缘精修 / 光影融合
# 对应 PRD《二、抠图与边缘融合规范》《三、背景色彩与光影规范》
#
# 旧实现直接把 BiRefNet 输出的 alpha 贴到纯色底上，产生三类问题：
#   1. alpha 由 1024 插值回原图，边缘带锯齿 → 发丝像被剪掉
#   2. 边缘 1~3px 是「前景 × 旧背景」的混合污染带 → 白边/黑边/色晕
#   3. 人物与背景无任何光影交互 → 剪纸般漂浮在色块上
# 下面四步逐一解决。

# 腐蚀：向内收，割掉污染带
_EDGE_ERODE_RATIO = 0.0015
_EDGE_ERODE_MIN = 1
_EDGE_ERODE_MAX = 4
# 羽化：向外渐变，得到发丝级透明过渡而非二值切边
_EDGE_FEATHER_RATIO = 0.0012
_EDGE_FEATHER_MIN = 1.0
_EDGE_FEATHER_MAX = 3.0
# 环境光：过渡带混入背景色的强度（0.28 = 明显可见但不脏）
_AMBIENT_STRENGTH = 0.28
# 软投影
_SHADOW_OPACITY = 0.16
_SHADOW_BLUR_RATIO = 0.012
_SHADOW_BLUR_MIN = 6.0
_SHADOW_BLUR_MAX = 28.0
_SHADOW_OFFSET_RATIO = 0.004


def _box_filter(src: np.ndarray, radius: int) -> np.ndarray:
    """O(1) 均值滤波（引导滤波基元）."""
    import cv2
    k = radius * 2 + 1
    return cv2.boxFilter(src, ddepth=-1, ksize=(k, k), normalize=True,
                         borderType=cv2.BORDER_REFLECT)


def _guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """引导滤波：以原图结构引导 alpha，保发丝、去插值锯齿."""
    mean_i = _box_filter(guide, radius)
    mean_p = _box_filter(src, radius)
    var_i = _box_filter(guide * guide, radius) - mean_i * mean_i
    cov_ip = _box_filter(guide * src, radius) - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    return _box_filter(a, radius) * guide + _box_filter(b, radius)


def _refine_alpha(alpha: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Alpha 边缘精修三步（PRD 二.发丝级抠图 / 颈部肩部过渡）.

    1. 引导滤波：以原图灰度为导向，把 1024→原图的插值锯齿贴合真实轮廓
    2. 形态学腐蚀：向内收 1~4px，割掉「前景与旧背景混合」的污染带
    3. 高斯羽化：向外 1~3px 渐变，得到发丝级透明过渡
    """
    import cv2

    h, w = alpha.shape[:2]
    short = min(h, w)

    gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    g_radius = max(2, int(round(short * 0.006)))
    a_ref = np.clip(_guided_filter(gray, alpha.astype(np.float32), g_radius, 1e-4), 0.0, 1.0)

    erode_px = max(_EDGE_ERODE_MIN, min(_EDGE_ERODE_MAX, int(round(short * _EDGE_ERODE_RATIO))))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1,) * 2)
    a_ref = cv2.erode(a_ref, k, iterations=1)

    feather_px = float(min(_EDGE_FEATHER_MAX, max(_EDGE_FEATHER_MIN, short * _EDGE_FEATHER_RATIO)))
    a_ref = cv2.GaussianBlur(a_ref, (0, 0), feather_px)
    return np.clip(a_ref, 0.0, 1.0).astype(np.float32)


def _blend_ambient(rgb: np.ndarray, alpha: np.ndarray, target_rgb) -> np.ndarray:
    """边缘环境光融合（PRD 二.衣物处理 / 三.环境光统一）.

    在 alpha 过渡带（0<α<1）混入目标背景色，模拟环境光在颈部/肩部/发丝
    边缘的微弱反射。既抑制旧背景残留的溢色（蓝底发白、红底发暗），
    又让人物不再是「贴在色块上的剪纸」。
    """
    band = np.clip(alpha * (1.0 - alpha) * 4.0, 0.0, 1.0)[..., None]
    ambient = band * _AMBIENT_STRENGTH
    target = np.array(target_rgb, dtype=np.float32).reshape(1, 1, 3)
    return rgb.astype(np.float32) * (1.0 - ambient) + target * ambient


def _drop_shadow(alpha: np.ndarray) -> np.ndarray:
    """人物背后的软投影（PRD 三.投影与层次）.

    复用人物 alpha 轮廓 → 大幅高斯模糊 → 下移几像素，
    得到贴合轮廓的自然投影（比硬椭圆真实得多），产生空间纵深。
    """
    import cv2

    h, w = alpha.shape[:2]
    short = min(h, w)
    blur = float(min(_SHADOW_BLUR_MAX, max(_SHADOW_BLUR_MIN, short * _SHADOW_BLUR_RATIO)))
    sh = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), blur)

    dy = int(round(short * _SHADOW_OFFSET_RATIO))
    if dy > 0:
        sh = np.roll(sh, dy, axis=0)
        sh[:dy, :] = 0.0          # 清掉 roll 的回绕部分
    return np.clip(sh * _SHADOW_OPACITY, 0.0, 1.0)


def replace_background(
    image: Image.Image,
    bg_color: str,
    *,
    refine_edge: bool = True,
    ambient: bool = True,
    shadow: bool = True,
) -> Image.Image:
    """AI 抠图后合成纯色背景（BiRefNet + 边缘精修 + 光影融合）.

    处理链（对应 PRD 二/三）:
        BiRefNet → alpha 引导滤波 → 腐蚀去污染带 → 羽化发丝过渡
        → 环境光融合（去色晕/防漂浮）→ 人物软投影 → 纯色背景合成

    - bg_color: 'keep' 跳过（保持原背景）
    - 其他值从 BG_COLOR_MAP 取色
    返回合成后的 RGB 图片.
    """
    if bg_color == "keep":
        logger.debug("Background kept as-is.")
        return image.convert("RGB") if image.mode != "RGB" else image

    if bg_color not in BG_COLOR_MAP:
        logger.warning(f"Unknown bg_color '{bg_color}', defaulting to white.")
        bg_color = "white"

    target_rgb = BG_COLOR_MAP[bg_color]

    alpha = _run_birefnet(image)
    rgb = np.array(image.convert("RGB"), dtype=np.float32)

    # 1) 边缘精修：去锯齿、去污染带、发丝级羽化
    if refine_edge:
        alpha = _refine_alpha(alpha, rgb)

    # 2) 环境光融合：过渡带混入背景色
    fg = _blend_ambient(rgb, alpha, target_rgb) if ambient else rgb

    # 3) 纯色背景
    h, w = alpha.shape[:2]
    bg = np.empty((h, w, 3), dtype=np.float32)
    bg[...] = np.array(target_rgb, dtype=np.float32)

    # 4) 软投影先落在背景上（随后被人物遮住大部分，仅在底部/两侧露出）
    if shadow:
        sa = _drop_shadow(alpha)[..., None]
        bg = bg * (1.0 - sa)

    # 5) 合成
    a3 = alpha[..., None]
    out = np.clip(fg * a3 + bg * (1.0 - a3), 0.0, 255.0).astype(np.uint8)

    edge_px = int(np.sum((alpha > 0.02) & (alpha < 0.98)))
    logger.info(
        f"Background replaced (BiRefNet + edge refine): {bg_color} RGB={target_rgb} "
        f"edge_px={edge_px} ambient={ambient} shadow={shadow}"
    )
    return Image.fromarray(out)
