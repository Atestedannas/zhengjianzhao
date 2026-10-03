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
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np
from PIL import Image
from loguru import logger

from app.config import settings

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
# 轻量兜底模型：输入 320x320，单次推理峰值只有几百 MB
LITE_MODEL_FILE = "u2net_human_seg.onnx"

# 每个引擎的「模型文件 → 推理输入边长」。
# 注意：两个模型的输入形状都是导出时写死的（BiRefNet 1024 / u2net 320），
# 不能靠改代码降分辨率，所以内存不够时唯一的办法是换轻量模型。
_ENGINE_MODELS = {
    "birefnet": (BIRefNET_MODEL_FILE, 1024),
    "u2net": (LITE_MODEL_FILE, 320),
}

# ImageNet 归一化参数（BiRefNet 与 u2net_human_seg 用的是同一套）
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)


@dataclass
class _ModelSession:
    """一个已加载的 onnxruntime session 及其输入/输出元信息.

    不能像以前那样把 input_name/output_name 挂到 _get_session 函数对象上 ——
    现在缓存里同时会有两个模型，挂函数属性会互相覆盖。
    """
    path: str
    engine: str
    size: int
    sess: object
    input_name: str
    output_name: str


def _intra_op_threads() -> int:
    """算子内并行线程数：限制线程数可以同时压低峰值内存与 CPU 争抢."""
    configured = int(getattr(settings, "ORT_INTRA_OP_THREADS", 0) or 0)
    if configured > 0:
        return configured
    return max(1, min(4, os.cpu_count() or 2))


def available_memory_mb() -> Optional[int]:
    """读取宿主机可用内存（MB）；非 Linux 或读不到时返回 None.

    容器没有设 memory limit 时 /proc/meminfo 反映的就是宿主机内存，
    这正是内核 OOM killer 判断的依据。
    """
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def resolve_engine(available_mb: Optional[int] = None) -> str:
    """决定本次抠图用哪个引擎.

    auto 模式下可用内存低于阈值就用轻量模型 —— 宁可质量略降，
    也不能让一个 3GB+ 的推理把整台小机器拖进 OOM。

    available_mb 仅用于测试注入；不传时读宿主机 /proc/meminfo。
    读不到（非 Linux）时按 BiRefNet 处理，保持改造前的行为。
    """
    mode = (getattr(settings, "BG_ENGINE", "auto") or "auto").strip().lower()
    if mode in ("birefnet", "u2net", "off"):
        return mode
    if mode != "auto":
        logger.warning(f"BG_ENGINE='{mode}' 不是合法值，按 auto 处理")

    threshold = int(getattr(settings, "BIREFNET_MIN_AVAILABLE_MB", 3600) or 3600)
    avail = available_memory_mb() if available_mb is None else available_mb
    if avail is None or avail >= threshold:
        return "birefnet"

    logger.warning(
        f"可用内存 {avail}MB < 阈值 {threshold}MB，抠图自动降级为轻量模型 "
        f"{LITE_MODEL_FILE}（320x320）以避免 OOM；升配内存后会恢复 BiRefNet"
    )
    return "u2net"


def _model_candidates(model_name: str) -> list:
    """返回模型候选搜索路径（项目 models 目录 / 容器内 /app/models / rembg 下载目录）.

    统一按「去掉扩展名再补 .onnx」处理：调用方传带不带 .onnx 都能命中，
    否则会出现 ~/.u2net/u2net_human_seg.onnx.onnx 这种找不到的路径。
    """
    stem = model_name[:-5] if model_name.endswith(".onnx") else model_name
    backend_models = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "models",
    )
    return [
        os.path.join(backend_models, f"{stem}.onnx"),          # backend/models/（本地开发）
        os.path.join("/app/models", f"{stem}.onnx"),           # 容器内模型目录
        os.path.expanduser(f"~/.u2net/{stem}.onnx"),           # Dockerfile COPY 的位置
        os.path.join("/root/.u2net", f"{stem}.onnx"),
        os.path.join(os.getcwd(), f"{stem}.onnx"),
        os.path.join(os.getcwd(), "models", f"{stem}.onnx"),
    ]


def _resolve_model_path(model_name: str):
    """按候选路径查找模型文件，返回存在的第一个绝对路径，找不到返回 None."""
    return next((p for p in _model_candidates(model_name) if os.path.exists(p)), None)


