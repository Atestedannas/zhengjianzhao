"""美颜模块 — MediaPipe FaceLandmarker (478 关键点) + 频率分离 + 引导滤波.

【为什么要重写】
旧实现有三个硬伤，直接导致“美颜效果很差、出图不理想”：

1. 滤波半径写死成固定像素（双边滤波 d=5~9、高斯 sigma=2.8）。
   手机原图动辄 3000~4000px，这个半径在整图尺度上几乎等于没做；
   而在 300px 的小图上又会糊成一片。
   → 现在所有半径都按“人脸尺寸（瞳距）”换算，同一张脸在任何分辨率下
     得到一致的观感，美颜效果不会因为原图尺寸而丢失。
2. 只做了“平滑”，没做“匀肤”。
   皮肤显脏的主因是**色度不均**（红斑、痘印、肤色斑块），不是亮度噪声。
   → 现在把亮度层与色度层分开：亮度层做频率分离磨皮，
     色度层（LAB 的 a/b）单独做大幅匀肤，这是“像美颜”而不是“像模糊”的关键。
3. 蒙版只有几何多边形（旧代码注释说“略向内收缩”，实际并没有收缩）。
   头发丝、眉毛、眉毛阴影、眼镜框会被一起磨掉，观感是“脸糊、边缘脏”。
   → 现在几何蒙版 ∩ 肤色概率蒙版 ∩ 非暗部 ∩ 非唇部，
     并按图像梯度做引导滤波羽化，只作用于真正的皮肤。

另外旧实现在 mediapipe 不可用（未安装/模型缺失）时直接 `return image`，
用户看到的是“完全没效果”。现在会降级到 Haar 人脸框 + 肤色蒙版继续美颜。

【处理链】
    几何/肤色蒙版 → 亮度频率分离（高频毛孔 + 中频瑕疵）→ 去油光
    → 提亮 → 通透感回填 → 眼部锐化 → 色度匀肤 → 按羽化蒙版回贴原图

【模型】
    backend/models/face_landmarker.task        (MediaPipe, Apache-2.0, 本地离线)
    backend/models/face_parsing_segformer.onnx (SegFormer-mit-b5, 19类人脸解析, 可选)

蒙版来源优先级：人脸解析（精确皮肤/五官） > 几何多边形 ∩ 肤色概率（旧降级路径）。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image
from loguru import logger


class BeautyLevel:
    """美颜等级.

    【证件照合规约束 — 见 docs/IMAGE_QUALITY_STANDARD.md】
    对外出图只允许 OFF(0) 与 COMPLIANT(1) 两档：
      * COMPLIANT 即「自然微调」，参数被 _clamp_compliant() 硬性锁死在
        公安/政务证件照红线内（磨皮 ≤20%、保留 ≥85% 原生纹理、不改骨相）。
      * LIGHT / MEDIUM / HIGH 降级为**内部对比测试**档位，真实出图时
        同样会被 _clamp_compliant() 压回合规上限，不会产生「塑料脸」。

    枚举数值保持不变（OFF=0,LIGHT=1,MEDIUM=2,HIGH=3），
    以兼容既有 API 请求体与数据库里存的历史值。
    """
    OFF = 0          # 关闭（原图直出）
    COMPLIANT = 1    # ★ 自然微调：唯一对外推荐档位（= 数值 1，别名 LIGHT）
    LIGHT = 1        # 兼容旧命名，语义等同 COMPLIANT
    MEDIUM = 2       # 内部测试档（会被红线钳制）
    HIGH = 3         # 内部测试档（会被红线钳制）


# ============================================================ 强度参数

@dataclass(frozen=True)
class _Params:
    """单等级的全部强度参数（0~1，除 gamma 外）."""
    smooth: float      # 高频（毛孔/噪点）压制强度
    fine_keep: float   # 高频保留比例下限（防止塑料脸）
    blemish: float     # 中频（斑点/痘印/色块）压制强度
    chroma: float      # 色度匀肤强度
    shine: float       # 去油光强度
    brighten: float    # 提亮强度
    gamma: float       # 提亮曲线（越小越亮）
    clarity: float     # 通透感（中低频对比回填）
    sharpen: float     # 眼部锐化强度


# ---- 证件照美颜红线（PRD 强制上限，任何档位都不得突破）----
COMPLIANT_MAX_SMOOTH = 0.20     # 磨皮力度 ≤ 20%，保留皮肤原生纹理
COMPLIANT_MAX_BLEMISH = 0.20    # 仅去临时性痘印/红肿，保留痣与特征
COMPLIANT_MAX_CHROMA = 0.15     # 肤色均匀化（仅提亮匀肤，不改色相）
COMPLIANT_MAX_BRIGHTEN = 0.15   # 提亮 ≤ 15%
COMPLIANT_MIN_FINE_KEEP = 0.85  # 原生纹理保留 ≥ 85%（禁止涂抹感）
COMPLIANT_MAX_SHINE = 0.12      # 去油光仅压镜面高光
COMPLIANT_MAX_CLARITY = 0.06    # 通透感回填，防糊平
COMPLIANT_MAX_SHARPEN = 0.08    # 眼神光级微锐化，禁止改变五官立体光影

_PARAMS = {
    # ★ 自然微调（默认出图档）：全部落在红线之内
    BeautyLevel.COMPLIANT: _Params(0.18, 0.88, 0.12, 0.10, 0.08, 0.08, 0.97, 0.03, 0.05),
    # 内部对比测试档（会被 _clamp_compliant 压回上限，仅用于美学回归测试）
    BeautyLevel.MEDIUM:    _Params(0.78, 0.44, 0.68, 0.62, 0.55, 0.46, 0.85, 0.13, 0.22),
    BeautyLevel.HIGH:      _Params(0.92, 0.30, 0.85, 0.78, 0.72, 0.60, 0.78, 0.18, 0.30),
}


def _clamp_compliant(para: _Params) -> _Params:
    """把任意档位参数钳制到证件照红线上限（幂等，可重复调用）.

    这是「美颜不得改变人脸识别特征」的最后一道工程保险：
    即使上游误传 MEDIUM/HIGH，或 _PARAMS 表被改坏，
    经此函数后 smooth/blemish/chroma/brighten 必然 ≤ 红线，
    fine_keep 必然 ≥ 0.85，sharpen 必然 ≤ 0.08。
    """
    return _Params(
        smooth=min(float(para.smooth), COMPLIANT_MAX_SMOOTH),
        fine_keep=max(float(para.fine_keep), COMPLIANT_MIN_FINE_KEEP),
        blemish=min(float(para.blemish), COMPLIANT_MAX_BLEMISH),
        chroma=min(float(para.chroma), COMPLIANT_MAX_CHROMA),
        shine=min(float(para.shine), COMPLIANT_MAX_SHINE),
        brighten=min(float(para.brighten), COMPLIANT_MAX_BRIGHTEN),
        # gamma 越大越暗，合规档只允许「轻微提亮」，故 gamma 不得低于 0.97
        gamma=max(float(para.gamma), 0.97),
        clarity=min(float(para.clarity), COMPLIANT_MAX_CLARITY),
        sharpen=min(float(para.sharpen), COMPLIANT_MAX_SHARPEN),
    )


# 引导滤波的方差阈值（guide 取值 0~255，阈值 ≈ (可平滑的亮度差 std)^2）
_EPS_FINE = 90.0     # ≈ std 9.5，小于它的起伏（毛孔/噪点）会被抹平
_EPS_MID = 320.0     # ≈ std 17.9，保留面部明暗结构
_EPS_CLARITY = 500.0

_MAX_FACES = 4
_ENABLE_NECK = True  # 是否把下颌→脖子也纳入美颜（避免脸白脖子黄的接缝）


# ============================================================ 人脸解析 (SegFormer)
# 19 类人脸解析（SegFormer-mit-b5，CelebAMask-HQ 微调，ONNX 量化版，约 85MB）。
# 相比 BiSeNet：同是 19 类人脸解析，但 HuggingFace 上有现成 ONNX 可直接加载，
# 无需本地装 torch 转换；精度（mit-b5 骨干）也优于 BiSeNet-resnet18。
# 类别顺序与 zllrunning/BiSeNet 不同，见下方 _PARSE_SKIN_CLASSES。

_PARSE_MODEL_FILE = "face_parsing_segformer.onnx"
_PARSE_INPUT = 512                                   # 短边缩放到 512 后中心裁 512
_PARSE_SKIN_CLASSES = (1, 2, 16, 17)                 # skin / nose / neck_l / neck
_PARSE_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_PARSE_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_PARSE_DISABLED = False                              # 模型缺失/加载失败后永久降级


def _parse_model_path() -> str:
    """返回 face_parsing_segformer.onnx 的绝对路径（backend/models/ 下）."""
    global _MODELS_DIR
    if _MODELS_DIR is None:
        _MODELS_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "models",
        )
    return os.path.join(_MODELS_DIR, _PARSE_MODEL_FILE)


@lru_cache(maxsize=1)
def _get_parse_session():
    """人脸解析 ONNX 会话单例（onnxruntime session 可并发复用）."""
    import onnxruntime as ort

    path = _parse_model_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"face parsing model not found at {path}; beautify falls back to "
            f"geometric + skin-color mask."
        )
    available = set(ort.get_available_providers())
    providers = [p for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider", "CPUExecutionProvider")
                 if p in available]
    if not providers:
        providers = ["CPUExecutionProvider"]
    sess = ort.InferenceSession(path, providers=providers)
    _get_parse_session.input_name = sess.get_inputs()[0].name
    _get_parse_session.output_name = sess.get_outputs()[0].name
    logger.info(f"Face parsing model ready (SegFormer-mit-b5, 19 classes): {path}")
    return sess


def _parse_skin_mask(bgr: np.ndarray) -> Optional[np.ndarray]:
    """
    用 19 类人脸解析得到精确皮肤蒙版 (0~1，与原图同尺寸).

    皮肤 = skin/nose/neck 类；唇/眼/眉/发/衣服天然不在集合内，
    因此能比「几何多边形 ∩ 肤色概率」更精确地保护唇色与五官边缘。

    任何失败（缺模型 / 缺 onnxruntime / 推理异常）返回 None，调用方降级到旧蒙版.
    """
    import cv2

    global _PARSE_DISABLED
    if _PARSE_DISABLED:
        return None

    try:
        sess = _get_parse_session()
    except Exception as e:
        _PARSE_DISABLED = True
        logger.warning(f"Face parsing disabled ({e}); using skin-color mask fallback.")
        return None

    h, w = bgr.shape[:2]
    try:
        rgb = bgr[:, :, ::-1]
        # 预处理与 SegFormer 预处理器一致：短边 512 → 中心裁 512x512 → ImageNet 归一化
        scale = float(_PARSE_INPUT) / float(min(h, w))
        nw, nh = int(round(w * scale)), int(round(h * scale))
        big = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        cy, cx = nh // 2, nw // 2
        half = _PARSE_INPUT // 2
        crop = big[cy - half: cy + half, cx - half: cx + half]
        x = (crop / 255.0 - _PARSE_MEAN) / _PARSE_STD
        x = np.transpose(x, (2, 0, 1))[None].astype(np.float32)

        out = sess.run([_get_parse_session.output_name],
                       {_get_parse_session.input_name: x})[0]   # 1,19,128,128
        labels = np.argmax(out, axis=1)[0]                       # 128,128
        skin = np.isin(labels, _PARSE_SKIN_CLASSES).astype(np.float32)
        mask = cv2.resize(skin, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.clip(mask, 0.0, 1.0).astype(np.float32)
    except Exception as e:
        logger.warning(f"Face parsing inference failed ({e}); using skin-color mask fallback.")
        return None


# ============================================================ FaceLandmarker

_LANDMARKER = None
_LANDMARKER_DISABLED = False
_DETECT_LOCK = threading.Lock()
_MODELS_DIR: Optional[str] = None


def _model_path() -> str:
    """返回 face_landmarker.task 的绝对路径（backend/models/ 下）."""
    global _MODELS_DIR
    if _MODELS_DIR is None:
        _MODELS_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "models",
        )
    return os.path.join(_MODELS_DIR, "face_landmarker.task")


def _get_landmarker():
    """模块级单例 FaceLandmarker；不可用时只告警一次并永久降级."""
    global _LANDMARKER, _LANDMARKER_DISABLED
    if _LANDMARKER is not None:
        return _LANDMARKER
    if _LANDMARKER_DISABLED:
        return None
    try:
        import mediapipe as mp  # noqa: F401
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        path = _model_path()
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"face_landmarker.task not found at {path}. "
                f"Please place the model in backend/models/."
            )
        # 用内存缓冲加载（MediaPipe C++ 后端无法打开含非 ASCII 字符的路径）
        with open(path, "rb") as f:
            model_buffer = f.read()
        base_options = mp_python.BaseOptions(model_asset_buffer=model_buffer)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_faces=_MAX_FACES,
            min_face_detection_confidence=0.4,
            min_face_presence_confidence=0.4,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        _LANDMARKER = vision.FaceLandmarker.create_from_options(options)
        logger.info("MediaPipe FaceLandmarker ready (478 landmarks).")
    except Exception as e:  # 缺包 / 缺模型 / 版本不兼容 → 永久降级
        _LANDMARKER_DISABLED = True
        logger.warning(
            f"MediaPipe FaceLandmarker unavailable ({e}); "
            f"beautify degrades to Haar face box + skin-color mask."
        )
    return _LANDMARKER


# MediaPipe FaceMesh 关键点索引（468 点体系，478 点模型额外含虹膜 468~477）
_FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365,
              379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93,
              234, 127, 162, 21, 54, 103, 67, 109]
_LEFT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
_RIGHT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
_LEFT_EYEBROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
_RIGHT_EYEBROW = [336, 296, 334, 293, 300, 276, 283, 282, 295, 285]
_LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
_NOSTRILS = [97, 328]          # 左右鼻孔中心
_CHIN = 152
_JAW_LEFT = 234
_JAW_RIGHT = 454


def _detect_all_landmarks(image: np.ndarray) -> List[list]:
    """
    检测所有人脸关键点（BGR 输入）.

    返回:
        按脸面积降序排列的关键点列表；无人脸或模型不可用返回 [].
    """
    landmarker = _get_landmarker()
    if landmarker is None:
        return []
    try:
        import mediapipe as mp

        rgb = np.ascontiguousarray(image[:, :, ::-1])
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        # FaceLandmarker 实例非线程安全，服务端并发请求需要串行化
        with _DETECT_LOCK:
            res = landmarker.detect(mp_image)
    except Exception as e:
        logger.warning(f"Face landmark detection failed: {e}")
        return []

    if not res.face_landmarks:
        return []
    faces = list(res.face_landmarks)
    faces.sort(key=_landmark_span, reverse=True)
    return faces[:_MAX_FACES]


def _landmark_span(landmarks) -> float:
    """关键点包络面积（用于挑最大脸）."""
    xs = [lm.x for lm in landmarks]
    ys = [lm.y for lm in landmarks]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


# ============================================================ 基础算子

def _ramp(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """线性软阈值：x<=lo → 0，x>=hi → 1，中间线性过渡."""
    if hi <= lo:
        return (x >= hi).astype(np.float32)
    return np.clip((x - lo) / float(hi - lo), 0.0, 1.0).astype(np.float32)


def _box(img: np.ndarray, radius: int) -> np.ndarray:
    """盒式均值滤波（引导滤波的 O(1) 均值算子）."""
    import cv2

    r = max(1, int(radius))
    return cv2.boxFilter(img, -1, (2 * r + 1, 2 * r + 1),
                         normalize=True, borderType=cv2.BORDER_REFLECT101)


def _guided_1(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """
    灰度引导滤波（He et al.）.

    guide 为单通道 float32；src 可以是单通道，也可以是多通道（逐通道输出）.
    相比双边滤波：不会出现梯度反转/油画感，代价与半径无关（O(1)/像素），
    因此很适合在大图上用“人脸尺度”的大半径做保边平滑.
    """
    multi = src.ndim == 3
    g = guide[..., None] if multi else guide

    mean_i = _box(guide, radius)
    mean_i = mean_i[..., None] if multi else mean_i
    mean_p = _box(src, radius)
    corr_i = _box(guide * guide, radius)
    corr_i = corr_i[..., None] if multi else corr_i
    corr_ip = _box(g * src, radius)

    var_i = corr_i - mean_i * mean_i
    cov_ip = corr_ip - mean_i * mean_p
    a = cov_ip / (var_i + float(eps))
    b = mean_p - a * mean_i
    return _box(a, radius) * g + _box(b, radius)


def _fast_blur(img: np.ndarray, radius: int) -> np.ndarray:
    """
    大半径快速模糊（先降采样再模糊再升采样）.

    色度匀肤 / 蒙版柔化只需要低频结果，用金字塔近似即可，
    比在高分辨率上跑 sigma=100+ 的高斯快一个数量级.
    """
    import cv2

    radius = max(1, int(radius))
    h, w = img.shape[:2]
    step = 1
    while step < 16 and radius / step > 6:
        step *= 2
    if step == 1:
        return cv2.blur(img, (2 * radius + 1, 2 * radius + 1))

    sh, sw = max(1, h // step), max(1, w // step)
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    r_small = max(1, int(round(radius / step)))
    small = cv2.blur(small, (2 * r_small + 1, 2 * r_small + 1))
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _ellipse_kernel(radius: int) -> np.ndarray:
    import cv2

    r = max(1, int(radius))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def _scale(iod: float, ratio: float, lo: int = 1) -> int:
    """按瞳距把“比例”换算成像素半径."""
    return int(max(lo, min(256, round(float(iod) * float(ratio)))))


def _pts(landmarks, indices: Sequence[int], w: int, h: int) -> np.ndarray:
    """关键点索引 → 像素坐标数组 (N,2)，自动跳过越界索引."""
    pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in indices if i < len(landmarks)]
    return np.array(pts, dtype=np.float32)


def _pil_to_cv2(image: Image.Image) -> np.ndarray:
    """PIL Image → OpenCV BGR numpy array."""
    return np.array(image.convert("RGB"))[:, :, ::-1].copy()


def _cv2_to_pil(img: np.ndarray) -> Image.Image:
    """OpenCV BGR numpy array → PIL Image."""
    return Image.fromarray(img[:, :, ::-1])


# ============================================================ 人脸几何

@dataclass
class _FaceGeometry:
    """用于把比例换算成像素尺度的人脸几何量."""
    cx: float
    cy: float
    width: float
    height: float
    iod: float  # 瞳距（尺度基准）


def _geometry_from_landmarks(landmarks, w: int, h: int) -> _FaceGeometry:
    oval = _pts(landmarks, _FACE_OVAL, w, h)
    if len(oval) < 3:
        return _FaceGeometry(w / 2.0, h / 2.0, w * 0.3, h * 0.3, max(8.0, w * 0.1))

    x0, y0 = oval.min(axis=0)
    x1, y1 = oval.max(axis=0)

    left_eye = _pts(landmarks, _LEFT_EYE, w, h)
    right_eye = _pts(landmarks, _RIGHT_EYE, w, h)
    iod = 0.0
    if len(left_eye) and len(right_eye):
        iod = float(np.linalg.norm(left_eye.mean(axis=0) - right_eye.mean(axis=0)))
    if iod < 1.0:  # 兜底：瞳距 ≈ 脸宽 1/3
        iod = max(1.0, float(x1 - x0) * 0.33)

    return _FaceGeometry(
        cx=float((x0 + x1) / 2.0), cy=float((y0 + y1) / 2.0),
        width=float(x1 - x0), height=float(y1 - y0), iod=iod,
    )


def _large_geometry(geometries: Sequence[_FaceGeometry]) -> _FaceGeometry:
    return max(geometries, key=lambda g: g.width * g.height)


# ============================================================ 蒙版构建

def _skin_prob(bgr: np.ndarray) -> np.ndarray:
    """
    肤色概率图 (0~1)：YCbCr 软阈值.

    用软阈值而不是硬阈值，是为了让蒙版边缘本来就有过渡，
    再叠加引导滤波羽化后不会出现明显的“美颜边界”.
    """
    import cv2

    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    y = ycrcb[:, :, 0].astype(np.float32)
    cr = ycrcb[:, :, 1].astype(np.float32)
    cb = ycrcb[:, :, 2].astype(np.float32)

    p = _ramp(cr, 130.0, 141.0) * (1.0 - _ramp(cr, 172.0, 188.0))
    p = p * _ramp(cb, 72.0, 84.0) * (1.0 - _ramp(cb, 126.0, 140.0))
    p = p * _ramp(y, 30.0, 55.0)  # 过暗（头发/阴影）不算皮肤
    return p.astype(np.float32)


def _reference_skin_stats(lab: np.ndarray, region: np.ndarray,
                          color: np.ndarray) -> Tuple[float, float]:
    """统计人脸区域内的参考亮度/红度中位数（用于相对阈值）."""
    l_chan = lab[:, :, 0].astype(np.float32)
    a_chan = lab[:, :, 1].astype(np.float32)
    sel = (region > 0) & (color > 0.6)
    if int(sel.sum()) < 200:
        sel = region > 0
    if int(sel.sum()) < 50:
        return 140.0, 128.0
    return float(np.median(l_chan[sel])), float(np.median(a_chan[sel]))


def _refine_skin_mask(bgr: np.ndarray, region: np.ndarray,
                      geo: _FaceGeometry, lab: np.ndarray,
                      parse_skin: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """
    把人脸区域精炼成“只有皮肤”的羽化浮点蒙版 (0~1).

    优先用 parse_skin（19 类人脸解析得到的精确皮肤）∩ 几何人脸区域；
    解析不可用时降级为：外沿收缩 → ∩肤色概率 → 抠暗部（眼/眉/鼻孔/头发）
    → 抠唇部。两条路径最后都做形态学去碎点补空洞 + 引导滤波羽化.
    """
    import cv2

    if region is None or int(region.max()) == 0:
        return None

    if parse_skin is not None:
        # 解析路径：精确皮肤 ∩ 几何人脸区域（不再依赖肤色/亮度/唇部启发式）
        soft = (region.astype(np.float32) / 255.0) * parse_skin
    else:
        # 外沿收缩：避开头发与面部轮廓外
        region = cv2.erode(region, _ellipse_kernel(_scale(geo.iod, 0.06, 1)))

        color = _skin_prob(bgr)
        med_l, med_a = _reference_skin_stats(lab, region, color)

        l_chan = lab[:, :, 0].astype(np.float32)
        a_chan = lab[:, :, 1].astype(np.float32)

        # 相对亮度：明显暗于皮肤中位数的像素（眉毛/睫毛/眼窝/头发）排除
        lit = _ramp(l_chan, 0.34 * med_l, 0.55 * med_l)
        # 相对红度：明显偏红的像素（嘴唇/唇线）排除
        non_lip = 1.0 - _ramp(a_chan - med_a, 18.0, 38.0)

        soft = (region.astype(np.float32) / 255.0) * color * lit * non_lip

    m = (soft * 255.0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, _ellipse_kernel(_scale(geo.iod, 0.06, 1)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, _ellipse_kernel(_scale(geo.iod, 0.02, 1)))
    m = m.astype(np.float32) / 255.0

    if float(m.max()) < 0.05:
        return None

    # 按图像梯度羽化：过渡带贴着头皮/眉毛的真实边缘，而不是几何多边形的硬边
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    m = _guided_1(gray, m, _scale(geo.iod, 0.12, 2), 4e-4)
    return np.clip(m, 0.0, 1.0).astype(np.float32)


def _neck_polygon(landmarks, w: int, h: int, geo: _FaceGeometry) -> Optional[np.ndarray]:
    """下颌 → 脖子梯形区域（用肤色蒙版兜底，避免脸/脖子色差接缝）."""
    if not _ENABLE_NECK or len(landmarks) <= max(_JAW_LEFT, _JAW_RIGHT, _CHIN):
        return None

    chin = np.array([landmarks[_CHIN].x * w, landmarks[_CHIN].y * h], dtype=np.float32)
    jaw_l = np.array([landmarks[_JAW_LEFT].x * w, landmarks[_JAW_LEFT].y * h], dtype=np.float32)
    jaw_r = np.array([landmarks[_JAW_RIGHT].x * w, landmarks[_JAW_RIGHT].y * h], dtype=np.float32)

    jaw_w = float(np.linalg.norm(jaw_r - jaw_l))
    depth = min(h - 1.0 - chin[1], geo.iod * 1.7)
    if jaw_w < 4.0 or depth < geo.iod * 0.25:
        return None

    cx = float((jaw_l[0] + jaw_r[0]) / 2.0)
    half_top = jaw_w * 0.35
    half_bot = half_top * 1.15
    y0 = chin[1] - geo.iod * 0.06      # 与下巴重叠，避免出现未处理的细缝
    y1 = chin[1] + depth
    return np.array([
        [cx - half_top, y0], [cx + half_top, y0],
        [cx + half_bot, y1], [cx - half_bot, y1],
    ], dtype=np.int32)


def _landmark_region_mask(bgr: np.ndarray, faces: List[list],
                          parse_skin: Optional[np.ndarray] = None) -> Tuple[Optional[np.ndarray], _FaceGeometry]:
    """由关键点构建“待美颜区域”（含脖子）并抠除五官."""
    import cv2

    h, w = bgr.shape[:2]
    geometries = [_geometry_from_landmarks(lm, w, h) for lm in faces]
    main = _large_geometry(geometries)

    region = np.zeros((h, w), dtype=np.uint8)
    for lm, geo in zip(faces, geometries):
        oval = _pts(lm, _FACE_OVAL, w, h)
        if len(oval) >= 3:
            cv2.fillPoly(region, [oval.astype(np.int32)], 255)
        neck = _neck_polygon(lm, w, h, geo)
        if neck is not None:
            cv2.fillPoly(region, [neck], 255)

    # 五官保护：眼 / 眉 / 唇 / 鼻孔（整体膨胀，避免锯齿边缘被磨到）
    feat = np.zeros((h, w), dtype=np.uint8)
    for lm in faces:
        for idx in (_LEFT_EYE, _RIGHT_EYE, _LEFT_EYEBROW, _RIGHT_EYEBROW, _LIPS):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(feat, [pts.astype(np.int32)], 255)
    nostril_r = _scale(main.iod, 0.05, 2)
    for lm in faces:
        for i in _NOSTRILS:
            if i < len(lm):
                cv2.circle(feat, (int(lm[i].x * w), int(lm[i].y * h)), nostril_r, 255, -1)
    feat = cv2.dilate(feat, _ellipse_kernel(_scale(main.iod, 0.10, 2)))
    region[feat > 0] = 0

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    return _refine_skin_mask(bgr, region, main, lab, parse_skin), main


def _eye_region_mask(bgr: np.ndarray, faces: List[list], main: _FaceGeometry) -> Optional[np.ndarray]:
    """眼部（眼 + 眉）区域蒙版，用于单独锐化眼神."""
    import cv2

    h, w = bgr.shape[:2]
    m = np.zeros((h, w), dtype=np.uint8)
    for lm in faces:
        for idx in (_LEFT_EYE, _RIGHT_EYE, _LEFT_EYEBROW, _RIGHT_EYEBROW):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(m, [pts.astype(np.int32)], 255)

    if int(m.max()) == 0:
        return None
    m = cv2.dilate(m, _ellipse_kernel(_scale(main.iod, 0.05, 2)))
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, _scale(main.iod, 0.05, 2) * 0.6))
    return m.astype(np.float32) / 255.0


def _fallback_region(bgr: np.ndarray,
                     parse_skin: Optional[np.ndarray] = None) -> Tuple[Optional[np.ndarray], Optional[_FaceGeometry]]:
    """
    降级路径：无关键点时用 Haar 人脸框 + 肤色/暗部/唇部约束构造蒙版.

    这条路径保证“即使 mediapipe 挂了也还有美颜效果”，
    而不是像旧实现那样直接返回原图.
    """
    import cv2

    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return None, None

    min_size = max(24, min(h, w) // 20)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                     minSize=(min_size, min_size))
    if len(faces) == 0:
        return None, None

    x, y, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    x, y, fw, fh = int(x), int(y), int(fw), int(fh)
    if fw < min_size or fh < min_size:
        return None, None

    geo = _FaceGeometry(cx=x + fw / 2.0, cy=y + fh / 2.0, width=float(fw),
                        height=float(fh), iod=max(4.0, fw * 0.33))

    region = np.zeros((h, w), dtype=np.uint8)
    # Haar 框约等于“眉毛→下巴”，向上扩到额头，向下兜住下巴
    ex0, ey0 = int(x - 0.14 * fw), int(y - 0.45 * fh)
    ex1, ey1 = int(x + 1.14 * fw), int(y + 1.28 * fh)
    cv2.ellipse(region,
                ((ex0 + ex1) // 2, (ey0 + ey1) // 2),
                (max(3, (ex1 - ex0) // 2), max(3, (ey1 - ey0) // 2)),
                0, 0, 360, 255, -1)

    if _ENABLE_NECK:
        neck_top = y + fh * 0.92
        neck_bottom = min(h - 1.0, y + fh * 1.9)
        if neck_bottom - neck_top > fh * 0.2:
            half = fw * 0.36
            cx = x + fw / 2.0
            cv2.fillPoly(region, [np.array([
                [cx - half, neck_top], [cx + half, neck_top],
                [cx + half * 1.2, neck_bottom], [cx - half * 1.2, neck_bottom],
            ], dtype=np.int32)], 255)

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    return _refine_skin_mask(bgr, region, geo, lab, parse_skin), geo


# ============================================================ 美颜算子

def _retouch(bgr: np.ndarray, mask: np.ndarray, geo: _FaceGeometry, para: _Params,
             eye_mask: Optional[np.ndarray],
             do_smooth: bool, do_blemish: bool,
             do_brighten: bool, do_sharpen: bool) -> np.ndarray:
    """
    在 LAB 空间做实际的美颜运算，最后按羽化蒙版回贴原图.

    亮度层 L：频率分离磨皮（高频毛孔 + 中频瑕疵）→ 去油光 → 提亮 → 通透感
    色度层 a/b：大半径匀肤（去红斑/色块，不动亮度纹理）
    """
    import cv2

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan = lab[:, :, 0].astype(np.float32)
    a_chan = lab[:, :, 1].astype(np.float32) - 128.0
    b_chan = lab[:, :, 2].astype(np.float32) - 128.0

    r_fine = _scale(geo.iod, 0.075, 2)
    r_mid = max(r_fine + 2, _scale(geo.iod, 0.22, 4))

    base_fine = _guided_1(l_chan, l_chan, r_fine, _EPS_FINE)
    base_mid = _guided_1(l_chan, l_chan, r_mid, _EPS_MID)
    high = l_chan - base_fine    # 高频：毛孔 / 噪点
    mid = base_fine - base_mid   # 中频：斑点 / 痘印 / 色块

    # 皮肤本身就很干净时自动降档，避免“越磨越假”
    sel_skin = mask > 0.5
    if int(sel_skin.sum()) > 200:
        texture = float(np.mean(np.abs(high[sel_skin])))
    else:
        texture = 3.0
    factor = 1.0
    if texture < 2.0:
        factor = 0.68
    elif texture < 3.2:
        factor = 0.85

    smooth_w = (para.smooth * factor) if do_smooth else 0.0
    blemish_w = (para.blemish * factor) if do_blemish else 0.0
    chroma_w = (para.chroma * factor) if do_smooth else 0.0

    if smooth_w > 0.0:
        # 幅度门控：小起伏（毛孔）压掉，大起伏（褶皱/轮廓）保留
        gate = _ramp(np.abs(high), 2.5, 14.0)
        keep = para.fine_keep + (1.0 - para.fine_keep) * gate
        high = high * (1.0 - smooth_w * (1.0 - keep))

    if blemish_w > 0.0:
        gate = _ramp(np.abs(mid), 4.0, 26.0)
        mid = mid * (1.0 - blemish_w * (1.0 - gate))

    l_out = base_mid + high + mid

    # 去油光：压抑皮肤上的镜面高光（额头/鼻头反光）
    if do_brighten and para.shine > 0.0:
        if int(sel_skin.sum()) > 200:
            thr = max(185.0, float(np.percentile(l_out[sel_skin], 90)))
        else:
            thr = 195.0
        blob = _ramp(l_out, thr, thr + 24.0)
        blob = np.clip(_fast_blur(blob, _scale(geo.iod, 0.12, 2)), 0.0, 1.0)
        l_out = l_out - para.shine * blob * np.maximum(l_out - thr, 0.0)

    # 提亮：低斜率提亮曲线 + 高光保护（不追求“假白”）
    if do_brighten and para.brighten > 0.0:
        norm = np.clip(l_out, 0.0, 255.0) / 255.0
        lifted = np.power(norm, para.gamma)
        protect = _ramp(norm, 0.72, 0.96)
        lifted = lifted * (1.0 - protect) + norm * protect
        l_out = l_out + para.brighten * (lifted * 255.0 - l_out)

    # 通透感：把平滑底层的明暗结构回填一点，避免“糊平”
    if para.clarity > 0.0:
        low = _guided_1(base_mid, base_mid, _scale(geo.iod, 0.30, 4), _EPS_CLARITY)
        l_out = l_out + para.clarity * (base_mid - low)

    # 眼部锐化（只锐化眼睛/眉毛，让眼神更清晰）
    if do_sharpen and para.sharpen > 0.0 and eye_mask is not None:
        sigma = max(0.8, r_fine * 0.6)
        blurred = cv2.GaussianBlur(l_out, (0, 0), sigma)
        l_out = l_out + eye_mask * para.sharpen * (l_out - blurred)

    # 色度匀肤：LAB 的 a/b 大幅平滑，去掉红斑与肤色斑块
    if chroma_w > 0.0:
        r_chroma = _scale(geo.iod, 0.35, 4)
        a_chan = a_chan + chroma_w * (_fast_blur(a_chan, r_chroma) - a_chan)
        b_chan = b_chan + chroma_w * (_fast_blur(b_chan, r_chroma) - b_chan)

    out_lab = cv2.merge([
        np.clip(l_out, 0, 255).astype(np.uint8),
        np.clip(a_chan + 128.0, 0, 255).astype(np.uint8),
        np.clip(b_chan + 128.0, 0, 255).astype(np.uint8),
    ])
    retouched = cv2.cvtColor(out_lab, cv2.COLOR_LAB2BGR).astype(np.float32)

    m3 = mask[..., None]
    blended = bgr.astype(np.float32) * (1.0 - m3) + retouched * m3
    return np.clip(blended, 0.0, 255.0).astype(np.uint8)


# ============================================================ 对外接口

def apply_beautify(
    image: Image.Image,
    level: int = BeautyLevel.COMPLIANT,
    smooth: bool = True,
    brighten: bool = True,
    blemish: bool = True,
    sharpen: bool = True,
    compliant: bool = True,
) -> Image.Image:
    """
    对图片应用美颜处理（MediaPipe FaceLandmarker + 频率分离 + 引导滤波）.

    参数:
        image:     输入图片
        level:     0=OFF 1=自然微调(COMPLIANT) 2/3=内部测试档（会被红线钳制）
        smooth:    磨皮（高频）+ 色度匀肤
        brighten:  提亮 + 去油光
        blemish:   去瑕疵（中频斑点）
        sharpen:   眼部微锐化
        compliant: ★ 是否启用证件照合规红线（**默认 True**）。
                   True 时参数经 _clamp_compliant() 钳制，保证不改骨相、
                   保留 ≥85% 原生纹理；仅美学回归测试才应传 False。

    返回:
        PIL.Image 美颜后的图片；未检测到人脸时降级处理，
        完全无法处理时原样返回.
    """
    if level == BeautyLevel.OFF:
        logger.debug("Beautify is OFF, returning original.")
        return image
    if level not in _PARAMS:
        logger.warning(f"Unknown beautify level {level}, fallback to COMPLIANT.")
        level = BeautyLevel.COMPLIANT

    para = _PARAMS[level]
    if compliant:
        para = _clamp_compliant(para)
        # 合规档默认不做眼部锐化（避免改变五官立体光影），除非显式要求
        sharpen = False

    started = time.perf_counter()
    bgr = _pil_to_cv2(image)

    # 精确皮肤蒙版（19 类人脸解析）；不可用时为 None，蒙版自动降级
    parse_skin = _parse_skin_mask(bgr)

    faces = _detect_all_landmarks(bgr)
    eye_mask: Optional[np.ndarray] = None
    if faces:
        mask, geo = _landmark_region_mask(bgr, faces, parse_skin)
        mode = "landmark"
        if mask is not None:
            eye_mask = _eye_region_mask(bgr, faces, geo)
    else:
        mask, geo = _fallback_region(bgr, parse_skin)
        mode = "fallback"
        if mask is not None:
            logger.info("Beautify using fallback skin mask (no face landmarks).")

    if mask is None or geo is None or float(mask.max()) < 0.05:
        logger.info("Beautify skipped: no usable skin region detected.")
        return image

    out = _retouch(bgr, mask, geo, para, eye_mask,
                   do_smooth=smooth, do_blemish=blemish,
                   do_brighten=brighten, do_sharpen=sharpen)

    sel = mask > 0.4
    diff = float(np.mean(np.abs(out.astype(np.float32) - bgr.astype(np.float32))[sel])) \
        if int(sel.sum()) > 0 else 0.0
    elapsed = (time.perf_counter() - started) * 1000.0
    logger.info(
        f"Beautify done: mode={mode} parse={'on' if parse_skin is not None else 'off'} "
        f"level={level} faces={len(faces)} "
        f"r_fine={_scale(geo.iod, 0.075, 2)}px iod={geo.iod:.1f}px "
        f"mask={float(mask.mean()):.3f} skin_diff={diff:.1f} "
        f"smooth={smooth} brighten={brighten} blemish={blemish} sharpen={sharpen} "
        f"in {elapsed:.0f}ms"
    )
    return _cv2_to_pil(out)
