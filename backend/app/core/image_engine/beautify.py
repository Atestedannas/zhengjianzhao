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
    几何/肤色蒙版 → 亮度频率分离（表面模糊 base：高频毛孔 + 中频瑕疵）
    → 自适应强度 → 分区/结构保护祛瑕疵 → 去油光 → 提亮 → 通透感回填
    → 色度匀肤 → **分路合成**（皮肤蒙版）→ 眼神光（眼/眉蒙版）
    → 唇色/牙齿（各自的蒙版，包围盒内 LAB 往返）

【本轮增强（P0~P2）】
    P0 ③ 唇色/牙齿提亮   LAB 颜色微调，唇蒙版已扣除牙齿（防粉牙）；
                        预算按**通道单位**给（+6 a*/-3 b*），不再用 15% 比例乘通道值
    P0 ④ 眼神光锐化       合规档放开，boost 归一到 ≤ 红线 8%；锐化半径固定 0.8~1.8px
                        （不再随人脸尺寸放大，否则是局部对比度而非锐化），
                        另加 ≤4 级的角膜反光提亮
    P1 ① 表面模糊         替换引导滤波作为磨皮基底（保边、保留体积感）；
                        8bit+降采样加速，工作半径/降采样都有上限，防止保边性被抹平
    P1 ② 分区定向祛瑕疵   额头/鼻唇/下颌降权 + 梯度结构保护 + **关键点锚定的
                        骨相保护区**（鼻侧影/法令纹/泪沟/眉骨/下颌线）
    P2 ⑤ 自适应强度       按肤质/曝光/清晰度/红斑量/人脸占比分轴缩放参数，
                        各轴单独夹下限，避免连乘把「自然微调」压成不可见