@lru_cache(maxsize=4)
def _get_session(model_path: str, engine: str, size: int) -> _ModelSession:
    """模块级缓存 onnxruntime session（session 可并发复用）."""
    import onnxruntime as ort

    logger.info(f"Loading {engine} model session from: {model_path} (input {size}x{size})")
    # 动态选择可用 provider（CPU 兜底，有 GPU 自动加速）
    available = set(ort.get_available_providers())
    providers = [p for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider", "CPUExecutionProvider")
                 if p in available]
    if not providers:
        providers = ["CPUExecutionProvider"]

    opts = ort.SessionOptions()
    # 【内存治理关键项】关掉 CPU memory arena：
    # 默认开启时推理过程中分配过的峰值内存会被 arena 长期缓存、不还给操作系统，
    # 在 4GB 以下的小机器上会把进程直接撑到被内核 OOM 杀掉（gunicorn SIGKILL → nginx 502）。
    opts.enable_cpu_mem_arena = False
    opts.enable_mem_pattern = False
    opts.intra_op_num_threads = _intra_op_threads()
    try:
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    except AttributeError:  # 老版本 onnxruntime 没有该枚举
        pass

    sess = ort.InferenceSession(model_path, sess_options=opts, providers=providers)
    info = _ModelSession(
        path=model_path,
        engine=engine,
        size=size,
        sess=sess,
        input_name=sess.get_inputs()[0].name,
        output_name=sess.get_outputs()[0].name,
    )
    logger.info(
        f"{engine} model ready: input={info.input_name} output={info.output_name} "
        f"threads={opts.intra_op_num_threads} arena=off"
    )
    return info


def _infer_alpha(image: Image.Image, engine: str) -> np.ndarray:
    """用指定引擎推理前景 alpha 蒙版.

    返回:
        np.ndarray: float32, 0~1, 尺寸与原图一致 (H, W)，1=前景 0=背景.
    """
    import cv2

    model_file, size = _ENGINE_MODELS[engine]
    model_path = _resolve_model_path(model_file)
    if model_path is None:
        raise FileNotFoundError(
            f"Model file not found. Looked in: {_model_candidates(model_file)}. "
            f"Please place {model_file} in backend/models/."
        )

    info = _get_session(model_path, engine, size)

    # 预处理：RGB 归一化 → resize 到模型输入边长
    # （两个模型的输入形状都是导出时写死的，喂错尺寸 onnxruntime 会直接报错）
    rgb = np.array(image.convert("RGB"), dtype=np.float32) / 255.0          # HWC
    rgb_resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_LINEAR)
    chw = np.transpose(rgb_resized, (2, 0, 1))[None, ...]                    # 1,3,H,W
    x = np.ascontiguousarray((chw - _MEAN) / _STD, dtype=np.float32)

    # 只取第一个输出（u2net 家族的第 0 个输出就是融合后的 d0，
    # 其余 d1~d6 用不到，onnxruntime 会按需裁剪不参与计算）
    out = info.sess.run([info.output_name], {info.input_name: x})[0]         # 1,1,size,size

    mask = np.squeeze(out)                                                   # size,size
    if mask.max() > 1.0 or mask.min() < 0.0:
        # 输出为 logits 时做 sigmoid
        mask = 1.0 / (1.0 + np.exp(-mask))

    # 缩回原图尺寸作为 alpha
    h, w = image.size[1], image.size[0]
    alpha = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    alpha = np.clip(alpha, 0.0, 1.0).astype(np.float32)

    # 显式释放中间张量：小内存机器上这几百 MB 回来的很及时
    del x, chw, rgb_resized, rgb, out, mask
    return alpha


def _run_alpha(image: Image.Image) -> np.ndarray:
    """按配置与当前可用内存选择抠图引擎（auto 模式下内存不足自动降级）."""
    return _infer_alpha(image, resolve_engine())


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
    - 抠图引擎由 settings.BG_ENGINE 决定（auto 时按可用内存自动降级，见 resolve_engine）
    返回合成后的 RGB 图片.
    """
    if bg_color == "keep":
        logger.debug("Background kept as-is.")
        return image.convert("RGB") if image.mode != "RGB" else image

    if bg_color not in BG_COLOR_MAP:
        logger.warning(f"Unknown bg_color '{bg_color}', defaulting to white.")
        bg_color = "white"

    engine = resolve_engine()
    if engine == "off":
        logger.warning(f"BG_ENGINE=off：跳过 AI 抠图，保留原背景（bg_color={bg_color}）")
        return image.convert("RGB") if image.mode != "RGB" else image

    target_rgb = BG_COLOR_MAP[bg_color]

    alpha = _infer_alpha(image, engine)
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
        f"Background replaced (engine={engine} + edge refine): {bg_color} RGB={target_rgb} "
        f"edge_px={edge_px} ambient={ambient} shadow={shadow}"
    )
    return Image.fromarray(out)