【量级修正（重要）】
    早期实现把「比例」当成绝对通道增量，例如唇色写成
    `lips * need * COMPLIANT_MAX_CHROMA`，实际只有 +0.12 个 a 单位；
    磨皮写成 `1 - smooth_w*(1-keep)`，在 fine_keep=0.88 下最多压掉 1.5% 高频。
    两者都被 8bit 往返量化噪声（±0.5 级）淹没 —— 实测整脸平均改动 0.2~0.3 级，
    等于「美颜没生效」。现在所有强度都以**通道单位或明确的百分比**表达，
    并由 `dsh_verify_beautify.py` 逐区域量化复核。

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
    证件照对外出图只允许 OFF(0) 与 COMPLIANT(1) 两档：
      * COMPLIANT 即「自然微调」，参数被 _clamp_compliant() 硬性锁死在
        公安/政务证件照红线内（磨皮 ≤20%、保留 ≥85% 原生纹理、不改骨相）。
      * LIGHT / MEDIUM / HIGH 降级为**内部对比测试**档位，真实出图时
        同样会被 _clamp_compliant() 压回合规上限，不会产生「塑料脸」。

    ARTISTIC(4) 是**非证件照**的艺术档（写真/社交图）：强度按「PS 中等
    修图」观感给出，不经 _clamp_compliant。调用方（pipeline）在该档必须
    显式传 compliant=False，且只允许在非证件照流程暴露。

    枚举数值保持不变（OFF=0,LIGHT=1,MEDIUM=2,HIGH=3），
    以兼容既有 API 请求体与数据库里存的历史值。
    """
    OFF = 0          # 关闭（原图直出）
    COMPLIANT = 1    # ★ 自然微调：证件照唯一对外推荐档位（= 数值 1，别名 LIGHT）
    LIGHT = 1        # 兼容旧命名，语义等同 COMPLIANT
    MEDIUM = 2       # 内部测试档（会被红线钳制）
    HIGH = 3         # 内部测试档（会被红线钳制）
    ARTISTIC = 4     # 艺术档：仅非证件照流程（调用方须 compliant=False）


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
    # ---- 分区域处理系统（本轮新增）----
    tone: float = 0.0      # 分区域低频肤色修正强度（额头/两颊/下颌/脖子各自拉向参考肤色）
    texture: float = 0.0   # 纹理回注强度（把磨皮拿走的毛孔级细节按比例补回）
    feature: float = 0.0   # 五官轮廓带局部增强强度（眼/眉/鼻翼/唇缘）


# ---- 证件照美颜红线（「修饰瑕疵，保留真实」口径，任何档位都不得突破）----
# 【2026-10 二次收紧】用户实测反馈：默认档仍有塑料感/光影被抬平。证件照的
# 第一原则是「越像本人越好」：磨皮只去临时瑕疵，毛孔与光影必须留在原片。
COMPLIANT_MAX_SMOOTH = 0.12     # 磨皮力度硬上限 ≤ 12%
COMPLIANT_MAX_BLEMISH = 0.18    # 仅去临时性痘印/红肿，保留痣与特征
COMPLIANT_MAX_CHROMA = 0.15     # 肤色均匀化上限（0.30 会把血色匀成苍白；再降保留气血感）
COMPLIANT_MAX_BRIGHTEN = 0.08   # 提亮 ≤ 8%（保留面部自然明暗，不做「打光板」）
COMPLIANT_MIN_FINE_KEEP = 0.90  # 原生纹理（毛孔）保留 ≥ 90%（禁止涂抹感）
COMPLIANT_MAX_SHINE = 0.10      # 去油光仅压镜面高光
COMPLIANT_MAX_CLARITY = 0.10    # 通透感回填（立体感的主要来源，保持不变）
COMPLIANT_MAX_SHARPEN = 0.06    # 眼神光级微锐化，禁止改变五官立体光影

# 【两条红线互相约束】磨皮 ≤12% 与纹理保留 ≥90% 不能同时取满：
# 12% 的压制只剩 88% 纹理，已经越过「保留 ≥90%」。因此**实际生效上限是 10%**
# （= 1 - COMPLIANT_MIN_FINE_KEEP），COMPLIANT_MAX_SMOOTH 只是不可越过的硬顶。
# 出图档的 smooth 参数取 0.08（约 8% 磨皮），自适应强度再向下浮动。
COMPLIANT_MAX_FINE_REMOVE = 1.0 - COMPLIANT_MIN_FINE_KEEP   # = 0.10

# ---- 分区域肤色修正预算（低频，通道单位）----
# 【为什么需要这一层】整脸磨皮/匀肤只能压「高频斑点」和「中频色块」，
# 而证件照最常见的脏感来自**低频区域色差**：额头偏黄、两颊偏红、
# 下颌偏暗、脖子与脸不是一个色。低频色差不是瑕疵，是「区域性的肤色偏移」，
# 只能靠「分区域统计 → 拉向整脸参考肤色」来修，磨皮再强也修不掉。
# 预算按通道单位给（L/a/b 均为 0~255 尺度），上限刻意很小：
# 只允许「同一张脸的左右/上下更一致」，不允许把肤色整体改掉（身份特征）。
COMPLIANT_MAX_TONE_SHIFT_L = 6.0    # 区域亮度向整脸中位数靠拢的上限（±6 级）
COMPLIANT_MAX_TONE_SHIFT_A = 4.0    # 区域红度（血色）修正上限（±4 a*）
COMPLIANT_MAX_TONE_SHIFT_B = 4.0    # 区域黄度修正上限（±4 b*）
TONE_GRID = 4                       # 区域网格：4x4（额头/太阳穴/两颊/鼻/口周/下颌/脖子）

# ---- 纹理回注预算 ----
# 「保留纹理」不能只靠「少磨皮」：磨皮算子再保边，也会把毛孔级起伏压掉一部分。
# 这里把磨皮**实际拿走**的那部分细节按比例回注到平坦皮肤上（强边不回注，
# 避免把睫毛/鼻翼拉出白边），让「磨皮可感知、毛孔仍在」同时成立。
TEXTURE_REINJECT_MAX = 0.55         # 最多回注磨皮拿走的 55%（texture=1.0 时）

# ---- 局部增强预算 ----
# 五官边缘清晰化：只作用于「眼/眉/鼻翼/唇」轮廓带，不改形状、不改位置。
COMPLIANT_MAX_FEATURE_SHARPEN = 0.06

# ---- 局部美化预算（③ 唇色/牙齿、④ 眼神光）----
# 【单位教训】LAB 的 a/b/L 都是 0~255 尺度（1 单位 ≈ 1 个 CIELAB 单位），
# 用 COMPLIANT_MAX_CHROMA(=0.15) 去乘通道值只会产生 0.1 级改动 —— 肉眼不可见。
# 局部美化必须用「通道单位」给预算，并单独列在这里。
COMPLIANT_MAX_LIP_A = 6.0       # 唇色红润度提升上限（a 通道，≈ +6 a*；只补气色不改唇色）
COMPLIANT_MAX_LIP_B = 3.0       # 唇色减黄上限（b 通道，≈ -3 b*）
COMPLIANT_MAX_TEETH_DESAT = 0.45   # 牙齿去黄/去饱和比例（相对值）
COMPLIANT_MAX_TEETH_L = 8.0        # 牙齿提亮上限（L 级）
EYE_CATCHLIGHT_LIFT = 3.0       # 眼神光：仅对眼区最亮的镜面反光轻微提亮（L 级）
_BRIGHTEN_GAIN = 0.15           # 提亮主项：brighten(≤0.08) × 余量 × 该系数 → 暗部峰值约 +3 级
                                # （0.35 时 +13 级会把阴影抬平 → 光影扁平化的直接原因。
                                #  增益不受 _clamp_compliant 钳制，红线口径是 brighten 参数 ≤0.10）

_PARAMS = {
    # ★ 自然微调（默认出图档）：全部落在红线之内，且明显低于上限 ——
    #   「这个人今天睡得很好、皮肤很干净、很精神」，而不是「去整容了」。
    # smooth=0.08：约 8% 高频压制（毛孔保留 ≥92%），配合 r_fine 缩到 0.032×iod，
    #   毛孔级纹理（1~2px）几乎原样保留；
    # chroma=0.12：只去明显红斑，保留血色与肤色起伏（0.30 会匀成苍白面具）；
    # brighten=0.08 × 增益 0.22：暗部峰值约 +5 级，提神但不抬平阴影；
    # clarity=0.10：立体感的主要来源，保持 PS clarity 低档位；
    # sharpen=0.05：眼神光克制版，避免睫毛白边。
    # tone/texture/feature 是「分区域处理系统」的三个新轴：
    #   tone=0.55 只做「同一张脸内更一致」（区域色差拉向中位数，预算见
    #              COMPLIANT_MAX_TONE_SHIFT_*，绝不允许整体改肤色）；
    #   texture=0.45 把磨皮拿走的毛孔细节回注 45%（磨皮可感知 + 毛孔仍在）；
    #   feature=0.45 只在五官轮廓带做微对比，不改形状不改位置。
    BeautyLevel.COMPLIANT: _Params(0.08, 0.92, 0.15, 0.10, 0.08, 0.06, 0.99, 0.10, 0.05,
                                   0.55, 0.45, 0.45),
    # 内部对比测试档（会被 _clamp_compliant 压回上限，仅用于美学回归测试）
    BeautyLevel.MEDIUM:    _Params(0.78, 0.44, 0.68, 0.62, 0.55, 0.46, 0.85, 0.13, 0.22,
                                   0.85, 0.25, 0.70),
    BeautyLevel.HIGH:      _Params(0.92, 0.30, 0.85, 0.78, 0.72, 0.60, 0.78, 0.18, 0.30,
                                   0.95, 0.15, 0.85),
    # 艺术档（仅非证件照流程，apply_beautify 需显式 compliant=False 才真正生效；
    # 误传 compliant=True 时会被 _clamp_compliant 压回红线，等价自然微调）：
    # 不改骨相/五官几何，只在肤色、光影、通透与锐度上向「PS 中等修图」靠拢，
    # 纹理保留 ≥80% 仍由 fine_keep 下限保证。各轴必须**严格强于** COMPLIANT
    # （合规档下调到 0.08/0.12 后，艺术档相应取 0.20/0.22）。
    BeautyLevel.ARTISTIC:  _Params(0.20, 0.88, 0.25, 0.22, 0.14, 0.12, 0.96, 0.12, 0.08,
                                   0.70, 0.35, 0.55),
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
        # 新增三轴同样必须被红线钳制：
        #   tone 只允许 ±(6,4,4) 的区域色差修正，不改变整体肤色；
        #   texture 最多回注 55% 被磨掉的细节（回注过多等于没磨皮）；
        #   feature 最多 6%（与眼神光同一量级，避免「锐化脸」）。
        tone=min(float(para.tone), 1.0),
        texture=min(float(para.texture), 1.0),
        feature=min(float(para.feature), 1.0),
    )


# 引导滤波的方差阈值（guide 取值 0~255，阈值 ≈ (可平滑的亮度差 std)^2）
_EPS_FINE = 90.0     # ≈ std 9.5，小于它的起伏（毛孔/噪点）会被抹平
_EPS_MID = 520.0     # ≈ std 22.8（原 320）：阈值越大越保留鼻影/颧骨等中频明暗，
                     # 光影立体感直接由此决定；磨皮只压斑点半径内的起伏
_EPS_CLARITY = 500.0

# ---- ① 磨皮基底算子 ----
# surface = 表面模糊（保边、不做局部线性拟合，磨皮后仍保留面部体积感）；
# guided  = 旧引导滤波，保留以便 A/B 对比与一键回滚。
SMOOTH_OPERATOR = "surface"
_SURFACE_SIGMA_FINE = 10.0   # 高频 range 阈值（毛孔/噪点）
_SURFACE_SIGMA_MID = 22.0    # 中频 range 阈值（斑点/色块，阈值越大越保结构）
_SURFACE_MAX_WORK_R = 12     # 降采样后的工作半径上限：把代价锁在 d≤25 的邻域内
_SURFACE_MAX_STEP = 4        # 降采样上限 1/4（原 1/8）：降得越深保边性被抹得越平；
                             # 中频基底已改走全分辨率引导滤波（见 _retouch），此处只是兜底

_MAX_FACES = 4
_ENABLE_NECK = True  # 是否把下颌→脖子也纳入美颜（避免脸白脖子黄的接缝）


# ============================================================ 人脸解析 (SegFormer)
# 19 类人脸解析（SegFormer-mit-b5，CelebAMask-HQ 微调，ONNX 量化版，约 85MB）。
# 相比 BiSeNet：同是 19 类人脸解析，但 HuggingFace 上有现成 ONNX 可直接加载，
# 无需本地装 torch 转换；精度（mit-b5 骨干）也优于 BiSeNet-resnet18。
# 类别顺序与 zllrunning/BiSeNet 不同，见下方 _PARSE_SKIN_CLASSES。

_PARSE_MODEL_FILE = "face_parsing_segformer.onnx"
_PARSE_INPUT = 512                                   # 整图缩放到 512x512 送网络（几何见 _parse_skin_mask）
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
        # 【几何修正】整图缩放到 512x512（含纵横比压缩）→ ImageNet 归一化 → 推理
        # → 128x128 标签图缩回原图尺寸。
        # 旧实现走的是「短边 512 → 中心裁 512x512」，却把裁切窗口的结果直接
        # resize 回 (w,h)：非正方形图片的蒙版会被整体压缩并纵向错位
        # （实测 300x400 的图，错位后 30%~45% 的“皮肤”像素落在脸型多边形之外，
        #  与正确几何的 IoU 只有 0.71~0.79）。人脸解析模型本身就是在方形
        # 压缩图上训练的，直接整图缩放到方形既是标准做法，也保证蒙版几何正确。
        x_img = cv2.resize(rgb, (_PARSE_INPUT, _PARSE_INPUT),
                           interpolation=cv2.INTER_LINEAR).astype(np.float32)
        x = (x_img / 255.0 - _PARSE_MEAN) / _PARSE_STD
        x = np.transpose(x, (2, 0, 1))[None].astype(np.float32)

        out = sess.run([_get_parse_session.output_name],
                       {_get_parse_session.input_name: x})[0]   # 1,19,H,W
        labels = np.argmax(out, axis=1)[0]                       # H,W
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


def _surface_blur(img: np.ndarray, radius: int, sigma_color: float) -> np.ndarray:
    """
    表面模糊（Photoshop「表面模糊」的等价实现）.

    表面模糊 = 半径内「只对亮度差小于阈值的邻居求平均」的保边平滑：
      * 与引导滤波相比，它不做局部线性拟合，不会把大面积明暗结构当成
        低频“基础层”抹掉，磨皮后皮肤仍保留真实的体积感；
      * 阈值越大越接近普通模糊，越小越保留纹理。
    这里用 OpenCV 的 bilateralFilter 做等价实现（range 核由硬阈值换成高斯，
    避免硬阈值在肤色渐变处产生台阶）。

    【性能与精度】bilateralFilter 的代价是 O(d²)/像素，且 OpenCV 对 CV_32F
    只有朴素实现（比 8u 的 SIMD 路径慢一个量级）。因此：
      1. L 通道本来就是 0~255，先转 uint8 走 SIMD 路径（量化误差 ≤1 级，
         而 base 层本身是低频，1 级误差不可见）；
      2. 大半径时先降采样再滤波，但**工作半径不超过 `_SURFACE_MAX_WORK_R`、
         降采样不超过 1/8**：这两条上限保证 base 层不会低到丢掉保边特性
         （早期版本一路 step*=2，3000px 图上会降到 1/16，等于把保边抹平）。
    """
    import cv2

    radius = max(1, int(radius))
    h, w = img.shape[:2]
    step = 1
    while step < _SURFACE_MAX_STEP and radius / step > _SURFACE_MAX_WORK_R:
        step *= 2

    src8 = np.clip(img, 0.0, 255.0).astype(np.uint8)

    def _bil(chan8: np.ndarray, r: int) -> np.ndarray:
        r = max(1, min(int(r), _SURFACE_MAX_WORK_R))
        out = cv2.bilateralFilter(chan8, 2 * r + 1, float(sigma_color), float(r))
        return out.astype(np.float32)

    if step == 1:
        return _bil(src8, radius)

    sh, sw = max(4, h // step), max(4, w // step)
    small = cv2.resize(src8, (sw, sh), interpolation=cv2.INTER_AREA)
    small = _bil(small, int(round(radius / step)))
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


# 上排牙齿在嘴唇内的参考位置：嘴唇关键点中 0/13/14 是上下唇中点，
# 牙齿大致在上下唇之间的开口区；没有专门标注点，用嘴唇多边形向内收缩近似。
_INNER_LIPS = [78, 191, 80, 81, 82, 13, 312, 311, 310, 308, 95, 88, 178, 87, 14, 317, 402, 318, 324]


def _lips_region_mask(bgr: np.ndarray, faces: List[list], main: _FaceGeometry) -> Optional[np.ndarray]:
    """嘴唇（外缘羽化）蒙版，用于唇色提亮；牙齿区单独再给一个蒙版."""
    import cv2

    h, w = bgr.shape[:2]
    outer = np.zeros((h, w), dtype=np.uint8)
    inner = np.zeros((h, w), dtype=np.uint8)
    for lm in faces:
        pts = _pts(lm, _LIPS, w, h)
        if len(pts) >= 3:
            cv2.fillPoly(outer, [pts.astype(np.int32)], 255)
        ipts = _pts(lm, _INNER_LIPS, w, h)
        if len(ipts) >= 3:
            cv2.fillPoly(inner, [ipts.astype(np.int32)], 255)

    if int(outer.max()) == 0:
        return None

    # 嘴唇外缘羽化
    r = max(1.0, _scale(main.iod, 0.05, 2) * 0.8)
    outer_f = cv2.GaussianBlur(outer, (0, 0), r).astype(np.float32) / 255.0
    # 牙齿区 = 内唇多边形（张嘴时露牙的区域），羽化更轻
    teeth = cv2.GaussianBlur(inner, (0, 0), max(0.8, r * 0.6)).astype(np.float32) / 255.0
    teeth_f = np.clip(teeth, 0.0, 1.0).astype(np.float32)
    # 【修复】唇色蒙版必须扣除牙齿/口腔：否则牙齿会先被染红、再做美白，
    # 张口的照片容易出现粉牙。lips = 外唇 - 内唇（口裂）.
    lips_f = np.clip(outer_f - teeth_f, 0.0, 1.0).astype(np.float32)
    return lips_f, teeth_f


# ---- ② 分区定向祛瑕疵：把「整脸一律去瑕疵」拆成子蒙版，避免立体感被磨平 ----
# 依据：痘印/色块主要长在两颊与下巴；额头、鼻梁、鼻唇沟、下颌阴影属于
# 「立体结构」，一旦被中频压制就会显得脸平、法令纹消失。
_BLEMISH_ZONE_FOREHEAD = 0.68   # 额头（保护眉弓/抬头纹方向的结构）
_BLEMISH_ZONE_NOSE = 0.40       # 鼻梁 + 鼻唇沟 + 人中（立体感最强）
_BLEMISH_ZONE_JAW = 0.75        # 下颌/下巴阴影
_BLEMISH_ZONE_CHEEK = 1.0       # 两颊（瑕疵高发区）


def _blemish_zone_weight(mask: np.ndarray, geo: _FaceGeometry) -> np.ndarray:
    """
    生成「祛瑕疵强度」的分区权重图 (0~1)，几何化、不依赖关键点.

    以人脸框为坐标系，拆出额头 / 鼻唇中轴 / 下颌三个子蒙版并分别降权，
    其余（两颊）保持满权重；关键点路径与 Haar 降级路径都可用.
    """
    h, w = mask.shape[:2]
    # 用一维坐标广播代替 mgrid，避免大图上多占一整张 (h,w) 的坐标内存
    xs = (np.arange(w, dtype=np.float32) - float(geo.cx)) / max(float(geo.width), 1.0)
    ys = (np.arange(h, dtype=np.float32)
          - (float(geo.cy) - float(geo.height) / 2.0)) / max(float(geo.height), 1.0)
    nx = xs[None, :]
    ny = ys[:, None]

    weight = np.full((h, w), _BLEMISH_ZONE_CHEEK, dtype=np.float32)

    # 额头带：脸框上部（含眉弓）
    forehead = _ramp(ny, -0.18, 0.06) * (1.0 - _ramp(ny, 0.30, 0.44))
    weight = weight - (_BLEMISH_ZONE_CHEEK - _BLEMISH_ZONE_FOREHEAD) * forehead

    # 鼻梁—人中中轴带
    nose = (1.0 - _ramp(np.abs(nx), 0.10, 0.28)) \
        * _ramp(ny, 0.16, 0.32) * (1.0 - _ramp(ny, 0.72, 0.94))
    weight = weight - (_BLEMISH_ZONE_CHEEK - _BLEMISH_ZONE_NOSE) * nose

    # 下颌/下巴阴影带
    jaw = _ramp(ny, 0.80, 1.02)
    weight = weight - (_BLEMISH_ZONE_CHEEK - _BLEMISH_ZONE_JAW) * jaw

    return np.clip(weight, 0.15, 1.0).astype(np.float32)


def _structure_guard(bgr: np.ndarray, geo: _FaceGeometry) -> np.ndarray:
    """
    结构保护图 (0~1)：图像梯度越强（鼻翼、法令纹、下颌线、眉弓）权重越低.

    祛瑕疵作用在中频层，若不加约束会把「本应存在的明暗结构」一起压掉，
    表现为脸变平、法令纹消失。这里用平滑后的梯度幅值反向门控，
    只让平坦皮肤区域吃满祛瑕疵强度.
    """
    import cv2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    mag = _fast_blur(mag, _scale(geo.iod, 0.03, 2))
    return (1.0 - 0.80 * _ramp(mag, 8.0, 30.0)).astype(np.float32)


# ---- ② 的第二层子蒙版：关键点锚定的立体结构保护区 ----
# _structure_guard 只看梯度，保护得住鼻翼/下颌线的强边，却保护不了梯度并不
# 强、但同样承载立体感的**法令纹沟、泪沟、鼻侧影**。这几处用关键点直接点名保护。
# 索引均取自 MediaPipe 468 点体系。
_NOSE_RIDGE = [168, 6, 197, 195, 5, 4, 1, 2, 98, 327, 129, 358, 49, 279]
_LEFT_LOWER_LID = [33, 7, 163, 144, 145, 153, 154, 155, 133]
_RIGHT_LOWER_LID = [362, 382, 381, 380, 374, 373, 390, 249, 263]
_L_ALAR, _R_ALAR = 98, 327                    # 左右鼻翼
_L_MOUTH_CORNER, _R_MOUTH_CORNER = 61, 291    # 左右嘴角
_JAW_CONTOUR = [234, 93, 132, 58, 172, 136, 150, 149, 176, 148, 152,
                377, 400, 378, 379, 365, 397, 288, 361, 323, 454]
_LANDMARK_PROTECT_STRENGTH = 0.70   # 保护区内祛瑕疵强度最多再降 70%（不是归零）


def _structure_protect_landmarks(bgr: np.ndarray, faces: List[list],
                                 main: _FaceGeometry) -> Optional[np.ndarray]:
    """
    关键点锚定的立体结构保护区 (0~1)，用作 ② 分区祛瑕疵的第二层子蒙版.

    覆盖：鼻梁+鼻侧影（关键点凸包）、法令纹（鼻翼→嘴角连线带）、
    泪沟/卧蚕（下眼睑轮廓下移带）、眉骨阴影、下颌线（脸型轮廓线带）。

    与 `_structure_guard` 相乘后：骨相明暗不会被当成瑕疵抹平，
    但保护区内的临时痘印仍会被淡化（降权 ≠ 完全不动）。

    无关键点（Haar 降级路径）时返回 None，调用方自动退化为只用 `_structure_guard`.
    """
    import cv2

    h, w = bgr.shape[:2]
    iod = float(main.iod)
    m = np.zeros((h, w), dtype=np.uint8)

    for lm in faces:
        # 鼻梁 + 鼻侧影
        nose = _pts(lm, _NOSE_RIDGE, w, h)
        if len(nose) >= 3:
            cv2.fillConvexPoly(m, cv2.convexHull(nose.astype(np.int32)), 255)

        # 法令纹：鼻翼 → 嘴角
        thick = _scale(iod, 0.16, 3)
        for ai, ci in ((_L_ALAR, _L_MOUTH_CORNER), (_R_ALAR, _R_MOUTH_CORNER)):
            if ai < len(lm) and ci < len(lm):
                p0 = (int(lm[ai].x * w), int(lm[ai].y * h))
                p1 = (int(lm[ci].x * w), int(lm[ci].y * h))
                cv2.line(m, p0, p1, 255, thick)

        # 泪沟/卧蚕：下眼睑轮廓整体下移形成一条带
        shift = np.array([0.0, iod * 0.22], dtype=np.float32)
        for idx in (_LEFT_LOWER_LID, _RIGHT_LOWER_LID):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(m, [np.vstack([pts, (pts + shift)[::-1]]).astype(np.int32)], 255)

        # 眉骨阴影
        for idx in (_LEFT_EYEBROW, _RIGHT_EYEBROW):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(m, [pts.astype(np.int32)], 255)

        # 下颌线：脸型下半轮廓的描边带
        jaw = _pts(lm, _JAW_CONTOUR, w, h)
        if len(jaw) >= 3:
            cv2.polylines(m, [jaw.astype(np.int32)], False, 255, _scale(iod, 0.14, 3))

    if int(m.max()) == 0:
        return None

    m = cv2.dilate(m, _ellipse_kernel(_scale(iod, 0.06, 1)))
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, _scale(iod, 0.07, 1)))
    return np.clip(m.astype(np.float32) / 255.0, 0.0, 1.0)


# ============================================================ 分区域肤色修正（⑦）
# 【为什么必须分区域】磨皮压的是「高频斑点」，匀肤压的是「中频色块」，两者都
# 解决不了证件照最常见的脏感来源 —— **低频区域色差**：额头偏黄、两颊偏红、
# 下颌偏暗、脖子与脸不是一个色。这类偏移的尺度比整张脸还大，任何滤波算子都
# 只会把它一起抹平（结果是「脸糊了，色差还在」）。
# 正确做法是「分区域统计 → 把每个区域的低频色偏拉向整脸参考肤色 → 羽化融合」，
# 即 PS 里的「分区域曲线 / 局部色阶」，而不是再来一遍滤镜。
#
# 约束（证件照红线）：
#   * 只允许「同一张脸内更一致」，不允许整体改肤色 —— 参考值取**本图皮肤中位数**，
#     所以整脸偏移为 0 时修正量也为 0；
#   * 每个区域修正量硬夹在 COMPLIANT_MAX_TONE_SHIFT_* 内（L≤6 / a≤4 / b≤4）；
#   * 网格级统计 + 双线性上采样 + 柔化，避免出现「一块一块」的补丁感；
#   * 只作用于皮肤蒙版内，头发/背景/五官不受影响。

def _tone_reference(lab: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
    """整脸参考肤色（L/a/b 的皮肤中位数）；皮肤像素太少时返回 None."""
    sel = mask > 0.5
    if int(sel.sum()) < 400:
        return None
    return np.array([
        float(np.median(lab[:, :, 0][sel])),
        float(np.median(lab[:, :, 1][sel])) - 128.0,
        float(np.median(lab[:, :, 2][sel])) - 128.0,
    ], dtype=np.float32)


def _tone_shift_map(lab: np.ndarray, mask: np.ndarray,
                    grid: int = TONE_GRID) -> Optional[np.ndarray]:
    """
    计算分区域肤色修正量图 (h, w, 3)，单位为 LAB 通道单位（L/a/b 已减 128）.

    做法：把画面切成 grid×grid 个格子，逐格统计皮肤像素的 L/a/b 中位数，
    与整脸参考值相减得到该格的色偏；色偏按预算夹紧后做**双线性上采样**，
    得到平滑的修正量场。整脸偏移本来就为 0 的区域修正量自然为 0。
    """
    import cv2

    ref = _tone_reference(lab, mask)
    if ref is None:
        return None

    h, w = mask.shape[:2]
    gh = max(1, min(int(grid), h))
    gw = max(1, min(int(grid), w))
    cell_h = max(1, h // gh)
    cell_w = max(1, w // gw)

    shift_small = np.zeros((gh, gw, 3), dtype=np.float32)
    weight_small = np.zeros((gh, gw), dtype=np.float32)
    for gy in range(gh):
        y0, y1 = gy * cell_h, (gy + 1) * cell_h if gy < gh - 1 else h
        for gx in range(gw):
            x0, x1 = gx * cell_w, (gx + 1) * cell_w if gx < gw - 1 else w
            sub_mask = mask[y0:y1, x0:x1] > 0.5
            if int(sub_mask.sum()) < 120:
                continue          # 该格几乎没有皮肤：不统计，靠邻格插值填充
            sub = lab[y0:y1, x0:x1]
            med = np.array([
                float(np.median(sub[:, :, 0][sub_mask])),
                float(np.median(sub[:, :, 1][sub_mask])) - 128.0,
                float(np.median(sub[:, :, 2][sub_mask])) - 128.0,
            ], dtype=np.float32)
            d = ref - med
            shift_small[gy, gx] = [
                float(np.clip(d[0], -COMPLIANT_MAX_TONE_SHIFT_L, COMPLIANT_MAX_TONE_SHIFT_L)),
                float(np.clip(d[1], -COMPLIANT_MAX_TONE_SHIFT_A, COMPLIANT_MAX_TONE_SHIFT_A)),
                float(np.clip(d[2], -COMPLIANT_MAX_TONE_SHIFT_B, COMPLIANT_MAX_TONE_SHIFT_B)),
            ]
            weight_small[gy, gx] = 1.0

    if float(weight_small.sum()) < 1.0:
        return None

    # 无皮肤的格子用「有效格均值」兜底，避免上采样出现黑洞
    valid = weight_small > 0
    if not bool(valid.all()):
        shift_small[~valid] = shift_small[valid].mean(axis=0)

    shift = cv2.resize(shift_small, (w, h), interpolation=cv2.INTER_LINEAR)
    # 网格上采样后再柔化一次：消除格线感，保证「渐变」而不是「补丁」
    soft_r = max(2, int(min(h, w) / (grid * 6)))
    for c in range(3):
        shift[:, :, c] = _fast_blur(shift[:, :, c], soft_r)
    return shift.astype(np.float32)


# ============================================================ 纹理回注（⑧）
# 「保留纹理」不能只靠「少磨皮」：磨皮算子再保边，也会把毛孔级起伏压掉一部分；
# 而完全不磨又会保留噪点。真正的做法是 PS 的「高低频分离」：
#   高频层 = 原图 - 磨皮基底，磨皮只**部分**压制高频；
#   压制后把「被拿走的那部分高频」按比例**回注**到平坦皮肤上。
# 强边（睫毛/鼻翼/痣/发丝）不回注，否则会把边缘拉出白边黑边。

def _texture_reinject(removed: np.ndarray, guard: np.ndarray,
                      strength: float) -> np.ndarray:
    """
    计算纹理回注量.

    removed: 磨皮真正拿走的高频量（high_original - high_after）
    guard:   强边保护（1=平坦皮肤可回注，0=强边不回注）
    strength: 回注比例（0~1，最终乘 TEXTURE_REINJECT_MAX）

    【为什么不直接「全额还原」】全额还原等于没磨皮，磨皮对中频斑点/噪点的
    压制会一起消失。回注只补「毛孔尺度的细节」：用幅度软阈值把
    大起伏（痣、睫毛根、鼻翼边）排除在外，剩下的才是要找回的皮肤质感。
    """
    if strength <= 0.0:
        return np.zeros_like(removed)
    # 幅度门控：毛孔级起伏（≈1~6 级）吃满回注，更大起伏本来就没被压掉
    small = 1.0 - _ramp(np.abs(removed), 1.0, 12.0)
    return removed * (TEXTURE_REINJECT_MAX * strength) * small * guard


# ============================================================ 五官轮廓带局部增强（⑨）
# 「眼睛、眉毛、鼻翼边缘不够清晰」不能靠整脸锐化解决（那会把皮肤噪点一起放大，
# 变成「锐化脸」）。这里只在**五官轮廓带**（眼/眉/鼻翼/唇缘，由关键点生成）
# 做 1~2px 尺度的局部对比增强，不改形状、不改位置、不改瞳孔颜色。

def _feature_band_mask(bgr: np.ndarray, faces: List[list],
                       main: _FaceGeometry) -> Optional[np.ndarray]:
    """
    五官轮廓带蒙版 (0~1)：眼/眉/鼻翼/唇缘的**描边带**（不是整块五官）.

    为什么用描边带而不是整块：整块增强会把眼白/虹膜一起提对比（显假、改色），
    描边带只增强「五官与皮肤的交界」，观感就是「五官清楚了」。
    """
    import cv2

    h, w = bgr.shape[:2]
    iod = float(main.iod)
    m = np.zeros((h, w), dtype=np.uint8)

    for lm in faces:
        for idx in (_LEFT_EYE, _RIGHT_EYE, _LEFT_EYEBROW, _RIGHT_EYEBROW, _LIPS):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.polylines(m, [pts.astype(np.int32)], True, 255,
                              max(1, _scale(iod, 0.05, 2)))
        # 鼻翼：两个鼻孔点各画一个小环（鼻翼边缘是「鼻形清楚」的关键）
        for i in _NOSTRILS:
            if i < len(lm):
                cv2.circle(m, (int(lm[i].x * w), int(lm[i].y * h)),
                           max(2, _scale(iod, 0.09, 2)), 255,
                           max(1, _scale(iod, 0.04, 1)))

    if int(m.max()) == 0:
        return None
    m = cv2.GaussianBlur(m, (0, 0), max(0.8, _scale(iod, 0.02, 1) * 0.6))
    return np.clip(m.astype(np.float32) / 255.0, 0.0, 1.0)


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


# ============================================================ 自适应强度（⑤）

def _lin(x: float, lo: float, hi: float) -> float:
    """标量线性软阈值（与 _ramp 同语义，供自适应强度使用）."""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def _adaptive_params(para: _Params, bgr: np.ndarray, mask: np.ndarray,
                     geo: _FaceGeometry) -> _Params:
    """
    自适应强度：按「肤质 / 曝光 / 清晰度 / 人脸占比」缩放美颜参数.

    证件照来源极杂（手机自拍、影楼翻拍、老照片翻拍），固定强度必然在部分
    场景过强或不足。这里只做保守的统计缩放，不做任何生成式改动：
      * 肤质：高频能量高（瑕疵多）→ 略强；已经很干净 → 收敛，防越磨越假；
      * 曝光：偏暗多提亮、偏亮少提亮，避免高光死白/假白；
      * 清晰度：越糊越少磨皮与锐化，避免把噪点当纹理放大；
      * 人脸占比：脸太小 → 关键点与结构不可靠，整体收敛.
    缩放结果仍会经 _clamp_compliant() 二次钳制，绝不可能突破红线.
    """
    import cv2

    sel = mask > 0.5
    n = int(sel.sum())
    if n < 200:
        return para

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan = lab[:, :, 0].astype(np.float32)
    a_chan = lab[:, :, 1].astype(np.float32)
    mean_l = float(np.mean(l_chan[sel]))

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hp = gray - _fast_blur(gray, _scale(geo.iod, 0.06, 2))
    texture = float(np.mean(np.abs(hp[sel])))
    tex_scale = 0.72 + 0.40 * _lin(texture, 1.2, 5.5)

    # 红斑量（a 通道相对皮肤中位数的上偏）：越红越需要色度匀肤
    a_ref = float(np.median(a_chan[sel]))
    redness = float(np.mean(np.clip(a_chan[sel] - a_ref, 0.0, None)))
    red_scale = 0.85 + 0.30 * _lin(redness, 1.5, 6.0)

    dark_need = 1.0 - _lin(mean_l, 105.0, 165.0)
    bright_scale = 0.55 + 0.80 * dark_need

    lap = cv2.Laplacian(l_chan, cv2.CV_32F)
    sharpness = float(np.mean(np.abs(lap[sel])))
    sharp_scale = 0.70 + 0.45 * _lin(sharpness, 3.0, 14.0)

    face_ratio = float(geo.width) / max(float(mask.shape[1]), 1.0)
    face_scale = 0.78 + 0.25 * _lin(face_ratio, 0.18, 0.55)

    # 【为什么要夹下限】三个系数连乘最坏只有 0.72*0.70*0.78 ≈ 0.39，
    # 会把「自然微调」压成肉眼不可见（实测暗图/糊图/小脸时磨皮只剩 9%）。
    # 这里按轴分别夹到有意义的区间：
    #   smooth ≥0.80、blemish ≥0.80 → 出图档实际落在 6%~8% 磨皮、12%~15% 去瑕疵；
    #   chroma 允许到 1.15（红斑多的照片更需要匀肤），上限仍由红线钳制。
    skin_scale = min(1.0, max(0.80, tex_scale * sharp_scale * face_scale))
    blemish_scale = min(1.0, max(0.80, tex_scale * face_scale))

    out = _Params(
        smooth=para.smooth * skin_scale,
        # 越糊的图越要保住已有纹理（该系数是保留率下限，调高 = 更保守）
        fine_keep=min(0.95, para.fine_keep + (0.05 if sharp_scale < 0.85 else 0.0)),
        blemish=para.blemish * blemish_scale,
        chroma=para.chroma * min(1.15, red_scale) * face_scale,
        shine=para.shine * max(0.60, bright_scale * 0.90),
        brighten=para.brighten * bright_scale * face_scale,
        # gamma 不放这里调：合规档的提亮曲线被红线固定为 ≥0.97，
        # 自适应再改也会被 _clamp_compliant 抹平（旧版这行是死代码）。
        gamma=para.gamma,
        clarity=para.clarity * (0.90 + 0.20 * sharp_scale),
        sharpen=para.sharpen * sharp_scale * face_scale,
        # ⑦⑧⑨ 三个新轴：
        #   tone 与清晰度无关，按人脸占比收敛（小脸时区域统计不可靠）；
        #   texture 是「找回细节」，脸越小越需要（插值损失更明显），但上限 1.0；
        #   feature 跟清晰度走：越糊越不能硬锐化，否则把压缩噪点当轮廓放大。
        tone=para.tone * (0.85 + 0.15 * face_scale),
        texture=min(1.0, para.texture * (1.15 - 0.15 * sharp_scale)),
        feature=para.feature * sharp_scale * face_scale,
    )
    logger.debug(
        f"Adaptive: mean_l={mean_l:.1f} texture={texture:.2f} sharp={sharpness:.2f} "
        f"redness={redness:.2f} face_ratio={face_ratio:.2f} -> smooth x{skin_scale:.2f} "
        f"blemish x{blemish_scale:.2f} chroma x{min(1.15, red_scale) * face_scale:.2f} "
        f"brighten x{bright_scale * face_scale:.2f} sharpen x{sharp_scale * face_scale:.2f}"
    )
    return out


# ============================================================ 美颜算子

def _mask_bbox(mask: np.ndarray, thr: float = 0.02, margin: int = 8):
    """蒙版的包围盒（带外扩）；空蒙版返回 None."""
    ys, xs = np.nonzero(mask > thr)
    if len(ys) == 0:
        return None
    h, w = mask.shape[:2]
    return (max(0, int(ys.min()) - margin), min(h, int(ys.max()) + margin + 1),
            max(0, int(xs.min()) - margin), min(w, int(xs.max()) + margin + 1))


def _local_lab_edit(bgr: np.ndarray, roi_mask: np.ndarray, edit) -> np.ndarray:
    """
    在 roi_mask 的包围盒内做一次 LAB 编辑，并把结果按蒙版软贴回原图.

    `edit(l, a, b, m) -> (l, a, b)`：a/b 是减去 128 的带符号值，m 是盒内的
    2D 蒙版（供闭包做分位数统计）。逐像素合成由本函数负责，edit 里**不要**再乘 m。

    【为什么要包围盒】眼睛/嘴唇/牙齿只占画面很小一块，整图 BGR↔LAB 往返在
    3000px 原图上是百毫秒级开销；限定在包围盒内做，代价与人脸大小成正比.
    """
    import cv2

    box = _mask_bbox(roi_mask)
    if box is None:
        return bgr
    y0, y1, x0, x1 = box
    sub = bgr[y0:y1, x0:x1]
    m = np.asarray(roi_mask[y0:y1, x0:x1], dtype=np.float32)
    if float(m.max()) < 0.02:
        return bgr

    lab = cv2.cvtColor(sub, cv2.COLOR_BGR2LAB)
    l = lab[:, :, 0].astype(np.float32)
    a = lab[:, :, 1].astype(np.float32) - 128.0
    b = lab[:, :, 2].astype(np.float32) - 128.0

    l2, a2, b2 = edit(l, a, b, m)

    out_lab = cv2.merge([
        np.clip(l2, 0, 255).astype(np.uint8),
        np.clip(a2 + 128.0, 0, 255).astype(np.uint8),
        np.clip(b2 + 128.0, 0, 255).astype(np.uint8),
    ])
    edited = cv2.cvtColor(out_lab, cv2.COLOR_LAB2BGR).astype(np.float32)
    m3 = m[..., None]
    res = bgr.copy()
    res[y0:y1, x0:x1] = np.clip(sub.astype(np.float32) * (1.0 - m3) + edited * m3,
                                0.0, 255.0).astype(np.uint8)
    return res


def _retouch(bgr: np.ndarray, mask: np.ndarray, geo: _FaceGeometry, para: _Params,
             eye_mask: Optional[np.ndarray],
             do_smooth: bool, do_blemish: bool,
             do_brighten: bool, do_sharpen: bool,
             lips_mask: Optional[np.ndarray] = None,
             teeth_mask: Optional[np.ndarray] = None,
             smooth_op: str = SMOOTH_OPERATOR,
             structure_protect: Optional[np.ndarray] = None,
             feature_band: Optional[np.ndarray] = None,
             debug: Optional[dict] = None) -> np.ndarray:
    """
    在 LAB 空间做实际的美颜运算，最后**分路贴蒙版**合成.

    亮度层 L：频率分离磨皮（高频毛孔 + 中频瑕疵）→ **纹理回注** → 去油光
              → 提亮 → 通透感 → **分区域肤色修正**
    色度层 a/b：大半径匀肤（去红斑/色块，不动亮度纹理）

    合成顺序（顺序不能改）：
      1. 皮肤路：磨皮/回注/匀肤/分区肤色/提亮/通透，贴皮肤蒙版 `mask`；
      2. 五官轮廓带：眼/眉/鼻翼/唇缘的 1~2px 局部对比增强（不改形状）；
      3. 眼神光：在合成后的亮度上做局部锐化 + 角膜反光提亮，贴眼/眉蒙版；
      4. 唇色、牙齿：各自的 `_local_lab_edit`，贴自己的蒙版。
    皮肤蒙版天然抠掉了眼/眉/唇，所以 2、3、4 必须走各自的蒙版，
    不能混进皮肤路一起乘 `mask`（那样会被整段丢弃）。

    `debug`（可选）：传入 dict 时会把各阶段中间结果写进去，供
    `apply_beautify(debug_dir=...)` 落盘做「哪一步导致不自然」的定位。
    """
    import cv2

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan = lab[:, :, 0].astype(np.float32)
    a_chan = lab[:, :, 1].astype(np.float32) - 128.0
    b_chan = lab[:, :, 2].astype(np.float32) - 128.0

    # 【半径即真实感】r_fine 曾是 0.075×iod（iod≈93px 时 ≈7px）——而毛孔只有
    # 1~2px，7px 半径的表面模糊会把毛孔连同噪点一起吃掉（塑料面具感的直接原因）。
    # 收紧到 0.032×iod（≈3px）：只抹平单像素噪点，毛孔与皮肤纹理原样保留。
    r_fine = _scale(geo.iod, 0.032, 2)
    r_mid = max(r_fine + 2, _scale(geo.iod, 0.16, 4))

    # ① 磨皮基底：
    #   - 细频基底 base_fine：表面模糊（保边、保留体积感），可切回引导滤波 A/B；
    #   - 中频基底 base_mid：一律用**全分辨率**引导滤波。
    #   【为什么 base_mid 不走 _surface_blur】它的大半径路径会先降采样再放大
    #   （step≥2 的插值往返），而 l_out = base_mid + high + mid —— 低频「底座」
    #   过一次插值会把整图系统性压软（观感就是「糊」）。引导滤波是 O(1)/像素、
    #   代价与半径无关，全分辨率跑零额外开销，根除这层插值损失。
    base_fine = (_guided_1(l_chan, l_chan, r_fine, _EPS_FINE)
                 if smooth_op == "guided"
                 else _surface_blur(l_chan, r_fine, _SURFACE_SIGMA_FINE))
    base_mid = _guided_1(l_chan, l_chan, r_mid, _EPS_MID)
    high = l_chan - base_fine    # 高频：毛孔 / 噪点
    mid = base_fine - base_mid   # 中频：斑点 / 痘印 / 色块

    # 皮肤本身就很干净时自动降档，避免“越磨越假”
    # （_adaptive_params 已按纹理/清晰度/人脸占比算过一轮，这里只做兜底统计）
    sel_skin = mask > 0.5

    smooth_w = para.smooth if do_smooth else 0.0
    blemish_w = para.blemish if do_blemish else 0.0
    chroma_w = para.chroma if do_smooth else 0.0
    # 【解耦】分区肤色修正有自己的开关（para.tone / beautify_tone），
    # 不挂在「磨皮」开关上：用户可能只想均匀肤色、不想磨皮。
    tone_w = para.tone

    if smooth_w > 0.0:
        # 幅度门控：小起伏（毛孔/噪点）压掉，大起伏（褶皱/轮廓/痣）保留
        gate = _ramp(np.abs(high), 2.5, 14.0)
        # 【修正】淘汰「把 keep 当权重」的旧写法：原式 1-smooth_w*(1-keep)
        # 在 fine_keep=0.88 / smooth_w≤0.15 时最多只压掉 1.5% 高频，
        # 与「磨皮 ≤15%」的红线差一个数量级 —— 实测整脸平均改动 <0.5 级，
        # 被 8bit 往返量化噪声完全淹没，观感上等于没做美颜。
        # 现在 fine_keep 是**保留率下限**：平坦皮肤压掉 smooth_w（≤15%），
        # 强起伏按 gate 平滑回落到 100% 保留，红线口径与文档一致。
        atten = np.maximum(1.0 - smooth_w * (1.0 - gate), para.fine_keep)
        high_kept = high * atten
        # ⑧ 纹理回注：把「磨皮拿走的毛孔级细节」按比例补回平坦皮肤。
        # 强边（|high| 很大）本来就没被压，removed 接近 0，自然不会回注；
        # 再用 gate 反向门控，保证不会把睫毛/鼻翼拉出白边。
        removed = high - high_kept
        # guard 用「弱边才回注」：强边处 atten≈1、removed≈0，本来就无需回注，
        # 这里再乘一次 (1-gate) 只是防止数值噪声被放大成白边。
        high = high_kept + _texture_reinject(removed, 1.0 - 0.5 * gate, para.texture)

    if blemish_w > 0.0:
        # ② 分区 + 双层结构保护：
        #   _structure_guard        数据驱动（梯度），保护鼻翼/下颌线等强边
        #   _structure_protect_*    关键点驱动，点名保护鼻侧影/法令纹/泪沟/眉骨
        guard = _structure_guard(bgr, geo)
        if structure_protect is not None:
            guard = guard * (1.0 - _LANDMARK_PROTECT_STRENGTH * structure_protect)
        blemish_map = blemish_w * _blemish_zone_weight(mask, geo) * guard
        gate = _ramp(np.abs(mid), 4.0, 26.0)
        mid = mid * (1.0 - blemish_map * (1.0 - gate))

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

    # 提亮：按「向白点的余量」抬升，暗部抬得多、亮部几乎不动（不追求“假白”）
    # 【量级修正】旧写法是「向 gamma 曲线混合 brighten 比例」，而合规档
    # gamma 被红线固定在 ≥0.97，曲线与原图几乎重合 → 实测只抬 0.3~0.5 级，
    # 等于没提亮。现在主项改为按余量抬升（增益 0.35 后峰值暗部约 +13 级、
    # 高光 <1 级），gamma 仍作为曲线形状的次项保留。
    if do_brighten and para.brighten > 0.0:
        norm = np.clip(l_out, 0.0, 255.0) / 255.0
        protect = _ramp(norm, 0.78, 0.96)                      # 高光保护
        lift = para.brighten * _BRIGHTEN_GAIN * np.clip(255.0 - l_out, 0.0, 255.0)
        lift = lift + para.brighten * (np.power(norm, para.gamma) * 255.0 - l_out)
        l_out = l_out + lift * (1.0 - protect)

    # 通透感：把平滑底层的明暗结构回填一点，避免“糊平”
    if para.clarity > 0.0:
        low = _guided_1(base_mid, base_mid, _scale(geo.iod, 0.30, 4), _EPS_CLARITY)
        l_out = l_out + para.clarity * (base_mid - low)

    # ⑦ 分区域低频肤色修正：把额头/两颊/下颌/脖子的**区域色偏**拉向整脸参考肤色.
    # 【为什么放在提亮之后】提亮会整体抬 L，先提亮再按「区域相对参考值」修正，
    # 才能保证修正量衡量的是「区域之间的差」而不是「整体亮度」；
    # 也避免修正量被后面的整体抬升再次拉开。
    tone_shift = None
    if tone_w > 0.0:
        # 用**磨皮之后**的 L 统计（斑点已被压掉，中位数更能代表区域肤色），
        # 色度用当前 a/b（匀肤前的原值即可 —— 区域色偏本就属于低频）。
        lab_tone = np.stack([
            np.clip(l_out, 0.0, 255.0),
            np.clip(a_chan + 128.0, 0.0, 255.0),
            np.clip(b_chan + 128.0, 0.0, 255.0),
        ], axis=-1).astype(np.float32)
        tone_shift = _tone_shift_map(lab_tone, mask)
        if tone_shift is not None:
            l_out = l_out + tone_w * tone_shift[:, :, 0]
            a_chan = a_chan + tone_w * tone_shift[:, :, 1]
            b_chan = b_chan + tone_w * tone_shift[:, :, 2]
            if debug is not None:
                debug["tone_shift"] = tone_shift

    # 色度匀肤：LAB 的 a/b 大幅平滑，去掉红斑与肤色斑块（只作用于皮肤路）。
    # 【蒙版加权平滑】先按皮肤蒙版加权求均值再平滑、除回平滑后的权重和：
    # 裸 blur 会把发际线/下颌缘过渡带里头发与背景的色度混进皮肤
    # （表现为边缘色晕、显脏）；加权版在过渡带只参考「属于皮肤」的像素。
    if chroma_w > 0.0:
        # 0.35×iod 的大半径会把整脸色度拉向同一个均值（苍白面具感）；
        # 收到 0.22×iod：只匀邻近区域的色块，保留脸颊-唇周的自然色差。
        r_chroma = _scale(geo.iod, 0.22, 4)
        m_soft = np.clip(_fast_blur(mask, r_chroma), 1e-2, 1.0)
        a_ref = np.clip(_fast_blur(a_chan * mask, r_chroma) / m_soft, -128.0, 127.0)
        b_ref = np.clip(_fast_blur(b_chan * mask, r_chroma) / m_soft, -128.0, 127.0)
        a_chan = a_chan + chroma_w * (a_ref - a_chan)
        b_chan = b_chan + chroma_w * (b_ref - b_chan)

    out_lab = cv2.merge([
        np.clip(l_out, 0, 255).astype(np.uint8),
        np.clip(a_chan + 128.0, 0, 255).astype(np.uint8),
        np.clip(b_chan + 128.0, 0, 255).astype(np.uint8),
    ])
    retouched = cv2.cvtColor(out_lab, cv2.COLOR_LAB2BGR).astype(np.float32)

    # ---- 合成：分路贴蒙版 ----
    # 【关键修正】皮肤蒙版 mask 已经抠掉了眼/眉/唇/鼻孔，所以「磨皮/匀肤/提亮」
    # 只能贴 mask；而唇色、牙齿、眼神光的蒙版天然落在 mask 之外。
    # 早期实现把这三者也改进同一份 LAB、最后统一乘 mask 合成 ——
    # 结果它们在合成时被 mask=0 乘掉，一行都没生效
    # （实测唇区 |Δa| 仅 0.3 级、眼区高频保留 1.005，与"没做"无异）。
    m3 = mask[..., None]
    out = np.clip(bgr.astype(np.float32) * (1.0 - m3) + retouched * m3,
                  0.0, 255.0).astype(np.uint8)
    if debug is not None:
        debug["skin"] = out.copy()

    # ⑨ 五官轮廓带局部增强：只在眼/眉/鼻翼/唇缘的描边带上做 1~2px 局部对比，
    # 让「眼睛、眉毛、鼻翼边缘更清楚」。不改形状、不改位置、不动瞳孔颜色。
    # 【为什么放在合成之后】它作用于**皮肤与五官的交界**，如果放进皮肤路会被
    # 皮肤蒙版乘掉（交界处 mask≈0），必须独立贴自己的蒙版。
    if do_sharpen and para.feature > 0.0 and feature_band is not None:
        sigma_f = float(np.clip(r_fine * 0.22, 0.7, 1.6))

        def _feature_edit(l, a, b, m):
            detail = l - cv2.GaussianBlur(l, (0, 0), sigma_f)
            # 只增强有意义的轮廓（>4 级），噪点与羽化带不进入增强区
            boost = _ramp(np.abs(detail), 4.0, 16.0)
            # 有效强度 = min(feature, 红线 6%)：boost 已归一到 [0,1]，
            # 所以峰值增益就是 COMPLIANT_MAX_FEATURE_SHARPEN。
            l = l + min(COMPLIANT_MAX_FEATURE_SHARPEN, para.feature) * boost * detail
            return l, a, b

        out = _local_lab_edit(out, feature_band, _feature_edit)

    # 眼神光：在**合成后**的亮度上做局部锐化 + 角膜反光提亮，只贴眼/眉蒙版
    if do_sharpen and para.sharpen > 0.0 and eye_mask is not None:
        # 【口径】「眼神光」的尺度就是 1~2px：锐化半径固定在像素级。
        # 旧实现用 max(0.8, r_fine*0.6)，3000px 原图上 r_fine≈26 → sigma≈15.6，
        # 那不是锐化而是大尺度局部对比度，会在睫毛/瞳孔边缘拉出白边黑边。
        sigma = float(np.clip(r_fine * 0.25, 0.8, 1.8))

        def _eye_edit(l, a, b, m):
            detail = l - cv2.GaussianBlur(l, (0, 0), sigma)
            # boost 归一到 [0,1] → 有效强度峰值 = para.sharpen ≤ 6%（红线）
            # 阈值 3~12（回调自 2~10）：只增强瞳孔/睫毛的强边缘，中等幅度的
            # 皮肤噪点不再进入增强区，避免「眼睛看着变大/睫毛拉丝」。
            boost = _ramp(np.abs(detail), 3.0, 12.0)
            l = l + para.sharpen * boost * detail
            # 角膜反光/眼白高光轻微提亮（≤4 级）；阈值取眼区自身分位，
            # 不引入固定白点，也不碰瞳孔颜色。
            sel = m > 0.3
            if EYE_CATCHLIGHT_LIFT > 0.0 and int(sel.sum()) > 64:
                thr = float(np.percentile(l[sel], 97.0))
                l = l + _ramp(l - thr, 0.0, 18.0) * EYE_CATCHLIGHT_LIFT
            return l, a, b

        out = _local_lab_edit(out, eye_mask, _eye_edit)

    # 唇色提亮 + 牙齿美白（不改唇形、不改唇面积，只提气色）
    if do_brighten and lips_mask is not None:
        a_ref = float(np.median(a_chan[sel_skin])) if int(sel_skin.sum()) > 200 else 12.0

        def _lip_edit(l, a, b, m):
            # 【单位修正】用通道单位预算（≤ +4 a / −2 b）。旧写法把 15% 的比例
            # 直接乘到通道值上，只有 +0.12 级 a，被量化噪声完全淹没。
            # 基准是本图皮肤自身的红度：比皮肤红 10 级以内吃满预算，
            # 红过 30 级的深唇色不再加红（越红越不动）。
            need = 1.0 - _ramp(a - a_ref, 10.0, 30.0)
            return l, a + need * COMPLIANT_MAX_LIP_A, b - need * COMPLIANT_MAX_LIP_B

        out = _local_lab_edit(out, lips_mask, _lip_edit)

    if do_brighten and teeth_mask is not None:

        def _teeth_edit(l, a, b, m):
            sat = np.sqrt(a ** 2 + b ** 2)
            desat = _ramp(sat, 8.0, 24.0)      # 越黄减得越多，白牙几乎不动
            return (l + desat * COMPLIANT_MAX_TEETH_L,                  # ≤ +8 级亮度
                    a * (1.0 - desat * COMPLIANT_MAX_TEETH_DESAT * 0.8),
                    b * (1.0 - desat * COMPLIANT_MAX_TEETH_DESAT))      # 偏黄主要在 b

        out = _local_lab_edit(out, teeth_mask, _teeth_edit)

    return out


def _dump_stage_images(dump_dir: str, bgr: np.ndarray, mask: np.ndarray,
                       feature_band: Optional[np.ndarray], debug: dict,
                       out: np.ndarray) -> None:
    """把美颜各阶段中间结果落盘（评测/调试专用，生产不调用）.

    产物（与 PRD 的验收口径一一对应）：
      skin_mask.png  皮肤蒙版（红叠色）：确认「磨到了哪里」
      smooth.png     磨皮+纹理回注后的结果（无分区肤色/无局部增强）
      tone.png       分区肤色修正量可视化（红=拉暖/提亮，蓝=压暗）
      feature.png    五官轮廓带蒙版：确认「五官保护/增强落在哪」
      final.png      最终结果
    只用于「哪一步导致不自然」的定位，不参与生产出图。
    """
    import cv2

    os.makedirs(dump_dir, exist_ok=True)

    def _write(name: str, img: np.ndarray) -> None:
        try:
            cv2.imwrite(os.path.join(dump_dir, name), img)
        except Exception as e:  # noqa: BLE001 —— 调试落盘失败绝不能影响出图
            logger.warning(f"dump {name} failed: {e}")

    vis = bgr.astype(np.float32).copy()
    vis[..., 2] = np.clip(vis[..., 2] + 140.0 * np.clip(mask, 0.0, 1.0), 0, 255)
    _write("skin_mask.png", vis.astype(np.uint8))

    if debug.get("skin") is not None:
        _write("smooth.png", np.ascontiguousarray(debug["skin"]))

    shift = debug.get("tone_shift")
    if shift is not None:
        # 修正量可视化：a 通道放大后映射到红/蓝，便于看「哪块被拉向参考肤色」
        amt = np.clip(shift[:, :, 1] / max(COMPLIANT_MAX_TONE_SHIFT_A, 1e-6), -1.0, 1.0)
        tone_vis = bgr.astype(np.float32).copy()
        pos = np.clip(amt, 0, 1)[..., None]
        neg = np.clip(-amt, 0, 1)[..., None]
        tone_vis = tone_vis * (1 - 0.35 * pos) + np.array([60, 60, 255], np.float32) * (0.35 * pos)
        tone_vis = tone_vis * (1 - 0.35 * neg) + np.array([255, 120, 60], np.float32) * (0.35 * neg)
        _write("tone.png", np.clip(tone_vis, 0, 255).astype(np.uint8))

    if feature_band is not None:
        fv = bgr.astype(np.float32).copy()
        fv[..., 1] = np.clip(fv[..., 1] + 160.0 * np.clip(feature_band, 0.0, 1.0), 0, 255)
        _write("feature.png", fv.astype(np.uint8))

    _write("final.png", np.ascontiguousarray(out))


# ============================================================ 对外接口

def apply_beautify(
    image: Image.Image,
    level: int = BeautyLevel.COMPLIANT,
    smooth: bool = True,
    brighten: bool = True,
    blemish: bool = True,
    sharpen: bool = True,
    compliant: bool = True,
    adaptive: bool = True,
    smooth_operator: str = SMOOTH_OPERATOR,
    strength: float = 1.0,
    tone: bool = True,
    texture: bool = True,
    feature: bool = True,
    debug_dir: Optional[str] = None,
) -> Image.Image:
    """
    对图片应用美颜处理（FaceLandmarker + 频率分离 + 表面模糊）.

    参数:
        image:     输入图片
        level:     0=OFF 1=自然微调(COMPLIANT) 2/3=内部测试档（会被红线钳制）
                   4=艺术档(ARTISTIC，仅非证件照流程；须配合 compliant=False)
        smooth:    磨皮（高频）+ 色度匀肤
        brighten:  提亮 + 去油光 + 唇色/牙齿
        blemish:   去瑕疵（中频斑点，分区 + 结构保护）
        sharpen:   眼部微锐化（眼神光，有效峰值 ≤ 红线 8%）
        compliant: ★ 是否启用证件照合规红线（**默认 True**）。
                   True 时参数经 _clamp_compliant() 钳制，保证不改骨相、
                   保留 ≥85% 原生纹理；仅非证件照的艺术档（ARTISTIC）与
                   美学回归测试才应传 False。
        adaptive:  ★ 是否按肤质/曝光/清晰度/人脸占比自适应缩放强度（默认 True）
        smooth_operator: 磨皮基底算子，"surface"=表面模糊（默认）/"guided"=引导滤波
        strength:  ★ 用户「还原度」滑杆（0~1，默认 1=完整档位）。只缩放减法类轴
                   （磨皮/去瑕疵/匀肤/去油光/提亮/锐化/分区肤色），fine_keep 随之
                   反向抬升；在红线钳制之后做纯减法，结果必然仍在红线内。
                   0 = 完全还原原图。
        tone:      ★ ⑦ 分区域低频肤色修正（额头/两颊/下颌/脖子各自拉向参考肤色）
        texture:   ★ ⑧ 纹理回注（把磨皮拿走的毛孔细节按比例补回，防塑料感）
        feature:   ★ ⑨ 五官轮廓带局部增强（眼/眉/鼻翼/唇缘，不改形状不改位置）
        debug_dir: ★ 传入目录时落盘各阶段中间结果，便于定位「哪一步导致不自然」：
                   skin_mask.png / smooth.png / tone.png / feature.png / final.png
                   （对应 PRD 里「保存各中间结果」的评测要求）。生产路径默认 None，
                   不做任何额外编码，零开销。

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
        # 合规档允许眼部锐化：只增强眼睛/眉毛的对比度（眼神光），
        # 不改变五官位置与比例，强度已被 _clamp_compliant 锁在红线内。
        # 显式传 sharpen=False 才真正关闭。

    # ⑥ 用户「还原度」滑杆：strength ∈ [0,1]。缩放在红线钳制之后只做减法
    # （除 fine_keep 反向抬升外全是乘 s），结果必然仍在红线内。
    s = min(1.0, max(0.0, float(strength)))
    if s < 1.0:
        para = _Params(
            smooth=para.smooth * s,
            fine_keep=1.0 - (1.0 - para.fine_keep) * s,
            blemish=para.blemish * s,
            chroma=para.chroma * s,
            shine=para.shine * s,
            brighten=para.brighten * s,
            gamma=para.gamma,
            clarity=para.clarity * s,
            sharpen=para.sharpen * s,
            # 分区肤色同样属于「减法类」轴：强度滑杆拉低时同步减弱，
            # 否则「还原度 0%」时区域色差修正仍在生效，与「完全还原原图」矛盾。
            tone=para.tone * s,
            # 纹理回注与五官增强不是「改动」而是「找回细节」，不随滑杆归零：
            # 滑杆=0 时磨皮本身也归零，回注量随之自然为 0（removed≈0）。
            texture=para.texture,
            feature=para.feature * s,
        )

    started = time.perf_counter()
    bgr = _pil_to_cv2(image)

    # 精确皮肤蒙版（19 类人脸解析）；不可用时为 None，蒙版自动降级
    parse_skin = _parse_skin_mask(bgr)

    faces = _detect_all_landmarks(bgr)
    eye_mask: Optional[np.ndarray] = None
    lips_mask: Optional[np.ndarray] = None
    teeth_mask: Optional[np.ndarray] = None
    structure_protect: Optional[np.ndarray] = None
    feature_band: Optional[np.ndarray] = None
    if faces:
        mask, geo = _landmark_region_mask(bgr, faces, parse_skin)
        mode = "landmark"
        if mask is not None:
            eye_mask = _eye_region_mask(bgr, faces, geo)
            lm_result = _lips_region_mask(bgr, faces, geo)
            if lm_result is not None:
                lips_mask, teeth_mask = lm_result
            # ② 第二层子蒙版：鼻侧影/法令纹/泪沟/眉骨/下颌线的点名保护
            structure_protect = _structure_protect_landmarks(bgr, faces, geo)
            # ⑨ 五官轮廓带：眼/眉/鼻翼/唇缘的描边带（局部增强用）
            if feature:
                feature_band = _feature_band_mask(bgr, faces, geo)
    else:
        mask, geo = _fallback_region(bgr, parse_skin)
        mode = "fallback"
        if mask is not None:
            logger.info("Beautify using fallback skin mask (no face landmarks).")

    if mask is None or geo is None or float(mask.max()) < 0.05:
        logger.info("Beautify skipped: no usable skin region detected.")
        return image

    # ⑤ 自适应强度：按图像内容缩放参数；缩放后再过一次红线钳制（幂等）
    if adaptive:
        para = _adaptive_params(para, bgr, mask, geo)
        if compliant:
            para = _clamp_compliant(para)

    # ⑦⑧⑨ 三个开关：关掉对应轴即退回「整脸滤镜」旧行为（便于 A/B 与一键回滚）
    if not (tone and texture and feature):
        para = _Params(
            smooth=para.smooth, fine_keep=para.fine_keep, blemish=para.blemish,
            chroma=para.chroma, shine=para.shine, brighten=para.brighten,
            gamma=para.gamma, clarity=para.clarity, sharpen=para.sharpen,
            tone=para.tone if tone else 0.0,
            texture=para.texture if texture else 0.0,
            feature=para.feature if feature else 0.0,
        )

    debug: Optional[dict] = {} if debug_dir else None
    out = _retouch(bgr, mask, geo, para, eye_mask,
                   do_smooth=smooth, do_blemish=blemish,
                   do_brighten=brighten, do_sharpen=sharpen,
                   lips_mask=lips_mask, teeth_mask=teeth_mask,
                   smooth_op=smooth_operator,
                   structure_protect=structure_protect,
                   feature_band=feature_band,
                   debug=debug)

    sel = mask > 0.4
    diff = float(np.mean(np.abs(out.astype(np.float32) - bgr.astype(np.float32))[sel])) \
        if int(sel.sum()) > 0 else 0.0
    elapsed = (time.perf_counter() - started) * 1000.0
    logger.info(
        f"Beautify done: mode={mode} parse={'on' if parse_skin is not None else 'off'} "
        f"level={level} faces={len(faces)} "
        f"r_fine={_scale(geo.iod, 0.032, 2)}px iod={geo.iod:.1f}px "
        f"mask={float(mask.mean()):.3f} skin_diff={diff:.1f} "
        f"smooth={smooth} brighten={brighten} blemish={blemish} sharpen={sharpen} "
        f"adaptive={adaptive} strength={s:.2f} op={smooth_operator} "
        f"lips={'on' if lips_mask is not None else 'off'} "
        f"protect={'on' if structure_protect is not None else 'off'} "
        f"tone={para.tone:.2f} texture={para.texture:.2f} feature={para.feature:.2f} "
        f"in {elapsed:.0f}ms"
    )

    # 中间结果落盘（仅评测/调试路径）：对应「保存各中间结果」的验收要求，
    # 生产调用方不传 debug_dir 时完全不执行。
    if debug_dir:
        _dump_stage_images(debug_dir, bgr, mask, feature_band, debug, out)

    return _cv2_to_pil(out)
