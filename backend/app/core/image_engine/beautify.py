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

import dataclasses

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
    # ★ 影楼精修档：比 COMPLIANT 强得多，但仍然**只做色彩与质感**，不做几何改动。
    # 与 ARTISTIC 的区别：ARTISTIC 是「艺术化」（弱磨皮 + 通透，偏写真风），
    # STUDIO 是「影楼证件照精修」（强磨皮 + 匀肤 + 塑形 + 白净，偏出片风）。
    STUDIO = 5


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
    # ---- 磨皮算子尺度（相对瞳距）----
    # 【为什么放进参数表】「磨皮半径」不是常数而是**风格选择**：
    #   证件照要「毛孔仍在」（r≈0.055·iod，覆盖 3~6px 痘印尺度）；
    #   影楼精修要「皮肤像绸缎」（r≈0.10·iod，覆盖 8~14px 的色块与粗糙区），
    #   两者用同一套算子、只是尺度不同。写死成模块常量就只能二选一。
    # 默认值 = 历史常量（0.055 / 90 / 520），保证 COMPLIANT 档数值零漂移。
    fine_r: float = 0.055
    sigma_fine: float = 90.0
    eps_mid: float = 520.0
    # ---- 影楼精修专属强度（COMPLIANT 档全部为 0，等于关闭）----
    sculpt: float = 0.0    # 明暗塑形（高光提亮 + 结构阴影加深，做立体感）
    fill: float = 0.0      # 补光（把面部中低频阴影抬亮，去掉「脏/灰」）
    wb: float = 0.0        # 皮肤白平衡（去黄、补血气，向干净肤色靠）
    eye_polish: float = 0.0  # 眼妆级精修（眼白提净 + 虹膜清晰 + 睫毛加深）
    under_eye: float = 0.0   # 眼袋/泪沟提亮（影楼最见效的一步：去疲惫感）


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
_BRIGHTEN_GAIN = 0.50           # 提亮主项：brighten(≤0.08) × 余量 × 该系数 → 皮肤约 +2.6 级
                                # 【2026-10 从 0.15 提到 0.50】0.15 时实测皮肤区 ΔL 仅
                                # +0.14 级（8-bit 图上完全不可见），是「美颜看不出来」
                                # 最主要的原因。磨皮的物理上限只有 1.5 级（皮肤高频本身
                                # 就那点幅度），真正能让人「看起来气色好」的是提亮。
                                # 0.50 配 brighten=0.08 → +2.6 级：能看出通透感，
                                # 又不会把阴影抬平成「假白」（0.70/0.20 会到 +11 级）。
                                # 增益不受 _clamp_compliant 钳制，红线口径是 brighten ≤0.10。

@dataclass(frozen=True)
class _Budget:
    """单条处理链路可用的「改动预算」（通道单位 / 相对比例）.

    【为什么要把它做成对象】
    过去所有预算都写死成模块级常量（COMPLIANT_MAX_*），于是「精修档」只能
    靠改常量实现 —— 一改就同时改掉了证件照合规档的红线，回滚成本极高。
    现在把预算参数化：合规档用 `_COMPLIANT_BUDGET`（数值与历史常量完全一致），
    精修档用 `_STUDIO_BUDGET`（数值更强但仍在「不改骨相」的范围内）。
    两个档位共用同一条 `_retouch` 代码路径，行为差异只来自这份预算表，
    出问题只需换回 `_COMPLIANT_BUDGET`，不会碰到任何算法逻辑。
    """
    # ---- 皮肤（相对比例）----
    fine_remove: float = COMPLIANT_MAX_FINE_REMOVE   # 高频最多压掉多少
    blemish: float = COMPLIANT_MAX_BLEMISH           # 中频斑点最多压掉多少
    chroma: float = COMPLIANT_MAX_CHROMA             # 色度匀肤强度
    brighten: float = COMPLIANT_MAX_BRIGHTEN         # 提亮强度
    shine: float = COMPLIANT_MAX_SHINE               # 去油光强度
    clarity: float = COMPLIANT_MAX_CLARITY           # 通透感回填
    sharpen: float = COMPLIANT_MAX_SHARPEN           # 眼/眉局部锐化
    texture_reinject: float = TEXTURE_REINJECT_MAX   # 纹理回注比例上限
    feature_sharpen: float = COMPLIANT_MAX_FEATURE_SHARPEN   # 五官轮廓带峰值增益
    # ---- 局部美化（通道单位）----
    lip_a: float = COMPLIANT_MAX_LIP_A
    lip_b: float = COMPLIANT_MAX_LIP_B
    teeth_desat: float = COMPLIANT_MAX_TEETH_DESAT
    teeth_l: float = COMPLIANT_MAX_TEETH_L
    catchlight: float = EYE_CATCHLIGHT_LIFT
    # ---- 分区肤色修正（通道单位）----
    tone_l: float = COMPLIANT_MAX_TONE_SHIFT_L
    tone_a: float = COMPLIANT_MAX_TONE_SHIFT_A
    tone_b: float = COMPLIANT_MAX_TONE_SHIFT_B
    # ---- 提亮主项系数（brighten × 余量 × gain）----
    brighten_gain: float = _BRIGHTEN_GAIN


_COMPLIANT_BUDGET = _Budget()


def _scale_budget(budget: "_Budget", s: float) -> "_Budget":
    """把预算表按「还原度」滑杆缩放（只缩减法类预算）.

    【为什么预算也要乘滑杆】早期实现里，唇色（lip_a/lip_b）、牙齿
    （teeth_l/teeth_desat）、角膜反光（catchlight）都是模块常量，不随
    `strength` 变化。于是「还原度 0%」时皮肤磨皮确实归零了，但唇色仍然被加红、
    眼白仍然被提亮 —— 与函数文档承诺的「0 = 完全还原原图」矛盾
    （实测 strength=0 仍有个别像素差 19 级，就是这几处局部美化造成的）。
    现在它们统一进预算表，滑杆一拉到底时**所有**改动一起归零。

    纹理回注（texture_reinject）与五官轮廓（feature_sharpen）不缩放：
    它们不是「改动」而是「找回细节」，磨皮归零后 removed≈0，回注量自然为 0。

    【只缩「真的从预算表取用」的字段】_retouch 里 blemish/chroma/brighten/
    shine/clarity/sharpen 的强度来自 `para.*`（已被滑杆缩过），预算表里那几项
    只是留档不用。这里若也乘 s 会造成「双重缩放」，看起来生效了两次。
    """
    if s >= 1.0:
        return budget
    return dataclasses.replace(
        budget,
        fine_remove=budget.fine_remove * s,
        lip_a=budget.lip_a * s,
        lip_b=budget.lip_b * s,
        teeth_desat=budget.teeth_desat * s,
        teeth_l=budget.teeth_l * s,
        catchlight=budget.catchlight * s,
        tone_l=budget.tone_l * s,
        tone_a=budget.tone_a * s,
        tone_b=budget.tone_b * s,
        brighten_gain=budget.brighten_gain,
    )


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
    # 【2026-10 参数重标定：让 smooth 真正生效】
    # 依据：线上实测皮肤 ΔL 仅 +0.14 级、高频仅 -1.6%，用户反馈「美颜看不出来」。
    #
    # 四处结构性修复（详见 _retouch / _smooth_gate / _l0_smooth 注释）：
    #   1. sigma_color 10→90、r_fine 0.032→0.055·iod：磨皮算子能力 2.7%→65%
    #   2. gate 改自适应（按本图皮肤 P90 定标）：小图上不再恒为 0
    #   3. keep 下限随 smooth 联动：smooth 不再是「被 fine_keep 夹死的摆设」
    #   4. l0_smooth 默认关闭（实现有缺陷，会把噪声放大 39 倍）
    #
    # ★ clarity 必须为 0：实测它是**唯一**会抵消磨皮的轴 ——
    #   同一组参数下 clarity 0.00 → 高频 -4.6%（磨皮生效）
    #                  clarity 0.04 → 高频 +6.4%（越磨越锐，完全失效）
    #   「通透感」本意是回填明暗结构防糊平，但在证件照小图上它实际做的是
    #   全图锐化，把磨皮结果与皮肤噪点一起放大。五官清晰度改由 feature
    #   （轮廓带增强，只在眼/眉/鼻翼/唇缘 1~2px 生效）单独保证。
    BeautyLevel.COMPLIANT: _Params(0.12, 0.90, 0.18, 0.15, 0.08, 0.08, 0.99, 0.00, 0.06,
                                   0.55, 0.45, 0.85),
    # 内部对比测试档（会被 _clamp_compliant 压回上限，仅用于美学回归测试）
    BeautyLevel.MEDIUM:    _Params(0.78, 0.44, 0.68, 0.62, 0.55, 0.46, 0.85, 0.00, 0.22,
                                   0.85, 0.25, 0.70),
    BeautyLevel.HIGH:      _Params(0.92, 0.30, 0.85, 0.78, 0.72, 0.60, 0.78, 0.00, 0.30,
                                   0.95, 0.15, 0.85),
    # 艺术档（仅非证件照流程，apply_beautify 需显式 compliant=False 才真正生效；
    # 误传 compliant=True 时会被 _clamp_compliant 压回红线，等价自然微调）：
    # 不改骨相/五官几何，只在肤色、光影、通透与锐度上向「PS 中等修图」靠拢，
    # 纹理保留 ≥80% 仍由 fine_keep 下限保证。各轴必须**严格强于** COMPLIANT。
    BeautyLevel.ARTISTIC:  _Params(0.32, 0.72, 0.32, 0.28, 0.16, 0.14, 0.96, 0.00, 0.10,
                                   0.70, 0.35, 1.00),
    # ★★ 影楼精修档（STUDIO=5）：目标观感 = 「影楼后期出片」而不是「轻微磨皮」。
    # 参数语义与 COMPLIANT 完全一致（同一条 _retouch 链路），差别在三处：
    #   1) 预算表换成 _STUDIO_BUDGET（改动幅度上限更高）；
    #   2) 磨皮尺度 fine_r 0.055→0.10·iod（覆盖色块/粗糙区，不只痘印）；
    #   3) 打开四个新轴：sculpt 塑形 / fill 补光 / wb 肤色白平衡 / eye_polish 眼妆。
    # 仍然**不做**任何几何改动（无瘦脸/大眼/改眼距/改下颌），这是本项目的架构红线：
    # 整条链路只有 LAB 通道运算与蒙版合成，没有 warp/重采样步骤。
    BeautyLevel.STUDIO: _Params(
        smooth=0.72, fine_keep=0.30, blemish=0.82, chroma=0.78,
        shine=0.30, brighten=0.15, gamma=0.97, clarity=0.04, sharpen=0.22,
        tone=0.92, texture=0.55, feature=1.00,
        fine_r=0.13, sigma_fine=150.0, eps_mid=900.0,
        sculpt=0.85, fill=0.24, wb=0.42, eye_polish=0.90, under_eye=0.85,
    ),
}

# ---- 影楼精修预算表（只在 STUDIO 档使用；合规档仍走 _COMPLIANT_BUDGET）----
# 【为什么每一条都要写理由】这些数字直接决定「精修」与「过度修图」的边界：
#   * fine_remove 0.62：高频最多压掉 62%，毛孔仍在（纹理回注再补回 62%×60%）；
#     再高就会进入「塑料」区间（实测 0.75 时 100% 放大可见糊面）。
#   * blemish 0.80：中频色块压得更干净，但仍保留法令纹/鼻侧影的结构保护。
#   * chroma 0.62：影楼匀肤的核心（脸要「一个色」），但保留两颊与唇周色差。
#   * brighten 0.15 / brighten_gain 0.62：整体提亮约 +9 级。
#     【为什么从 0.22/0.85 收回来】首版实测 dL=+16.5、ITA 从 29 跳到 43.5
#     （跨过一个肤色档），观感是「粉白/发灰」而不是「白净」——
#     参考图的亮是「通透的亮」，靠的是匀净 + 塑形，不是把 L 拉满。
#   * fill 0.26：【为什么从 0.45 收回来】补光过强会把颧骨下方的自然阴影
#     一起抬平，脸变「平板」。现在只补最脏的那部分（鼻侧/下颌），
#     立体感交给 sculpt。
#   * sculpt 0.75：塑形是「白而不平」的关键 —— 受光面提、转折面压，
#     所以提亮可以收小，靠明暗差撑住体积感。
#   * wb 0.42（目标 b*=8、a*=13）：去黄到「干净暖白」而不是「粉白」。
#   * tone 预算 ±(10,7,7)：额头偏黄/两颊偏红/下颌偏暗要真的被拉平。
#   * lip_a 10 / lip_b 5：唇色「粉润」是精修最明显的辨识点。
#   * catchlight 7：眼白与角膜反光是「有神」的来源。
_STUDIO_BUDGET = _Budget(
    fine_remove=0.62,
    blemish=0.80,
    chroma=0.62,
    brighten=0.15,
    shine=0.30,
    clarity=0.05,
    sharpen=0.20,
    texture_reinject=0.60,
    feature_sharpen=0.16,
    lip_a=10.0,
    lip_b=5.0,
    teeth_desat=0.55,
    teeth_l=12.0,
    catchlight=7.0,
    tone_l=10.0,
    tone_a=7.0,
    tone_b=7.0,
    brighten_gain=0.62,
)

# 精修档默认参数快照：评测/扫掠脚本改完 `_PARAMS[STUDIO]` 后用它恢复，
# 避免「跑完扫掠忘了还原」导致后续出图用的是最后一组试验参数。
_STUDIO_DEFAULT = _PARAMS[BeautyLevel.STUDIO]


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
        # 尺度参数与精修轴：合规档必须把它们按「历史常量 + 全关」钳死，
        # 否则一旦上游误传 STUDIO 档位、或 _PARAMS 表被改坏，
        # 证件照就会用上精修的强磨皮半径 —— 这是本函数存在的全部意义。
        fine_r=_FINE_RADIUS_RATIO,
        sigma_fine=_SURFACE_SIGMA_FINE,
        eps_mid=_EPS_MID,
        sculpt=0.0,
        fill=0.0,
        wb=0.0,
        eye_polish=0.0,
        under_eye=0.0,
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
# 【2026-10 关键修复】sigma_color 曾为 10.0，导致磨皮整条链路空转。
#
# 先把磨皮的代数关系摆清楚（已用代码验算，max|diff|<2e-5）：
#     high = L - base_fine ;  mid = base_fine - base_mid
#     l_out = base_mid + high*atten + mid  ≡  base_fine + (L-base_fine)*atten
# 即：**磨皮上限完全由 base_fine 决定**，atten 只是「用多少」。
#     atten=1 → 原图；atten=0 → 完全取 base_fine。
# 实测不同 base_fine 能力（295×413 证件照，皮肤区高频压缩率）：
#     sigma=10  r=2  →   2.7%   ← 旧值，等于没磨皮
#     sigma=45  r=2  →  29.3%   ← 上一版修复，仍偏弱
#     sigma=90  r=5  →  71.9%   ← 本次采用，接近 Photoshop 表面模糊的观感
#     Gaussian s=3   →  86.7%   ← 过强，毛孔会被抹平
# 选 sigma=90 / r=5：能真正抹平痘印与粗糙感，同时保住毛孔尺度纹理。
# 半径随图幅自适应（小图 r=5 已覆盖 10px 尺度，大图按比例放大）。
_SURFACE_SIGMA_FINE = 90.0   # 高频 range 阈值（毛孔/痘印）
_SURFACE_SIGMA_MID = 22.0    # 中频 range 阈值（斑点/色块，阈值越大越保结构）
_SURFACE_MAX_WORK_R = 12     # 降采样后的工作半径上限：把代价锁在 d≤25 的邻域内
_SURFACE_MAX_STEP = 4        # 降采样上限 1/4（原 1/8）：降得越深保边性被抹得越平；
                             # 中频基底已改走全分辨率引导滤波（见 _retouch），此处只是兜底
# 细频磨皮半径比例（相对瞳距）。旧值 0.032×iod 在 295×413 上只有 2px，
# 覆盖不到痘印尺度（3~6px）；提到 0.055×iod（小图≈4px，3000px 大图≈27px），
# 与 sigma=90 配合才能真正压掉粗糙感。
_FINE_RADIUS_RATIO = 0.055
_FINE_RADIUS_MIN = 3

# 【磨皮门控阈值】gate = _ramp(|high|, GATE_LO, GATE_HI)
# 低于 GATE_LO 的起伏视为「毛孔/噪点」，全额压制；高于 GATE_HI 的视为
# 「褶皱/轮廓/痣」，原样保留。
#
# 【标定过程（记录踩过的坑）】
# (2.5, 14.0) 按 3000px 大图标定：大图皮肤高频 std 达 8~15 级，合理；
#   但证件照主路径是 295×413，缩图后高频 std 仅 1.3 级 → gate 恒为 0，
#   磨皮彻底失效（实测 removed 仅 0.066 级）。
# (0.8, 8.0)：sigma 提到 90、r_fine 提到 4px 后 high 的 std 涨到 7.4，
#   又顶到 GATE_HI=8 附近，gate 只剩 0.233，磨皮再次被挡住。
# (1.0, 22.0)：发现 GATE_LO=1 是硬伤 —— 8-bit L 通道最小非零差就是 1，
#   实测 |high| 中位数恰好 =1.000，而 _ramp 在 x<=lo 时返回 0，
#   于是**一半像素 gate 恒为 0**。
#
# 现在改为**按图像实际高频尺度自适应**（见 _smooth_gate），不再写死绝对阈值：
# 因为「毛孔多大」本身随图幅、压缩、肤质变化 —— 295×413 证件照的毛孔
# 高频只有 1~3 级，而合成测试图或 3000px 原片能到 7~15 级。
# 固定阈值必然在某一类图上失效，这正是过去三次标定都失败的根本原因。
_SMOOTH_GATE_LO = 0.0      # 保留字段：自适应时不再使用（见 _smooth_gate）
_SMOOTH_GATE_HI = 22.0     # 同上
# 自适应门控：以皮肤区 |high| 的 P90 作为「结构」基准，
# GATE_HI = P90 × 该系数（>1 保证真结构不被压），GATE_LO = 0。
_SMOOTH_GATE_P90_RATIO = 1.15
# 兜底：P90 极小时（纯色皮肤）用这个绝对下限，避免 gate 除零或全压
_SMOOTH_GATE_HI_MIN = 4.0

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


def _parse_skin_mask(bgr: np.ndarray,
                     face_boxes: Optional[Sequence[Tuple[int, int, int, int]]] = None
                     ) -> Optional[np.ndarray]:
    """
    用 19 类人脸解析得到精确皮肤蒙版 (0~1，与原图同尺寸).

    皮肤 = skin/nose/neck 类；唇/眼/眉/发/衣服天然不在集合内，
    因此能比「几何多边形 ∩ 肤色概率」更精确地保护唇色与五官边缘。

    【2026-11 关键修复：必须先裁到人脸再解析】
    旧实现把**整图**缩放到 512×512 送进网络。证件照场景下人脸通常只占画面
    8%~20%（1680×2240 原片里脸只有 199×264），缩到 512 后脸只剩 60×80，
    解析网络（在 CelebAMask-HQ 的 512 人脸裁图上训练）根本认不出五官级结构：
    实测整图解析在「脸型多边形内」的皮肤覆盖率只有 **0.24**，
    而先裁到人脸框（外扩 0.55×脸尺寸）再解析，覆盖率 **0.78** —— 差 3 倍多。
    覆盖率低的直接后果：皮肤蒙版大面积缺失 → 磨皮/匀肤/提亮全都被
    「蒙版 × 0」乘掉，用户看到的就是「美颜没效果」。这是历史上反复
    「加参数但不生效」的结构性根因，与算子强度无关。

    做法：按传入的 `face_boxes`（或内部检测的关键点框）裁人脸区域 →
    解析 → 把 128×128 标签图双线性放大回裁切尺寸 → 贴回整图坐标。
    `face_boxes` 为 None 且检测不到人脸时，退化为「整图解析」（旧行为）。

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

    def _run_parse(rgb_region: np.ndarray) -> np.ndarray:
        """对一块 RGB 区域做解析，返回同尺寸的皮肤概率图 (0~1)."""
        x_img = cv2.resize(rgb_region, (_PARSE_INPUT, _PARSE_INPUT),
                           interpolation=cv2.INTER_LINEAR).astype(np.float32)
        x = (x_img / 255.0 - _PARSE_MEAN) / _PARSE_STD
        x = np.transpose(x, (2, 0, 1))[None].astype(np.float32)
        out = sess.run([_get_parse_session.output_name],
                       {_get_parse_session.input_name: x})[0]   # 1,19,h,w
        labels = np.argmax(out, axis=1)[0]                       # h,w
        skin = np.isin(labels, _PARSE_SKIN_CLASSES).astype(np.float32)
        return cv2.resize(skin, (rgb_region.shape[1], rgb_region.shape[0]),
                          interpolation=cv2.INTER_LINEAR)

    try:
        rgb = bgr[:, :, ::-1]

        if face_boxes is None:
            face_boxes = _detect_face_boxes(bgr)

        if not face_boxes:
            # 退化路径：整图解析（几何正确，但小脸场景精度有限）
            mask = _run_parse(rgb)
            return np.clip(mask, 0.0, 1.0).astype(np.float32)

        mask = np.zeros((h, w), dtype=np.float32)
        for (bx0, by0, bx1, by1) in face_boxes:
            # 裁切框做边界夹紧，并保证最小边长（太小解析无意义）
            cx0, cy0 = max(0, int(bx0)), max(0, int(by0))
            cx1, cy1 = min(w, int(bx1)), min(h, int(by1))
            if cx1 - cx0 < 16 or cy1 - cy0 < 16:
                continue
            sub_skin = _run_parse(rgb[cy0:cy1, cx0:cx1])
            # 多张脸重叠时取最大值（不是相加，避免蒙版 >1）
            np.maximum(mask[cy0:cy1, cx0:cx1], sub_skin, out=mask[cy0:cy1, cx0:cx1])

        if float(mask.max()) < 0.05:
            logger.debug("Face-crop parsing produced empty skin mask; retry whole image.")
            mask = _run_parse(rgb)
        return np.clip(mask, 0.0, 1.0).astype(np.float32)
    except Exception as e:
        logger.warning(f"Face parsing inference failed ({e}); using skin-color mask fallback.")
        return None


# 人脸裁切框的外扩比例（相对人脸长边）。
# 【为什么是 0.55】实测 0.35/0.55/0.80 三档的解析质量几乎相同
# （脸型内皮肤覆盖率 0.779/0.776/0.779），说明外扩只要「够把脸和一点脖子
# 包进去」就够；再放大只是让 512 分辨率摊到更多无用像素上（脸反而变小）。
# 取 0.55 兼顾「多脸同框」与「脸贴边」两种极端构图。
_PARSE_CROP_PAD = 0.55


def _box_from_landmarks(lm, w: int, h: int) -> Tuple[int, int, int, int]:
    """单张脸的关键点包络 → 解析裁切框（含外扩，见 _PARSE_CROP_PAD）."""
    xs = [p.x for p in lm]
    ys = [p.y for p in lm]
    fx0, fx1 = min(xs) * w, max(xs) * w
    fy0, fy1 = min(ys) * h, max(ys) * h
    pad = max(fx1 - fx0, fy1 - fy0) * _PARSE_CROP_PAD
    return (int(max(0, fx0 - pad)), int(max(0, fy0 - pad)),
            int(min(w, fx1 + pad)), int(min(h, fy1 + pad * 1.35)))


def _detect_face_boxes(bgr: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """
    检测人脸裁切框（含外扩），供人脸解析使用.

    【为什么优先关键点而不是 Haar】Haar 的框比真实脸小一圈且常偏上；
    关键点包络更准，且下游本来就要跑关键点（结果复用，不额外耗时）。
    返回 [(x0, y0, x1, y1), ...]，坐标已夹紧在图内。
    """
    import cv2

    h, w = bgr.shape[:2]
    boxes: List[Tuple[int, int, int, int]] = []

    for lm in _detect_all_landmarks(bgr):
        boxes.append(_box_from_landmarks(lm, w, h))

    if boxes:
        return boxes

    # 降级：Haar 人脸框（比关键点粗糙，但总比整图解析好）
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    cascade_path = os.path.join(cv2.data.haarcascades,
                                "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return []
    min_size = max(24, min(h, w) // 20)
    found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                     minSize=(min_size, min_size))
    for (x, y, bw, bh) in found[:_MAX_FACES]:
        pad = max(bw, bh) * _PARSE_CROP_PAD
        boxes.append((int(max(0, x - pad)), int(max(0, y - pad)),
                      int(min(w, x + bw + pad)), int(min(h, y + bh + pad * 1.35))))
    return boxes


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


def _smooth_gate(high: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """磨皮门控：返回 0~1，1=该像素属于「结构」应完全保留，0=毛孔/噪点应压制.

    【为什么必须自适应，而不能写死阈值】
    「毛孔有多大」不是常数，它随三件事变化：
      * 图幅：295×413 证件照的皮肤高频 |high| 中位数只有 1~2 级；
        3000px 原片能到 8~15 级（同一张脸，不同输出尺寸）
      * 压缩/插值：LANCZOS 缩图会把高频抹平一截
      * 肤质：粗糙皮肤 vs 光滑皮肤本身差 2~3 倍
    过去三次写死阈值（2.5/14、0.8/8、1.0/22）全部在某一类图上失效，
    根因就是拿一个绝对数去卡一个相对量。

    【现在的做法】以**当前这张图皮肤区 |high| 的 P90** 作为「结构」基准：
      GATE_HI = P90 × 1.15   （略高于 P90，保证真结构——痣/睫毛根/鼻翼边——
                              落在门控上沿被完整保留）
      GATE_LO = 0            （8-bit L 通道最小非零差就是 1，LO 必须为 0，
                              否则中位数为 1 的像素会整片被判成结构）
    这样无论图幅/肤质如何，毛孔级起伏都稳定落在 [0, GATE_HI) 的压制区，
    而最大的那 10% 起伏保留 —— 磨皮强度只由 smooth_w 决定，尺度无关。
    """
    sel = mask > 0.4
    if int(sel.sum()) > 200:
        p90 = float(np.percentile(np.abs(high)[sel], 90.0))
    else:
        p90 = float(np.percentile(np.abs(high), 90.0))
    hi = max(_SMOOTH_GATE_HI_MIN, p90 * _SMOOTH_GATE_P90_RATIO)
    return _ramp(np.abs(high), 0.0, hi)


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
    # 蒙版羽化的半径很小（0.12×iod），Fast 版会自动退回精确路径，这里直接调用即可
    m = _guided_1_fast(gray, m, _scale(geo.iod, 0.12, 2), 4e-4)
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


def _under_eye_mask(bgr: np.ndarray, faces: List[list],
                    main: _FaceGeometry) -> Optional[np.ndarray]:
    """
    眼袋 / 泪沟带蒙版 (0~1)：下眼睑下方一条弧形带（不是整块眼周）.

    【为什么单独做一条带】眼袋和泪沟是证件照里「显疲惫、显老」的头号来源，
    但它们既不是皮肤高频噪声（磨皮压不掉），也不是色度斑块（匀肤改不动）——
    它们是**局部偏暗的凹陷阴影**，必须用「局部提亮 + 轻微去青」单独处理。
    整块眼周提亮会把卧蚕和眼型一起抹平（观感=假眼），所以只取
    「下眼睑轮廓向下 0.10~0.22×iod」这一条窄带，并按梯度保护卧蚕亮部。
    """
    import cv2

    h, w = bgr.shape[:2]
    iod = float(main.iod)
    m = np.zeros((h, w), dtype=np.uint8)

    for lm in faces:
        for idx in (_LEFT_LOWER_LID, _RIGHT_LOWER_LID):
            pts = _pts(lm, idx, w, h)
            if len(pts) < 3:
                continue
            # 把下眼睑轮廓整体下移，形成「带」，而不是填满眼下整块
            shifted = pts + np.array([0.0, iod * 0.16], dtype=np.float32)
            band = np.vstack([pts, shifted[::-1]]).astype(np.int32)
            cv2.fillPoly(m, [band], 255)

    if int(m.max()) == 0:
        return None
    # 轻微羽化：硬边会在眼下出现「两块亮斑」
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, iod * 0.03))
    return np.clip(m.astype(np.float32) / 255.0, 0.0, 1.0)


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
                    grid: int = TONE_GRID,
                    budget: "_Budget" = _COMPLIANT_BUDGET) -> Optional[np.ndarray]:
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
                float(np.clip(d[0], -budget.tone_l, budget.tone_l)),
                float(np.clip(d[1], -budget.tone_a, budget.tone_a)),
                float(np.clip(d[2], -budget.tone_b, budget.tone_b)),
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
                      strength: float,
                      budget: "_Budget" = _COMPLIANT_BUDGET) -> np.ndarray:
    """
    计算纹理回注量.

    removed: 磨皮真正拿走的高频量（high_original - high_after）
    guard:   强边保护（1=平坦皮肤可回注，0=强边不回注）
    strength: 回注比例（0~1，最终乘 budget.texture_reinject）
    budget:   预算表（合规档 0.55 / 精修档 0.62）

    【为什么不直接「全额还原」】全额还原等于没磨皮，磨皮对中频斑点/噪点的
    压制会一起消失。回注只补「毛孔尺度的细节」：用幅度软阈值把
    大起伏（痣、睫毛根、鼻翼边）排除在外，剩下的才是要找回的皮肤质感。
    """
    if strength <= 0.0:
        return np.zeros_like(removed)
    # 幅度门控：毛孔级起伏（≈1~6 级）吃满回注，更大起伏本来就没被压掉
    small = 1.0 - _ramp(np.abs(removed), 1.0, 12.0)
    return removed * (budget.texture_reinject * strength) * small * guard


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


# ============================================================ 套餐 A ①：#4 Fast Guided Filter
# 【来源】He & Sun, "Fast Guided Filter", arXiv:1505.00996 (2015)。
# 引导滤波本身是 O(N)（与半径无关），Fast 版再做**系数降采样**：
# 在低分辨率上估计线性系数 a/b，再上采样回全分辨率应用。
# 论文实测 s=4 时 >10× 加速且视觉几乎无差异。
#
# 【为什么本项目需要它】证件照常在 3000~4000px 原图上处理，_guided_1 的
# boxFilter 虽然 O(1)/像素，但在 4 个通道 × 多次调用下仍是整条链路的主要耗时。
# 换成 Fast 版后同样的观感、耗时降到 1/4~1/10。
#
# 【关键约束】降采样只用于**估计系数**，最后的应用（a*I+b）仍在全分辨率做，
# 所以不会像「先降采样滤波再上采样」那样把整图压软（那是本模块反复踩过的坑）。
_GUIDED_SUBSAMPLE = 4          # 系数估计的降采样比（论文推荐 4）
_GUIDED_SUBSAMPLE_MIN_PX = 512 # 图像小于该值时不做降采样（省不了多少，还多了插值）

def _guided_1_fast(guide: np.ndarray, src: np.ndarray, radius: int, eps: float,
                   subsample: int = _GUIDED_SUBSAMPLE) -> np.ndarray:
    """
    Fast Guided Filter（He & Sun 2015）：系数降采样版引导滤波.

    与 _guided_1 数值等价（差一个插值误差），但大图上快一个量级。
    radius 按**原分辨率**给出，内部按 subsample 折算到低分辨率。
    """
    import cv2

    r = max(1, int(radius))
    h, w = guide.shape[:2]
    s = max(1, int(subsample))
    # 小图 / 小半径：降采样收益不足，直接走精确版
    if s <= 1 or min(h, w) < _GUIDED_SUBSAMPLE_MIN_PX or r < s * 2:
        return _guided_1(guide, src, r, eps)

    hh, ww = max(1, h // s), max(1, w // s)
    g_small = cv2.resize(guide, (ww, hh), interpolation=cv2.INTER_AREA)
    r_small = max(1, int(round(r / s)))

    multi = src.ndim == 3
    if multi:
        p_small = cv2.resize(src, (ww, hh), interpolation=cv2.INTER_AREA)
    else:
        p_small = cv2.resize(src, (ww, hh), interpolation=cv2.INTER_AREA)

    # 在低分辨率上估计系数（这四步是引导滤波的全部代价所在）
    mean_i = _box(g_small, r_small)
    mean_p = _box(p_small, r_small)
    corr_i = _box(g_small * g_small, r_small)
    corr_ip = _box(g_small[..., None] * p_small, r_small) if multi else _box(g_small * p_small, r_small)

    var_i = corr_i - mean_i * mean_i
    if multi:
        cov_ip = corr_ip - mean_i[..., None] * mean_p
        a = cov_ip / (var_i[..., None] + float(eps))
        b = mean_p - a * mean_i[..., None]
    else:
        cov_ip = corr_ip - mean_i * mean_p
        a = cov_ip / (var_i + float(eps))
        b = mean_p - a * mean_i

    a = _box(a, r_small)
    b = _box(b, r_small)

    # 系数上采样回全分辨率，再用**原始 guide** 应用 —— 应用步全分辨率，
    # 所以不会引入「整图被插值压软」的系统性损失。
    if multi:
        a_full = np.stack([
            cv2.resize(a[..., c], (w, h), interpolation=cv2.INTER_LINEAR) for c in range(a.shape[2])
        ], axis=-1)
        b_full = np.stack([
            cv2.resize(b[..., c], (w, h), interpolation=cv2.INTER_LINEAR) for c in range(b.shape[2])
        ], axis=-1)
        return a_full * guide[..., None] + b_full
    a_full = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
    b_full = cv2.resize(b, (w, h), interpolation=cv2.INTER_LINEAR)
    return a_full * guide + b_full


# ============================================================ 套餐 A ②：#10 ITA° 肤色自适应
# 【来源】Chardon et al. (1991) Individual Typology Angle；
#        Fitzpatrick (1975) 皮肤分型；ITA° 现为皮肤色度学界标准量化指标。
#        ITA° = arctan((L* - 50) / b*) × 180/π
#        （L*/b* 取 CIELAB，D65 白点）
#
# 【为什么需要它】现有自适应只看「亮度 / 高频能量 / 红斑量」，缺一个
# **肤色深浅**轴。后果是同一套强度在深肤色上会把皮肤磨白、在浅肤色上
# 显得没效果 —— 这不是调参问题，是缺少一个与肤色类型挂钩的基准。
# ITA° 把肤色分成 6 档（very light → dark），据此缩放「提亮 / 磨皮 / 匀肤」：
#   * 深肤色：**大幅降低提亮与匀肤**（避免灰白、避免把暖调匀掉），磨皮也收敛
#     （深肤色高频对比更弱，同样强度下更容易糊）；
#   * 浅肤色：提亮与磨皮可正常吃满（但仍受红线钳制）。
#
# 【合规意义】证件照不得改变肤色类型。ITA° 自适应保证「同一档位在不同肤色上
# 做同等的**相对**修饰量」，而不是把所有人拉向同一个亮度 —— 后者才是"改肤色"。
_ITA_BINS = (
    # (ITA 上限, 名称, 提亮缩放, 磨皮缩放, 匀肤缩放)
    (-30.0, "dark",        0.55, 0.72, 0.60),
    (10.0,  "brown",       0.68, 0.82, 0.72),
    (28.0,  "tan",         0.82, 0.92, 0.85),
    (41.0,  "intermediate", 1.00, 1.00, 1.00),
    (55.0,  "light",       1.00, 1.00, 1.00),
    (90.0,  "very_light",  0.92, 1.00, 1.00),
)
ITA_REFERENCE_B = 18.0          # ITA 公式里的 b* 下限（防除零）


def skin_ita(lab: np.ndarray, mask: np.ndarray) -> Optional[float]:
    """
    计算皮肤区域的 ITA°（Individual Typology Angle）.

    ITA° = arctan((L* - 50) / b*) × 180/π，L*/b* 为皮肤像素中位数。
    OpenCV 的 LAB 是 0~255 尺度，L* 需按 100/255 折算、b* 需减去 128 还原符号。
    返回 None 表示皮肤像素不足，调用方跳过肤色自适应。
    """
    sel = mask > 0.5
    if int(sel.sum()) < 400:
        return None
    l_star = float(np.median(lab[:, :, 0][sel])) * 100.0 / 255.0
    b_star = float(np.median(lab[:, :, 2][sel])) - 128.0
    b_star = max(ITA_REFERENCE_B, b_star)      # 负 b*（偏蓝）按下限处理
    angle = np.degrees(np.arctan((l_star - 50.0) / b_star))
    return float(angle)


def _ita_skin_class(ita: float) -> str:
    """ITA° → 肤色类型名（Chardon 1991 的 6 档划分）."""
    for upper, name, *_ in _ITA_BINS:
        if ita < upper:
            return name
    return "very_light"


def _ita_scales(ita: float) -> Tuple[float, float, float]:
    """ITA° → (提亮缩放, 磨皮缩放, 匀肤缩放) 三元组."""
    for upper, _name, bright, smooth, chroma in _ITA_BINS:
        if ita < upper:
            return float(bright), float(smooth), float(chroma)
    return 0.92, 1.0, 1.0


# ============================================================ 套餐 A ③：#11 PatchMatch 痘印修补
# 【来源】Barnes et al., "PatchMatch: A Randomized Correspondence Algorithm for
#        Structural Image Editing", SIGGRAPH 2009；Telea (2004) FMM 修补。
#
# 【为什么需要它】现有祛瑕疵是「中频压制」——把痘印的对比度压下去，但**痘印
# 区域本身的像素仍然是痘印的颜色**，只是变淡。真正的祛痘是「用周围健康皮肤
# 的纹理把这块替换掉」，即 inpainting。
# 这里用 PatchMatch 的**核心思想**做一个轻量实现：
#   1) 只在「疑似痘印」的小连通域上做（不是整脸）；
#   2) 每个待修补块在环形邻域里随机+传播地找一个最相似的源块；
#   3) 按块中心距离羽化融合，避免出现补丁边。
# 相比 Telea/FMM（各向同性扩散，会把毛孔糊掉），PatchMatch 类方法能**搬来
# 周围的真实皮肤纹理**，修完仍有毛孔 —— 这正是证件照要的。
#
# 【红线】只修补「小、圆、孤立、比周围红/暗」的连通域（痘印/红肿），
# 面积超过 BLEMISH_PATCH_MAX_AREA 或长宽比过大的（痣、疤痕、皱纹）一律跳过。
# 单块面积占脸区比例上限。
# 【标定依据】证件照里一颗痘印的直径约 2~4mm，脸宽约 25mm → 占脸面积约
# 0.2%~0.8%；而痣/疤痕通常 ≥1.5%。取 0.30% 落在两者之间。
# 【为什么不是 0.12%】Top-Hat 检出的连通域会沿斑点边缘外扩一圈（实测
# r=7 的圆斑在 129k px 的脸上检出 239px，而圆的真实面积只有 154px），
# 0.12%（155px）会把**真痘印**卡掉。0.30% 给边缘扩展留了余量，
# 同时仍远小于痣/疤的面积。
BLEMISH_PATCH_MAX_AREA = 0.0030
BLEMISH_PATCH_MIN_AREA = 12       # 小于该像素数视为噪点，不修
BLEMISH_PATCH_MAX_ASPECT = 2.6    # 长宽比上限（痣/皱纹偏长，痘印近圆）
BLEMISH_PATCH_RADIUS_RATIO = 0.10 # 搜索环半径 = 瞳距 × 该比例
BLEMISH_PATCH_HALO = 1.6          # 修补时向外多取的上下文倍数（供融合羽化）
# 优先级门槛：mean_score × (0.6 + 0.4×圆度)。0.55 意味着「块内平均偏红/偏暗
# 强度 ≥ 0.55」才修 —— 对应「肉眼可见的痘印」，纹理起伏与胡茬阴影会被挡掉。
BLEMISH_PATCH_MIN_PRIORITY = 0.55


def _detect_blemish_blobs(bgr: np.ndarray, mask: np.ndarray,
                          geo: "_FaceGeometry") -> list:
    """
    检测「痘印型」小连通域（返回 [(x0,y0,x1,y1,score), ...]）.

    判据（全部要满足，宁可漏检也不误修）：
      1) 在皮肤蒙版内（mask > 0.5）；
      2) 相对周围皮肤**偏红**（a* 上偏）或**偏暗**（L 下偏）；
      3) 面积在 [MIN_AREA, 脸区面积 × MAX_AREA] 之间（排除大色块与痣）；
      4) 长宽比 < MAX_ASPECT（排除皱纹/疤痕/发丝）。
    """
    import cv2

    sel = mask > 0.5
    n_skin = int(sel.sum())
    if n_skin < 2000:
        return []

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    l_chan, a_chan = lab[:, :, 0], lab[:, :, 1] - 128.0

    # 【为什么用 Top-Hat 而不是「大半径基准相减」】
    # 大半径模糊做基准时，半径必须小于「要检测的斑点尺度」才不会把斑点本身
    # 平均进去；但基准半径又必须大于「脸部明暗过渡」才不会把鼻影/下颌阴影
    # 误判成瑕疵 —— 两个要求互相矛盾（实测合成样张上 87 个候选全是假阳性，
    # 真正的痘印还和阴影合并成一个大连通域被面积上限拒掉）。
    # 形态学 Top-Hat（原图 − 开运算）从定义上就只响应「比结构元更小的
    # 局部极值」，大尺度明暗结构在开运算里被完整保留、相减后自然为 0。
    # 这就是小斑点检测的经典做法，天然满足「只修痘印、不碰光影」。
    r_se = max(3, int(round(geo.iod * 0.10)))      # 结构元半径 ≈ 痘印尺度
    kernel = _ellipse_kernel(r_se)

    # 偏暗斑点：Black-Hat（闭运算 − 原图）
    dark_raw = cv2.morphologyEx(np.clip(l_chan, 0, 255).astype(np.uint8),
                                cv2.MORPH_BLACKHAT, kernel).astype(np.float32)
    # 偏红斑点：对 a* 做 Top-Hat（a* 上偏 = 局部更红）
    a_u8 = np.clip(a_chan + 128.0, 0, 255).astype(np.uint8)
    red_raw = cv2.morphologyEx(a_u8, cv2.MORPH_TOPHAT, kernel).astype(np.float32)

    # 【阈值必须自适应，不能用固定值】Top-Hat 的绝对响应随原图的纹理量、
    # 噪声水平、压缩质量变化极大：同一组固定阈值在干净样张上刚好，
    # 在高噪样张上会让 60% 的皮肤面积都超过阈值（实测合成样张 blackhat
    # 中位数就有 12 级，而真实照片通常 1~2 级）。固定阈值必然在部分图上
    # 要么一个都不修、要么整脸乱修。
    # 做法：以**皮肤区自身的分位数**为基准 —— 痘印是「皮肤里最突出的一小撮
    # 局部极值」，所以取 p97 作为「明显异常」的起点、p99.5 作为「吃满」。
    def _adaptive_ramp(raw: np.ndarray, lo_p: float, hi_p: float,
                       floor: float) -> np.ndarray:
        vals = raw[sel]
        if vals.size < 500:
            return np.zeros_like(raw)
        lo = max(float(floor), float(np.percentile(vals, lo_p)))
        hi = max(lo + 1.0, float(np.percentile(vals, hi_p)))
        return _ramp(raw, lo, hi)

    dark = _adaptive_ramp(dark_raw, 97.0, 99.7, 4.0)
    red = _adaptive_ramp(red_raw, 97.0, 99.7, 2.0)
    score = np.clip(np.maximum(red, dark), 0.0, 1.0) * sel

    # 【必须先平滑再阈值化】Top-Hat 对每个单像素极值都响应，皮肤的正常
    # 高频纹理会产生大量 1~2px 的「伪斑点」；直接阈值化后即使做 OPEN 也
    # 会剩下几百个碎块，把真正痘印挤出预算（实测合成样张 315 个候选，
    # 全是噪声碎块）。痘印的物理尺度是 2~4mm（≈结构元半径），所以先用
    # 同尺度的高斯把 score 图平滑，让「单点噪声」被平均掉、
    # 「痘印尺度的连续区域」保留下来。
    score = cv2.GaussianBlur(score, (0, 0), max(1.0, r_se * 0.45))

    cand = (score > 0.45).astype(np.uint8)
    if int(cand.sum()) < BLEMISH_PATCH_MIN_AREA:
        return []

    # 形态学去碎点 + 闭运算补内部空洞（痘印中心常被压得最暗，
    # 边缘过渡反而低于阈值，会形成环形；CLOSE 把它补成实心块）
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, _ellipse_kernel(max(1, r_se // 3)))
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, _ellipse_kernel(max(1, r_se // 2)))

    num, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    face_area = max(1.0, float(sel.sum()))
    max_area = max(64, int(face_area * BLEMISH_PATCH_MAX_AREA))

    # 【排序必须按「像不像痘印」而不是按面积】
    # 早期版本按面积降序，结果「大而弱的假阳性」（纹理起伏、胡茬阴影）
    # 会先吃掉全部预算，真正的痘印反而轮不到（实测合成样张上 87 个候选
    # 全是假阳性，真斑点被挤到预算外）。这里用「块内平均 score × 圆度」
    # 排序：越红/越暗、越接近圆形 → 越像痘印 → 越优先修。
    blobs = []
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if area < BLEMISH_PATCH_MIN_AREA or area > max_area:
            continue
        aspect = max(bw, bh) / max(1.0, float(min(bw, bh)))
        if aspect > BLEMISH_PATCH_MAX_ASPECT:
            continue
        # 块内平均强度（红/暗程度）+ 圆度加成 + 面积加成。
        # 【为什么要面积因子】痘印是「有面积的连续区域」，而残余噪声是
        # 「孤立小碎块」。纯按 mean_score 排序时，噪声碎块因局部对比更强
        # 反而排在前面（实测前 8 名全是 12~19px 的碎块）。面积用对数权重：
        # 面积翻倍只提升 ~0.1，不会让大色块压倒真实痘印。
        blob_mask = labels[y:y + bh, x:x + bw] == i
        mean_score = float(score[y:y + bh, x:x + bw][blob_mask].mean())
        roundness = 1.0 / max(1.0, aspect)     # 1.0=正圆，越小越细长
        area_bonus = min(1.0, 0.5 + 0.25 * np.log2(max(1.0, area / 16.0)))
        priority = mean_score * (0.5 + 0.3 * roundness + 0.2 * area_bonus)
        blobs.append((int(x), int(y), int(x + bw), int(y + bh), float(area), priority))

    blobs.sort(key=lambda b: -b[5])
    return blobs


def _patchmatch_fill(bgr: np.ndarray, region: np.ndarray,
                     geo: "_FaceGeometry") -> np.ndarray:
    """
    用 PatchMatch 思想修补单个矩形区域（region 为 uint8 待修补掩码）.

    对区域内的每个 patch，在环形邻域里做「随机搜索 + 邻域传播」找最相似的
    源块，把源块内容搬过来；最后按距离羽化融合，避免补丁边。
    只处理小区域（痘印级），代价与区域面积成正比，不影响整图性能。
    """
    import cv2

    ys, xs = np.nonzero(region)
    if len(ys) == 0:
        return bgr
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1

    # 上下文窗口（含修补区四周的健康皮肤，供匹配与融合）
    pad = max(3, int(geo.iod * BLEMISH_PATCH_RADIUS_RATIO * BLEMISH_PATCH_HALO))
    h, w = bgr.shape[:2]
    wy0, wy1 = max(0, y0 - pad), min(h, y1 + pad)
    wx0, wx1 = max(0, x0 - pad), min(w, x1 + pad)

    win = bgr[wy0:wy1, wx0:wx1].astype(np.float32)
    rmask = (region[wy0:wy1, wx0:wx1] > 0)
    if not bool(rmask.any()):
        return bgr

    # 目标 patch：用「区域外的健康像素」估计其颜色，作为匹配模板
    healthy = ~rmask
    if int(healthy.sum()) < 20:
        return bgr
    target = np.array([float(win[:, :, c][healthy].mean()) for c in range(3)], np.float32)

    # 候选源：窗口内、与目标中心距离 > 2×区域半径、且本身健康的像素
    cy, cx = (y0 + y1) / 2.0 - wy0, (x0 + x1) / 2.0 - wx0
    rad = max(3.0, 0.5 * max(y1 - y0, x1 - x0))
    yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    src_ok = healthy & (dist > rad * 2.0)
    if int(src_ok.sum()) < 20:
        return bgr

    # 匹配：在候选里找颜色最接近目标、且局部梯度（纹理量）最接近的源块中心
    gray = cv2.cvtColor(win.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    tex = np.sqrt(gx * gx + gy * gy)
    tex_med = float(np.median(tex[src_ok]))

    color_cost = np.sqrt(((win - target) ** 2).sum(axis=2))
    tex_cost = np.abs(tex - tex_med)
    cost = color_cost + 0.8 * tex_cost
    cost[~src_ok] = np.inf
    sy, sx = np.unravel_index(int(np.argmin(cost)), cost.shape)
    if not np.isfinite(cost[sy, sx]):
        return bgr

    # 源块偏移量：把源块内容平移到目标位置
    dy, dx = int(cy - sy), int(cx - sx)
    shifted = np.empty_like(win)
    src_y0, src_x0 = max(0, -dy), max(0, -dx)
    src_y1 = min(win.shape[0], win.shape[0] - dy)
    src_x1 = min(win.shape[1], win.shape[1] - dx)
    if src_y1 <= src_y0 or src_x1 <= src_x0:
        return bgr
    shifted[src_y0 + dy:src_y1 + dy, src_x0 + dx:src_x1 + dx] =         win[src_y0:src_y1, src_x0:src_x1]
    # 平移越界处用目标色兜底
    shifted[shifted.sum(axis=2) == 0] = target

    # 羽化融合：按到修补区边界的距离做软过渡（避免补丁边）
    feather = cv2.GaussianBlur(rmask.astype(np.float32), (0, 0), max(1.0, rad * 0.55))
    feather = np.clip(feather, 0.0, 1.0)[..., None]

    out = bgr.copy()
    merged = win * (1.0 - feather) + shifted * feather
    out[wy0:wy1, wx0:wx1] = np.clip(merged, 0, 255).astype(np.uint8)
    return out


def _blemish_patch_repair(bgr: np.ndarray, mask: np.ndarray,
                          geo: "_FaceGeometry", strength: float) -> np.ndarray:
    """
    痘印修补总入口：检测 → 逐块 PatchMatch 修补.

    strength ∈ [0,1] 控制「修多少块」：先修分数最高的块，累计面积不超过
    皮肤面积的 BLEMISH_PATCH_MAX_AREA × 8（防止把整脸当痘印修掉）。
    """
    if strength <= 0.0:
        return bgr
    blobs = _detect_blemish_blobs(bgr, mask, geo)
    if not blobs:
        return bgr
    # 只保留「明显像痘印」的候选：优先级低于阈值的多是纹理起伏/胡茬阴影，
    # 修它们等于把正常皮肤纹理抹掉（证件照红线：不许丢纹理）。
    blobs = [b for b in blobs if b[5] >= BLEMISH_PATCH_MIN_PRIORITY]
    if not blobs:
        return bgr

    face_area = max(1.0, float((mask > 0.5).sum()))
    budget = face_area * BLEMISH_PATCH_MAX_AREA * 8.0 * strength
    used = 0.0
    out = bgr
    for (x0, y0, x1, y1, area, _prio) in blobs:
        if used + area > budget:
            continue      # 不是 break：小的斑点可能还塞得进剩余预算
        region = np.zeros(mask.shape, np.uint8)
        # 稍微外扩 1px，把痘印边缘的半透明过渡也一起换掉
        region[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1] = 255
        out = _patchmatch_fill(out, region, geo)
        used += area
    if used > 0:
        logger.debug(f"Blemish patch repair: {len(blobs)} candidates, repaired area {used:.0f}px")
    return out


# ============================================================ 套餐 A ④：#26 ArcFace 身份门禁
# 【来源】Deng et al., "ArcFace: Additive Angular Margin Loss for Deep Face
#        Recognition", CVPR 2019。用 ArcFace 的 embedding 余弦相似度衡量
#        「修图前后还是不是同一个人」。
#
# 【为什么这是证件照最重要的一道保险】前面所有红线都是「参数上限」，
# 但参数合规 ≠ 结果一定保真：极端光照、极端肤色、模型降级都可能让观感跑偏。
# ArcFace 给出的是**可审计的数字**（相似度 0.97 vs 0.83），可以：
#   * 出图前自动校验，低于阈值直接拒绝（或自动降强度重跑）；
#   * 写进结果元信息，随图一起留档，事后可复查；
#   * 作为回归测试的硬断言（新算子不许把相似度拉低）。
#
# 【阈值标定（本机实测，dsh_p1.png，arcface_resnet.onnx）】
#   原图 vs 原图            1.0000
#   轻度保边平滑            0.99998
#   重度双边+高斯（修过头） 0.9898
#   局部放大眼区（大眼）    0.9524
#   整体横向压缩（瘦脸）    0.9950  ← 见下方说明
#   另一个人                0.8479
# 据此把门禁设在「同人下限」与「异人上限」之间：
#   FAIL < 0.90 —— 已接近异人区间，说明改动幅度到了会影响识别的程度；
#   WARN < 0.96 —— 已出现可测的身份漂移（如大眼类局部几何改动）。
# 注意：本门禁是**保真度地板**，不是几何检查器 —— 它证明"还是这个人"，
# 但瘦脸/大眼这类几何改动在本项目里根本不实现（架构上就没有 warp 环节），
# 所以不需要靠它拦几何改动。它的真实职责是拦住「模型/参数跑飞导致的脸变形」。
IDENTITY_SIM_THRESHOLD = 0.90     # 出图门禁阈值（低于即判定改动过大）
IDENTITY_SIM_WARN = 0.96          # 低于此值记录警告（仍出图）
_ARC_MODEL_FILE = "arcface_resnet.onnx"
_ARC_INPUT = 112                  # ArcFace 标准输入尺寸
_ARC_DISABLED = False


def _arc_model_path() -> str:
    global _MODELS_DIR
    if _MODELS_DIR is None:
        _MODELS_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "models",
        )
    return os.path.join(_MODELS_DIR, _ARC_MODEL_FILE)


@lru_cache(maxsize=1)
def _get_arc_session():
    """ArcFace ONNX 会话单例（不可用时抛异常，调用方永久降级）."""
    import onnxruntime as ort

    path = _arc_model_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"arcface model not found at {path}")
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    _get_arc_session.input_name = sess.get_inputs()[0].name
    _get_arc_session.output_name = sess.get_outputs()[0].name
    logger.info(f"ArcFace identity model ready: {path}")
    return sess


def _face_crop_for_arc(bgr: np.ndarray, faces: List[list]) -> Optional[np.ndarray]:
    """按关键点取「对齐后的人脸 112×112」供 ArcFace 推理（用眼睛做旋转对齐）."""
    import cv2

    if not faces:
        return None
    h, w = bgr.shape[:2]
    lm = faces[0]
    try:
        left = _pts(lm, _LEFT_EYE, w, h).mean(axis=0)
        right = _pts(lm, _RIGHT_EYE, w, h).mean(axis=0)
    except Exception:
        return None
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        return None

    # 两眼连线角度 → 旋转对齐（ArcFace 训练时人脸是正立的）
    dy, dx = float(right[1] - left[1]), float(right[0] - left[0])
    angle = float(np.degrees(np.arctan2(dy, dx)))
    iod = float(np.linalg.norm(right - left))
    if iod < 4.0:
        return None

    cx, cy = float((left[0] + right[0]) / 2.0), float((left[1] + right[1]) / 2.0)
    # 以瞳距为尺度取方形窗口（ArcFace 训练裁切的典型比例）
    half = iod * 1.9
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    M[0, 2] += _ARC_INPUT / 2.0 - cx
    M[1, 2] += _ARC_INPUT / 2.0 - cy
    scale = _ARC_INPUT / (2.0 * half)
    M = cv2.getRotationMatrix2D((cx, cy), angle, scale)
    M[0, 2] += _ARC_INPUT / 2.0 - cx
    M[1, 2] += _ARC_INPUT / 2.0 - cy
    crop = cv2.warpAffine(bgr, M, (_ARC_INPUT, _ARC_INPUT),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return crop


def _arc_embedding(bgr: np.ndarray, faces: List[list]) -> Optional[np.ndarray]:
    """ArcFace embedding（L2 归一化）；模型缺失/推理失败返回 None."""
    global _ARC_DISABLED
    if _ARC_DISABLED:
        return None
    try:
        sess = _get_arc_session()
    except Exception as e:
        _ARC_DISABLED = True
        logger.warning(f"ArcFace disabled ({e}); identity gate skipped.")
        return None

    crop = _face_crop_for_arc(bgr, faces)
    if crop is None:
        return None
    try:
        # 【预处理必须按模型实际签名来】本仓库的 arcface_resnet.onnx 是
        # TF/Keras 导出的 **NHWC + 0~255 原始像素**（不做 mean/std 归一化），
        # 输入 shape 为 [N,112,112,3]。按 ArcFace 论文的 NCHW + (x-127.5)/128
        # 喂进去会直接报 INVALID_ARGUMENT（dimensions 不匹配）。
        # 因此这里读模型的真实输入 shape 决定布局与归一化，兼容两种导出风格。
        in_shape = sess.get_inputs()[0].shape
        nhwc = len(in_shape) == 4 and int(in_shape[-1]) == 3
        rgb = crop[:, :, ::-1].astype(np.float32)          # BGR → RGB
        if nhwc:
            x = rgb[None].astype(np.float32)               # [1,H,W,3]，0~255
        else:
            x = np.transpose((rgb - 127.5) / 128.0, (2, 0, 1))[None].astype(np.float32)
        out = sess.run([_get_arc_session.output_name],
                       {_get_arc_session.input_name: x})[0]
        v = np.asarray(out, np.float32).reshape(-1)
        n = float(np.linalg.norm(v))
        if n < 1e-6:
            return None
        return v / n
    except Exception as e:
        logger.warning(f"ArcFace inference failed: {e}")
        return None


def identity_similarity(before: np.ndarray, after: np.ndarray,
                        faces: List[list]) -> Optional[float]:
    """
    美颜前后的身份相似度（ArcFace cosine）.

    返回 None 表示模型不可用 / 没检测到人脸 —— 此时调用方**不阻断出图**
    （降级不阻断），但会在元信息里标注 identity_check="skipped"。
    """
    e1 = _arc_embedding(before, faces)
    if e1 is None:
        return None
    # 结果图上重新检测关键点（美颜后位置基本不变，但重新检测更严谨）
    faces_after = _detect_all_landmarks(after)
    e2 = _arc_embedding(after, faces_after if faces_after else faces)
    if e2 is None:
        return None
    return float(np.clip(np.dot(e1, e2), -1.0, 1.0))


# ============================================================ 套餐 B ①：#5 L0 梯度最小化平滑
# 【来源】Xu, Lu, Xu, Jia, "Image Smoothing via L0 Gradient Minimization",
#        SIGGRAPH Asia 2011。
#        能量：min_S  ||S - I||² + λ · #{ p | |∇S_p| ≠ 0 }
#        即「只保留数量有限的强边，其余梯度全部压到 0」。
#
# 【为什么适合证件照】L0 的产物是**分段常数**的：皮肤被压成平整色块，
# 但鼻翼/眼睑/下颌线的强边被完整保留。它比双边滤波更"干净"（双边会留下
# 光晕与阶梯），比引导滤波更"彻底"（引导滤波总会残留一点中频起伏）。
# 用在「中频瑕疵层」上，可以做到「痘印完全消失、骨相一根不少」。
#
# 【为什么不直接当磨皮】L0 输出的分段常数图有轻微"卡通感"，直接出图会假。
# 正确用法是本项目的频率分离：只用 L0 提取**中频基底**（base_mid），
# 高频层仍走表面模糊，最后 l_out = base_mid + high + mid。
L0_LAMBDA = 2.0e-3      # λ 越小越平滑；2e-3 在 0~1 归一化亮度上保留主要结构
L0_KAPPA = 2.0          # 梯度收缩系数（论文推荐 2）
L0_MAX_ITER = 4         # 交替优化迭代次数（论文 3~5 次足够收敛）
# 【性能闸门】L0 要对整幅图做 4 次 FFT（实测 600×800=178ms、900×1200=450ms、
# 1500×2000=1366ms），在 3000px 原图上会吃掉整个请求预算。
# 因此只在像素数 ≤ 该阈值时启用；超过则自动降级到 Rolling Guidance
# （实测同尺寸 18ms，观感接近，只是痘印压得没 L0 那么"干净"）。
L0_MAX_PIXELS = 1_200_000


def _poisson_solve(rhs: np.ndarray, beta: float,
                   n_jacobi: int = 12) -> np.ndarray:
    """
    解 (I + β·Δ) S = rhs（Neumann 边界），用**阻尼 Jacobi** 迭代.

    【为什么不用 FFT】L0 的泊松方程带 Neumann 边界，必须用 DCT 而不是 FFT；
    FFT 版本会引入周期性边界 + 频域除零，实测误差 0.4~6.0（解析解验证），
    是噪声被放大的直接原因。这里改用最朴素但**数值上必然收敛**的 Jacobi：
    (I + βΔ) 是对角占优的对称正定矩阵（β 越大越接近纯 Δ，条件数变差，
    所以配 β 的 continuation 一起用），每次迭代只是 4 邻域平均。

    【代价】每次迭代 O(N)，12 次迭代在 1200×900 上约 0.15s；
    L0 只在「中频基底」上调用一次且带像素预算（L0_MAX_PIXELS），可接受。
    """
    h, w = rhs.shape[:2]
    # ==================================================================
    # 【2026-10 彻底修复：改用 DCT 精确解】
    #
    # 历史问题（两次都踩在同一个坑上）：
    #   a) Jacobi 只迭代 12 次 —— 远未收敛。240×240 上需要数百次迭代才能
    #      把低频误差压下去，12 次相当于「只做了 12 次 4 邻域模糊」，
    #      于是边缘被反复抹平、噪声被放大（实测 edge 0.300→0.030，
    #      noise 0.0120→0.4707，放大 39 倍）。
    #   b) 迭代系数里混入 continuation 的 β（1→2→4→8），β 越大越接近
    #      纯均值滤波，进一步摧毁解。
    #
    # 正解：带 Neumann 边界的 (I + βΔ) 是**可对角化**的，
    # 用 DCT-II（其基函数天然满足零法向导数 = Neumann 边界）一步求精确解：
    #     S = IDCT( DCT(rhs) / (1 + β·λ_kl) )
    #   λ_kl = 4·sin²(πk/2H) + 4·sin²(πl/2W)   ← 离散 Laplacian 的特征值
    # 无迭代、无收敛问题、O(N log N)，且严格满足边界条件。
    # ==================================================================
    import cv2 as _cv2

    beta_f = max(1e-6, float(beta))
    # 离散 Laplacian 在 DCT 基下的特征值
    ky = np.arange(h, dtype=np.float32)
    kx = np.arange(w, dtype=np.float32)
    lam_y = 4.0 * np.sin(np.pi * ky / (2.0 * h)) ** 2
    lam_x = 4.0 * np.sin(np.pi * kx / (2.0 * w)) ** 2
    eig = 1.0 + beta_f * (lam_y[:, None] + lam_x[None, :])

    dct = _cv2.dct(rhs.astype(np.float32))
    sol = _cv2.idct(dct / eig)
    return np.clip(sol, 0.0, 1.0).astype(np.float32)


def _l0_smooth(gray01: np.ndarray, lam: float = L0_LAMBDA,
               kappa: float = L0_KAPPA, n_iter: int = L0_MAX_ITER) -> np.ndarray:
    """
    L0 梯度最小化平滑（Xu, Lu, Xu, Jia, SIGGRAPH Asia 2011）.

    交替优化：
      ① 固定 S，把梯度幅值 ≤ λ 的全部置零（这就是 L0 的"硬"所在）；
      ② 固定 g，解 (I + βΔ)S = I + β∇·g 重建图像；
      ③ β 逐轮加倍、λ 逐轮减半（continuation），加速收敛。

    输入/输出均为 0~1 的 float32 单通道。
    """
    s = np.clip(gray01.astype(np.float32), 0.0, 1.0)
    # 【2026-10 修复】原实现每轮用「上一轮已修改的 s」当泊松方程输入，
    # 误差随迭代累积：实测在「强边 + 弱噪声」图上
    #   边缘 0.300 → 0.030（被抹掉 90%，本该完整保留）
    #   噪声 0.0120 → 0.4707（被放大 39 倍，本该压平）
    # 与「分段常数基底」的设计意图完全相反，生产上表现为出图高频 +20.9%。
    # 论文的交替优化里泊松重建输入始终是**原始图像 I**：
    #   (I + βΔ)S = I + β·∇·g
    # 而不是上一轮的 S。这里保存 src 并始终用它。
    src = s.copy()
    beta = 1.0
    cur_lam = float(lam)

    for _ in range(max(1, int(n_iter))):
        # ① 前向差分梯度（基于当前估计 s）
        gx = np.zeros_like(s)
        gy = np.zeros_like(s)
        gx[:, :-1] = s[:, 1:] - s[:, :-1]
        gy[:-1, :] = s[1:, :] - s[:-1, :]

        # L0 硬阈值：幅值不超过 λ 的梯度直接归零
        keep = (gx * gx + gy * gy) > (cur_lam * cur_lam)
        gx = gx * keep
        gy = gy * keep

        # ② 散度 ∇·g（后向差分，与上面的前向差分配对，保证正定性）
        div = np.zeros_like(s)
        div[:, :] = gx
        div[:, 1:] -= gx[:, :-1]
        div[:, :] += gy
        div[1:, :] -= gy[:-1, :]

        # ③ 解 (I + βΔ)S = I + β·div —— 输入恒为原始 src，不是上一轮的 s
        rhs = src + beta * div
        s = _poisson_solve(rhs, beta)
        s = np.clip(s, 0.0, 1.0)

        # continuation：β 增大（更接近纯泊松）、λ 减小（保留更多细节）
        beta *= kappa
        cur_lam /= kappa

    return s.astype(np.float32)


# ============================================================ 套餐 B ②：#7 Rolling Guidance Filter
# 【来源】Zhang, Shen, Xu, Jia, "Rolling Guidance Filter",
#        ECCV 2014。
#        迭代：J^{t+1} = GuidedFilter(guide=J^t, src=I, r, ε)
#        第一轮用 σ_s 尺度做保边平滑，随后逐轮用**上一轮结果**当引导，
#        于是「小于 σ_s 的结构」被逐步抹掉，「大于 σ_s 的结构」稳定保留。
#
# 【为什么需要它】L0 与双边都是「单尺度」：要么把 3px 的痘印和 30px 的
# 鼻影一起保留，要么一起抹掉。Rolling Guidance 明确按**尺度**分离：
# 我们只抹「小于毛孔-痘印尺度」的结构，鼻影/颧骨/法令纹这些大尺度结构
# 在迭代中会自然稳定下来 —— 这是「磨皮但不失立体感」的论文级解法。
ROLLING_ITER = 3            # 迭代轮数（论文 3~4 轮收敛）
ROLLING_SIGMA_RATIO = 0.035 # 起始尺度 = 瞳距 × 该比例（≈毛孔-痘印尺度）


def _rolling_guidance(gray: np.ndarray, sigma_s: int,
                      n_iter: int = ROLLING_ITER,
                      eps: float = _EPS_MID) -> np.ndarray:
    """
    Rolling Guidance Filter（Zhang et al. 2014）.

    gray 为 0~255 float32；sigma_s 是「要抹掉的结构尺度」（像素）。
    返回同尺寸平滑结果：小于 sigma_s 的起伏被消除，更大的结构保持不变。
    """
    guide = gray.astype(np.float32).copy()
    r = max(1, int(sigma_s))
    for _ in range(max(1, int(n_iter))):
        guide = _guided_1_fast(guide, gray, r, eps)
    return guide.astype(np.float32)


# ============================================================ 套餐 B ③：#9 Retinex 光照归一化
# 【来源】Land & McCann (1971) Retinex 理论；Jobson et al. (1997)
#        "Properties and Performance of a Center/Surround Retinex"（SSR/MSR）；
#        Rahman et al. (1996) MSRCR（带色彩恢复）。
#        模型：I = R ⊙ L（观测 = 反射率 × 光照），R = I / L，
#        L 用多尺度高斯近似：L = Σ w_i · (I * G_{σ_i})
#
# 【为什么证件照最需要它】证件照最大的一类废片是「暗光自拍/背光」：
# 一侧脸亮一侧脸暗、整体偏黄。这不是瑕疵，是**光照不均**。
# 磨皮/匀肤都修不了光照 —— 必须先把光照层分离出来、拉平、再乘回反射率。
# 与「提亮」的区别：提亮是整体加法（会把亮的更亮），Retinex 是**按光照分布
# 归一化**（暗部抬得多、亮部几乎不动），且能顺带去掉偏色（MSRCR 的 color restore）。
#
# 【红线】只在 LAB 的 L 通道上做光照归一化，a/b 只做「去偏色」的轻度校正，
# 避免把肤色本身改掉（Retinex 在 RGB 上跑会严重改变色相，是常见误用）。
RETINEX_SCALES = (15, 80, 250)   # 三个高斯尺度（论文经典配置）
RETINEX_MAX_LIFT = 22.0          # 暗部最大抬升（L 级），防止过曝与"灰雾感"
# 【2026-10 从 0.35 降到 0.12】0.35 时实测把皮肤 a* 削掉 2.13 级
# （原图 a=14.6 → 12.5），血色被一起去掉，出图观感「苍白/发灰」——
# 这正是用户反馈「白底看着不自然」的色度来源之一。
# 去偏色的目标是「去掉环境光的整体偏色」（低频），不是「把脸洗白」；
# 0.12 只压掉明显偏色（实测 Δa -1.2 以内），保留脸颊/唇周的血色。
RETINEX_COLOR_RESTORE = 0.12     # a/b 去偏色强度（MSRCR 的 color restoration 系数）
# 【ITA 守卫（关键红线）】Retinex 会整体抬 L，实测 strength=1.0 时把皮肤
# ITA° 从 33.9 推到 41.3 —— 正好跨过 intermediate/light 的档位边界（41），
# 也就是「改肤色类型」，证件照不允许。
# 所以归一化后必须复核 ITA°：若抬升超过 ITA_MAX_LIFT（3°）就按比例回收强度。
# 3° 的依据：ITA 六档每档约 12~14°，3° 不到 1/4 档，肉眼不可辨但仍能修光照。
ITA_MAX_LIFT = 3.0


def _retinex_normalize(bgr: np.ndarray, mask: Optional[np.ndarray] = None,
                       strength: float = 1.0) -> np.ndarray:
    """
    多尺度 Retinex 光照归一化（在 LAB 亮度层上做，含轻度去偏色）.

    strength ∈ [0,1]：0 = 原图，1 = 完整归一化（仍受 RETINEX_MAX_LIFT 钳制）。
    只作用于 mask 内（皮肤），蒙版外原样返回 —— 背景/衣服的光照不该被"校正"。
    """
    import cv2

    if strength <= 0.0:
        return bgr

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    l_chan = lab[:, :, 0]

    # 多尺度光照估计（每层都用快速模糊近似高斯，大半径下等价且快一个量级）
    log_l = np.zeros_like(l_chan)
    for sigma in RETINEX_SCALES:
        log_l += np.log1p(np.clip(_fast_blur(l_chan, int(sigma)), 1.0, None))
    log_l /= float(len(RETINEX_SCALES))

    # 反射率 = log(I) - log(L)，再映射回 0~255 尺度
    refl = np.log1p(np.clip(l_chan, 1.0, None)) - log_l
    refl = refl - float(np.mean(refl))          # 去均值，保持整体曝光不变
    gain = 255.0 / max(1e-3, float(np.percentile(np.abs(refl), 99)))
    refl_l = np.clip(l_chan + refl * gain * 0.5, 0.0, 255.0)

    # ==================================================================
    # 【2026-10 修复：抬升方向反了 —— 暗部不抬、亮部抬满】
    #
    # 旧实现直接对 refl 取正部当抬升量：
    #     lift = clip(refl_l - l_chan, 0, MAX)
    # 但 refl = log(I) - log(L) 是「反射率」，与光照 L 反相关：
    #     暗区 log(I) 小 → refl 为负 → clip(0) 后**抬升为 0**
    #     亮区 log(I) 大 → refl 为正 → 被放大到 MAX
    # 实测（半明半暗测试图）：lift 暗区 0.00、亮区 22.00 —— 与
    # 「Retinex 归一化应抬暗部、亮部不动」的设计目标完全相反，
    # 结果是画面越提亮越亮、暗部反而更脏（观感=发灰 + 锐化感）。
    # 这也是 Retinex 开启时出图高频 +7.9% 的来源（它在放大亮区细节）。
    #
    # 正确做法：抬升量应来自**光照不足的程度**，而不是反射率符号：
    #     deficit = 1 - L_norm        ← 光照越低（暗）越接近 1
    #     lift    = deficit × MAX × strength
    # 这样暗区吃满预算、亮区几乎不动，且天然单调、不会反向。
    # ==================================================================
    l_norm = np.clip(l_chan, 0.0, 255.0) / 255.0
    # 【光照图必须足够平滑】抬升量只能反映「这块区域整体暗」，不能反映毛孔起伏。
    # 旧版用 min(shape)*0.02（295×413 上仅 5px），平滑不足 →
    # 逐像素差异被当成「光照不足」抬亮，等于把噪点/纹理一起放大
    # （实测出图高频 +8.3%，观感是「发灰 + 锐化」）。
    # 这里取短边的 8%（小图≈23px），只保留低频光照分布。
    lr = max(9, int(min(l_chan.shape) * 0.08))
    l_smooth = _fast_blur(l_norm, lr)
    # 【指数必须接近 1】曾用 ^1.5「更偏向暗部」，但指数越大，抬升量的空间
    # 变化越陡 —— 相邻像素抬升量差得越多，输出高频就越高（实测 ^1.5 时
    # 高频 +9.7%，改线性后降到 +6.3%）。光照归一化的目标是「整体变亮」，
    # 不是「局部对比增强」，所以用线性 + 平滑光照图。
    deficit = np.clip(1.0 - l_smooth, 0.0, 1.0)
    lift = np.clip(deficit * RETINEX_MAX_LIFT * float(strength), 0.0, RETINEX_MAX_LIFT)
    l_new = np.clip(l_chan + lift, 0.0, 255.0)

    # 去偏色（MSRCR 思路）：把 a/b 的**低频**部分往中性拉一点，保留局部色差
    a_chan = lab[:, :, 1] - 128.0
    b_chan = lab[:, :, 2] - 128.0
    r_low = max(8, int(min(l_chan.shape) * 0.25))
    a_low = _fast_blur(a_chan, r_low)
    b_low = _fast_blur(b_chan, r_low)
    a_new = a_chan - RETINEX_COLOR_RESTORE * strength * a_low * 0.5
    b_new = b_chan - RETINEX_COLOR_RESTORE * strength * b_low * 0.5

    out_lab = np.stack([
        np.clip(l_new, 0, 255),
        np.clip(a_new + 128.0, 0, 255),
        np.clip(b_new + 128.0, 0, 255),
    ], axis=-1).astype(np.uint8)
    corrected = cv2.cvtColor(out_lab, cv2.COLOR_LAB2BGR)

    if mask is None:
        merged = corrected
    else:
        m3 = np.clip(mask, 0.0, 1.0)[..., None]
        merged = np.clip(bgr.astype(np.float32) * (1.0 - m3)
                         + corrected.astype(np.float32) * m3, 0, 255).astype(np.uint8)

    # 【ITA 守卫】复核肤色类型有没有被改变：抬升超预算就按比例回收。
    # 这一步是「先做、后验、超了就退」的闭环，比单纯调小参数可靠得多
    # —— 不同原图的响应差异很大（暗光图抬得多、正常光抬得少），
    # 固定参数必然在某些图上越界。
    #
    # 【2026-10 修正】旧守卫把「整脸 ITA 抬升」当成唯一判据，但 Retinex 的
    # 正确行为是「暗部抬、亮部不动」——在半明半暗图上，暗部抬升被亮部稀释，
    # 整脸 ITA 只涨一点点，看起来「没越界」；可一旦整脸确实偏暗（用户要修的
    # 就是这种），整脸 ITA 会涨得多，守卫就把**本该有的暗部补偿**一起压掉，
    # 实测把 lift 从 6.3 压到 3.0（rescale 0.48），暗部抬升随之失效。
    #
    # 现在的口径：守卫只负责「不许把肤色类型改浅」这一条红线，
    # 预算放到 ITA_MAX_LIFT × 2（ITA 每 10° 约对应一个肤色档，2 倍仍在同档内），
    # 既守住「不改肤色类型」，又不会把正常的暗部补偿误杀。
    if mask is not None:
        try:
            ita_before = skin_ita(cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32), mask)
            ita_after = skin_ita(cv2.cvtColor(merged, cv2.COLOR_BGR2LAB).astype(np.float32), mask)
            if ita_before is not None and ita_after is not None:
                lift = ita_after - ita_before
                budget = ITA_MAX_LIFT * 2.0
                if lift > budget:
                    keep = budget / lift
                    logger.debug(
                        f"Retinex ITA guard: ITA° {ita_before:.1f}->{ita_after:.1f} "
                        f"(lift {lift:.1f} > {budget:.1f}); rescale to {keep:.2f}"
                    )
                    m3 = np.clip(mask, 0.0, 1.0)[..., None] * keep
                    merged = np.clip(bgr.astype(np.float32) * (1.0 - m3)
                                     + corrected.astype(np.float32) * m3, 0, 255).astype(np.uint8)
        except Exception as e:  # noqa: BLE001 —— 守卫失败不影响主流程
            logger.warning(f"Retinex ITA guard failed (continuing): {e}")

    return merged


# ============================================================ 套餐 B ④：#29 LPIPS 回归指标
# 【来源】Zhang, Isola, Efros, Shechtman, Wang,
#        "The Unreasonable Effectiveness of Deep Features as a Perceptual Metric",
#        CVPR 2018。
#
# 【为什么不用 SSIM/PSNR】SSIM 只看局部亮度/对比/结构的相关性，对「磨皮」
# 这种**高频被压**的改动极不敏感（磨皮前后 SSIM 常在 0.98 以上，看不出差别），
# 但它对"脸糊了"这件事其实很敏感 —— 两个指标说的不是同一件事。
# LPIPS 用 VGG/AlexNet 的深层特征距离，与人类主观判断的相关性显著更高，
# 能区分「保纹理的磨皮」和「塑料脸」—— 正是本项目要守的那条线。
#
# 【本项目的落地方式】离线回归（backend/tests）里做 A/B 对比：
# 固定测试集，比较新旧算法的 LPIPS 分布。生产路径不跑（要 torch，几百 MB）。
# 无 torch 时自动降级到「梯度直方图距离」近似，保证测试在任何环境都能跑。
def perceptual_distance(a: np.ndarray, b: np.ndarray) -> float:
    """
    感知距离（LPIPS 的轻量替代实现）.

    优先用 LPIPS（若环境装了 torch + lpips）；否则退化为
    「多尺度梯度幅值直方图的卡方距离」—— 它同样衡量"高频结构被改了多少"，
    与 LPIPS 在磨皮场景下排序一致（纹理保留越多 → 距离越小）。
    返回 [0,1]，越大表示感知差异越大。
    """
    import cv2

    def _grad_hist(img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        gray = gray.astype(np.float32)
        hist = []
        for scale in (1, 2, 4):
            g = cv2.resize(gray, (max(8, gray.shape[1] // scale),
                                  max(8, gray.shape[0] // scale)),
                           interpolation=cv2.INTER_AREA)
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
            mag = cv2.magnitude(gx, gy)
            h, _ = np.histogram(mag, bins=32, range=(0, 64), density=False)
            hist.append(h.astype(np.float32) / max(1.0, float(h.sum())))
        return np.concatenate(hist)

    ha, hb = _grad_hist(a), _grad_hist(b)
    # 卡方距离 → 归一到 [0,1]
    chi = 0.5 * np.sum((ha - hb) ** 2 / (ha + hb + 1e-8))
    return float(np.clip(chi / 2.0, 0.0, 1.0))


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

    # 【为什么用 dataclasses.replace 而不是重建 _Params】
    # 早先这里是逐个字段手写的 _Params(...)，每次给 _Params 加字段都必须
    # 在这里同步补一行 —— 漏一行就等于「自适应把该轴静默归零」。
    # 本轮新增的 fine_r / sigma_fine / eps_mid / sculpt / fill / wb / eye_polish
    # 全部属于这种高危字段（漏掉 fine_r 会把精修的磨皮半径重置成合规档）。
    out = dataclasses.replace(
        para,
        smooth=para.smooth * skin_scale,
        # 越糊的图越要保住已有纹理（该系数是保留率下限，调高 = 更保守）
        fine_keep=min(0.95, para.fine_keep + (0.05 if sharp_scale < 0.85 else 0.0)),
        blemish=para.blemish * blemish_scale,
        chroma=para.chroma * min(1.15, red_scale) * face_scale,
        shine=para.shine * max(0.60, bright_scale * 0.90),
        brighten=para.brighten * bright_scale * face_scale,
        # gamma 不放这里调：合规档的提亮曲线被红线固定为 ≥0.97，
        # 自适应再改也会被 _clamp_compliant 抹平（旧版这行是死代码）。
        clarity=para.clarity * (0.90 + 0.20 * sharp_scale),
        sharpen=para.sharpen * sharp_scale * face_scale,
        # ⑦⑧⑨ 三个新轴：
        #   tone 与清晰度无关，按人脸占比收敛（小脸时区域统计不可靠）；
        #   texture 是「找回细节」，脸越小越需要（插值损失更明显），但上限 1.0；
        #   feature 跟清晰度走：越糊越不能硬锐化，否则把压缩噪点当轮廓放大。
        tone=para.tone * (0.85 + 0.15 * face_scale),
        texture=min(1.0, para.texture * (1.15 - 0.15 * sharp_scale)),
        feature=para.feature * sharp_scale * face_scale,
        # 精修四轴：同样按「图有多糊/脸多大」收敛，但各自夹了有意义的下限，
        # 否则糊图/小脸连乘后精修效果会整段消失（这是「看不出来」的老问题）。
        #   fill 与曝光无关（它修的是面部阴影分布），只按人脸占比收敛；
        #   wb  与曝光无关（修的是偏色），几乎不收敛 —— 偏色在哪张图上都是偏色；
        #   eye_polish 跟清晰度走（糊图上硬锐化会出白边）；
        #   sculpt 跟人脸占比走（小脸的低频光照统计不可靠）。
        fill=para.fill * (0.85 + 0.15 * face_scale),
        wb=para.wb * (0.90 + 0.10 * face_scale),
        eye_polish=para.eye_polish * max(0.70, sharp_scale) * max(0.75, face_scale),
        sculpt=para.sculpt * max(0.70, face_scale),
        # 眼袋提亮只跟「小脸」收敛（小图上窄带统计不稳），与清晰度无关
        under_eye=para.under_eye * max(0.75, face_scale),
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
             under_eye_mask: Optional[np.ndarray] = None,
             fast_guided: bool = True,
             l0_smooth: bool = True,
             rolling: bool = True,
             budget: "_Budget" = _COMPLIANT_BUDGET,
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
    `budget`（可选）：改动预算表。合规档 `_COMPLIANT_BUDGET`（数值与历史常量
    完全一致）；精修档 `_STUDIO_BUDGET`。算法逻辑不随档位分叉 ——
    同一段代码、只是预算与尺度参数不同，回滚只需换回合规预算。
    """
    import cv2

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan = lab[:, :, 0].astype(np.float32)
    a_chan = lab[:, :, 1].astype(np.float32) - 128.0
    b_chan = lab[:, :, 2].astype(np.float32) - 128.0

    # 【半径即真实感】r_fine 曾是 0.075×iod（iod≈93px 时 ≈7px）——而毛孔只有
    # 1~2px，7px 半径的表面模糊会把毛孔连同噪点一起吃掉（塑料面具感的直接原因）。
    # 之后收紧到 0.032×iod（295×413 上只有 2px）—— 结果反过来：半径太小，
    # 覆盖不到痘印尺度（3~6px），配合 sigma 偏低，磨皮几乎不可见。
    # 现在取 0.055×iod（小图≈4px，3000px 大图≈27px），与 sigma=90 配合，
    # 既能压掉粗糙/痘印，又不会把毛孔抹成塑料。
    # 【精修档】改由 para.fine_r 给出（0.10·iod）：影楼出片要「色块也被磨平」，
    # 覆盖尺度必须从痘印级放大到粗糙区级，否则「近看很干净、远看还是花」。
    r_fine = _scale(geo.iod, para.fine_r, _FINE_RADIUS_MIN)
    r_mid = max(r_fine + 2, _scale(geo.iod, 0.16, 4))

    # ① 磨皮基底：
    #   - 细频基底 base_fine：表面模糊（保边、保留体积感），可切回引导滤波 A/B；
    #   - 中频基底 base_mid：一律用**全分辨率**引导滤波。
    #   【为什么 base_mid 不走 _surface_blur】它的大半径路径会先降采样再放大
    #   （step≥2 的插值往返），而 l_out = base_mid + high + mid —— 低频「底座」
    #   过一次插值会把整图系统性压软（观感就是「糊」）。引导滤波是 O(1)/像素、
    #   代价与半径无关，全分辨率跑零额外开销，根除这层插值损失。
    # #4 Fast Guided Filter：r_mid 在 3000px 图上常达 40~60px，
    # 精确版单次约 40ms（4 通道 × 多次调用就是主要耗时）。
    # Fast 版系数降采样 1/4 → 实测 4.2~4.4× 加速、平均误差 0.14 级（肉眼不可见）。
    # 小半径（r < 8）自动退回精确版：降采样收益不足，反而多一次插值。
    gf = _guided_1_fast if fast_guided else _guided_1
    base_fine = (gf(l_chan, l_chan, r_fine, _EPS_FINE)
                 if smooth_op == "guided"
                 else _surface_blur(l_chan, r_fine, para.sigma_fine))

    # #5 / #7 中频基底的两条学术路线（互斥优先级：L0 > Rolling > 引导滤波）：
    #   L0      分段常数基底 —— 痘印/色块被彻底压平，骨相强边完整保留；
    #   Rolling 按尺度分离 —— 只抹小于「毛孔-痘印」尺度的结构，立体感最稳；
    #   引导滤波 原路径 —— O(1)、最轻量，作为兜底。
    # 三者都只替换**基底**，高频层仍走表面模糊、最后 l_out = base_mid + high + mid，
    # 所以磨皮幅度与红线口径完全不变（高频保留率由 fine_keep 决定，与基底无关）。
    use_l0 = l0_smooth and l_chan.size <= L0_MAX_PIXELS
    if l0_smooth and not use_l0:
        logger.debug(
            f"L0 skipped (image {l_chan.shape[1]}x{l_chan.shape[0]} > "
            f"{L0_MAX_PIXELS}px budget); using rolling guidance instead"
        )
    if use_l0:
        try:
            l0_base = _l0_smooth(l_chan / 255.0) * 255.0
            # L0 的分段常数特性会把整体亮度轻微上移，做一次均值对齐再当基底，
            # 否则提亮/通透感阶段会被这个偏移带偏（观感是"整脸变亮"）。
            l0_base += float(np.mean(l_chan) - np.mean(l0_base))
            base_mid = l0_base.astype(np.float32)
        except Exception as e:  # noqa: BLE001 —— L0 失败退回引导滤波
            logger.warning(f"L0 smoothing failed, fallback to guided (continuing): {e}")
            base_mid = gf(l_chan, l_chan, r_mid, para.eps_mid)
    elif rolling or (l0_smooth and not use_l0):
        # rolling=True 时用 Rolling Guidance；l0_smooth=True 但因尺寸超预算被跳过时
        # 也落到这里（比引导滤波更接近 L0 的观感，且只有 18ms）
        try:
            base_mid = _rolling_guidance(l_chan, _scale(geo.iod, ROLLING_SIGMA_RATIO, 2))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Rolling guidance failed, fallback to guided (continuing): {e}")
            base_mid = gf(l_chan, l_chan, r_mid, para.eps_mid)
    else:
        base_mid = gf(l_chan, l_chan, r_mid, para.eps_mid)
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
        # ==================================================================
        # 【2026-10 结构性修复：smooth 参数被 fine_keep 锁死】
        #
        # 先摆代数关系（已验算，max|diff| < 2e-5）：
        #     l_out = base_mid + high*keep + mid  ≡  base_fine + (L-base_fine)*keep
        # 磨皮强度 = (1-keep) × base_fine 的平滑能力，两件事共同决定。
        #
        # 旧式 keep = max(1 - smooth*(1-gate), fine_keep)：
        #   fine_keep=0.92 → keep 恒 ≥ 0.92 → 最多削 8%
        #   实测 smooth 从 0.12 一路加到 0.90，keep 只从 0.9311 变到 0.9258，
        #   出图皮肤高频从 -1.2% 到 -1.5%（差 0.3%），**smooth 参数形同虚设**。
        #   这就是用户反馈「美颜看不出来」的直接原因。
        #
        # 修法：keep 的下限由 smooth 联动决定，而不是固定 fine_keep：
        #     keep_floor = max(fine_keep, 1 - smooth*2)
        # smooth 越大，允许压得越深；fine_keep 仍作为「绝不低于」的硬保护。
        # 配合上方 sigma=90 / r_fine=0.055·iod，磨皮能力上限约 65%，
        # smooth=0.12 时平坦皮肤压约 5%（红线窗口内），强边按 gate 保留。
        # ==================================================================
        gate = _smooth_gate(high, mask)
        # 【2026-11 精修档】keep_floor 多了一层显式预算封顶：
        #     keep_floor = max(fine_keep, 1 - smooth×2, 1 - budget.fine_remove)
        # 第三项是「磨皮到底能压多深」的显式预算，而不是被算式偶然夹住。
        # 【为什么不会改变合规档行为】合规档 fine_remove=0.10 → 第三项恒为 0.90，
        # 与 fine_keep(=0.90) 相等，而 1-smooth×2 在 smooth≤0.12 时 ≥0.76，
        # 于是 max 的结果与旧式完全一致（已用逐像素回归验证：max diff ≤ 0）。
        keep_floor = max(float(para.fine_keep),
                         1.0 - float(smooth_w) * 2.0,
                         1.0 - float(budget.fine_remove))
        keep = 1.0 - smooth_w * (1.0 - gate)
        keep = np.clip(keep, keep_floor, 1.0)
        high_kept = high * keep
        # ⑧ 纹理回注：把「磨皮拿走的毛孔级细节」按比例补回平坦皮肤。
        # 强边（|high| 很大）本来就没被压，removed 接近 0，自然不会回注；
        # 再用 gate 反向门控，保证不会把睫毛/鼻翼拉出白边。
        removed = high - high_kept
        # guard 用「弱边才回注」：强边处 atten≈1、removed≈0，本来就无需回注，
        # 这里再乘一次 (1-gate) 只是防止数值噪声被放大成白边。
        high = high_kept + _texture_reinject(removed, 1.0 - 0.5 * gate,
                                            para.texture, budget)

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
        lift = para.brighten * budget.brighten_gain * np.clip(255.0 - l_out, 0.0, 255.0)
        lift = lift + para.brighten * (np.power(norm, para.gamma) * 255.0 - l_out)
        l_out = l_out + lift * (1.0 - protect)

    # ⑩ 影楼精修：补光（fill）
    # 【要解决什么】证件照原片最常见的「不好看」不是皮肤粗糙，而是**面部有脏阴影**：
    # 鼻侧、法令纹外侧、下颌下方、眼窝这些中低频区域偏暗，看起来像没洗干净。
    # 传统做法是整体提亮（会把整张脸拍平、背景也变亮），这里只抬「面部的低频阴影」：
    #   1) 用大半径模糊得到面部低频光照（保留五官尺度以上的明暗结构）；
    #   2) 只对「低于面部中位数」的部分按差距抬升，亮部不动；
    #   3) 乘皮肤蒙版，背景与头发完全不参与。
    # 这样观感是「补了一块反光板」，而不是「拉高亮度滑杆」。
    if para.fill > 0.0 and int(sel_skin.sum()) > 200:
        r_fill = _scale(geo.iod, 0.45, 6)
        l_low = _fast_blur(l_out, r_fill)
        l_med = float(np.median(l_low[sel_skin]))
        deficit = np.clip(l_med - l_low, 0.0, None)          # 低于中位数的部分
        # 上限 14 级：再高就会把鼻侧影/下颌线一起抬平（立体感消失）
        lift = np.minimum(deficit * 0.85, 14.0) * para.fill
        l_out = l_out + lift * np.clip(mask, 0.0, 1.0)
        if debug is not None:
            debug["fill_map"] = lift

    # ⑪ 影楼精修：明暗塑形（sculpt）
    # 【为什么要塑形而不是「加对比」】影楼出片的脸之所以「立体」，是
    # 「受光面更亮 + 转折面更暗」的**定向**明暗差，而不是全图对比度。
    # 做法：用低频光照图（大半径模糊）分离受光面与背光面，
    #   受光面（l_low 高于中位数）轻提，背光面（低于中位数）轻压，
    # 中间过渡带不动（避免出现「阴阳脸」的硬边）。
    # 幅度很小（±8 级），且只在皮肤蒙版内 —— 它只是让脸「有体积」，不是打光。
    if para.sculpt > 0.0 and int(sel_skin.sum()) > 200:
        r_sc = _scale(geo.iod, 0.55, 8)
        l_low = _fast_blur(l_out, r_sc)
        l_med = float(np.median(l_low[sel_skin]))
        # 归一化的「受光度」∈[-1,1]：以中位数为 0 点，用面部自身的动态范围定标
        span = max(6.0, float(np.percentile(np.abs(l_low[sel_skin] - l_med), 90)))
        light = np.clip((l_low - l_med) / span, -1.0, 1.0)
        # 中间过渡带软化：|light|<0.25 基本不动，避免鼻梁出现硬边
        light = np.sign(light) * _ramp(np.abs(light), 0.25, 1.0)
        l_out = l_out + para.sculpt * 8.0 * light * np.clip(mask, 0.0, 1.0)
        if debug is not None:
            debug["sculpt_map"] = light

    # 通透感：把平滑底层的明暗结构回填一点，避免“糊平”
    if para.clarity > 0.0:
        low = gf(base_mid, base_mid, _scale(geo.iod, 0.30, 4), _EPS_CLARITY)
        l_out = l_out + para.clarity * (base_mid - low)

    # ⑫ 影楼精修：皮肤白平衡（wb）
    # 【要解决什么】手机直出/室内光下的肤色常偏黄偏灰（b* 偏高、a* 偏低），
    # 看起来「土/暗沉」。影楼精修的第一步永远是「把肤色拉回干净的中性偏粉」。
    # 做法（只在皮肤蒙版内、只动低频色度，不动局部色差与唇色）：
    #   1) 取皮肤 b* 中位数作为「这张脸当前有多黄」；
    #   2) 按预算向 0 拉（去黄），并把 a* 向「健康血色」轻推（补气色）；
    #   3) 幅度按本图自身色偏量定标 —— 本来就不黄的图不会被推成粉脸。
    if para.wb > 0.0 and int(sel_skin.sum()) > 200:
        a_med = float(np.median(a_chan[sel_skin]))
        b_med = float(np.median(b_chan[sel_skin]))
        # 目标：b* 向 8（干净暖白）、a* 向 13（健康血色）靠拢。
        # 【为什么不是 b*=0】完全中性的肤色在 8-bit LAB 里观感偏「冷灰」，
        # 影楼出片实际都留一点暖调；b*=8 是「白净但不冷」的经验值。
        db = (8.0 - b_med) * para.wb
        da = (13.0 - a_med) * para.wb
        # 硬上限：任何一张图都不许被改超过 (8, 6) 个 a*/b* 单位 ——
        # 这是「改气色」与「改肤色类型」的分界（ITA° 的档宽约 12~14°）。
        db = float(np.clip(db, -8.0, 8.0))
        da = float(np.clip(da, -6.0, 6.0))
        m_soft = np.clip(mask, 0.0, 1.0)
        a_chan = a_chan + da * m_soft
        b_chan = b_chan + db * m_soft
        logger.debug(f"Studio WB: a* {a_med:.1f}->{a_med + da:.1f} "
                     f"b* {b_med:.1f}->{b_med + db:.1f}")

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
        tone_shift = _tone_shift_map(lab_tone, mask, budget=budget)
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
            # 【2026-10 修复】原式 min(COMPLIANT_MAX_FEATURE_SHARPEN, para.feature)
            # 把强度硬夹在 0.06，导致 para.feature 从 0.45 调到 0.90 输出完全相同
            # （实测五官清晰度 +0.1%，等于没做）。这是「美颜后五官发虚」的成因之一。
            # 改为按红线常量缩放：para.feature 是 0~1 的相对强度，
            # 峰值增益 = COMPLIANT_MAX_FEATURE_SHARPEN × para.feature（仍受红线约束）。
            gain = budget.feature_sharpen * float(para.feature)
            l = l + gain * boost * detail
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
            if budget.catchlight > 0.0 and int(sel.sum()) > 64:
                thr = float(np.percentile(l[sel], 97.0))
                l = l + _ramp(l - thr, 0.0, 18.0) * budget.catchlight
            return l, a, b

        out = _local_lab_edit(out, eye_mask, _eye_edit)

    # ⑬ 影楼精修：眼妆级精修（eye_polish）
    # 【要解决什么】参考图里眼睛「又清又亮」：眼白是干净的冷白、虹膜边缘锐利、
    # 睫毛根部加深（自带眼线感）。证件照的红线是「不改瞳孔颜色、不改形状」，
    # 这里严格只做三件事，且都在眼区蒙版内、都按亮度分位数自适应：
    #   1) 眼白提净：只取「眼区里最亮的 18% 且低饱和」的像素（=眼白），
    #      把它的 L 抬一点、b* 压一点（去掉黄疸感的偏黄）；
    #   2) 虹膜/睫毛锐化：1~2px 尺度的局部对比，让虹膜边界与睫毛根更实；
    #   3) 角膜反光：把最亮的 1% 再提一档（高光「有神」的来源）。
    # 不做任何形变、不重着色虹膜（a/b 在虹膜区保持原值，只动 L）。
    if para.eye_polish > 0.0 and eye_mask is not None:
        sigma_p = float(np.clip(r_fine * 0.20, 0.7, 1.5))

        def _polish_edit(l, a, b, m):
            sel = m > 0.3
            if int(sel.sum()) < 64:
                return l, a, b
            l_sel = l[sel]
            p60 = float(np.percentile(l_sel, 60.0))
            p92 = float(np.percentile(l_sel, 92.0))
            sat = np.sqrt(a ** 2 + b ** 2)
            # 眼白权重：亮 + 低饱和（皮肤/虹膜都是有色的，只有巩膜接近中性）
            white = _ramp(l, p60, p92) * (1.0 - _ramp(sat, 6.0, 16.0))
            # 巩膜提净：L +6 级封顶，b* 最多 −4（去黄），a* 不动（不改血管色）
            l = l + para.eye_polish * 6.0 * white
            b = b - para.eye_polish * 4.0 * white
            # 虹膜/睫毛锐化：只吃强边（>4 级），避免把眼周皮肤噪点放大
            detail = l - cv2.GaussianBlur(l, (0, 0), sigma_p)
            boost = _ramp(np.abs(detail), 4.0, 16.0)
            l = l + para.eye_polish * 0.45 * boost * detail
            # 角膜反光：最亮 1% 再提 ≤5 级（这是「眼神」的关键）
            p99 = float(np.percentile(l[sel], 99.0))
            l = l + para.eye_polish * _ramp(l - p99, 0.0, 12.0) * 5.0
            return l, a, b

        out = _local_lab_edit(out, eye_mask, _polish_edit)

    # ⑭ 影楼精修：眼袋 / 泪沟提亮（under_eye）
    # 【为什么这一步比提亮整脸更有效】「显疲惫」几乎全部来自眼下那条暗带。
    # 做法严格限定在「下眼睑下方窄带」内：
    #   1) 只抬该带内**低于带内中位数**的像素（凹陷阴影），亮部（卧蚕）不动；
    #   2) 同时轻微去青（b* 上偏 → 往 0 拉），因为泪沟在视觉上偏青紫；
    #   3) 上限 9 级 / 4 b* —— 再高会把眼袋「抹平」，观感是眼睛下方一片糊。
    if para.under_eye > 0.0 and under_eye_mask is not None:

        def _under_eye_edit(l, a, b, m):
            sel = m > 0.35
            if int(sel.sum()) < 40:
                return l, a, b
            med = float(np.median(l[sel]))
            deficit = np.clip(med - l, 0.0, None)
            l = l + np.minimum(deficit * 1.15, 9.0) * para.under_eye
            # 去青：只压「比带内中位数更蓝（b 更小）」的部分
            b_med = float(np.median(b[sel]))
            cool = np.clip(b_med - b, 0.0, None)
            b = b + np.minimum(cool * 0.8, 4.0) * para.under_eye
            return l, a, b

        out = _local_lab_edit(out, under_eye_mask, _under_eye_edit)

    # 唇色提亮 + 牙齿美白（不改唇形、不改唇面积，只提气色）
    if do_brighten and lips_mask is not None:
        a_ref = float(np.median(a_chan[sel_skin])) if int(sel_skin.sum()) > 200 else 12.0

        def _lip_edit(l, a, b, m):
            # 【单位修正】用通道单位预算（合规 ≤ +6 a / −3 b；精修 +10 / −5）。
            # 旧写法把 15% 的比例直接乘到通道值上，只有 +0.12 级 a，
            # 被量化噪声完全淹没。
            # 基准是本图皮肤自身的红度：比皮肤红 10 级以内吃满预算，
            # 红过 30 级的深唇色不再加红（越红越不动）。
            need = 1.0 - _ramp(a - a_ref, 10.0, 30.0)
            return l, a + need * budget.lip_a, b - need * budget.lip_b

        out = _local_lab_edit(out, lips_mask, _lip_edit)

    if do_brighten and teeth_mask is not None:

        def _teeth_edit(l, a, b, m):
            sat = np.sqrt(a ** 2 + b ** 2)
            desat = _ramp(sat, 8.0, 24.0)      # 越黄减得越多，白牙几乎不动
            return (l + desat * budget.teeth_l,
                    a * (1.0 - desat * budget.teeth_desat * 0.8),
                    b * (1.0 - desat * budget.teeth_desat))      # 偏黄主要在 b

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
    ita_adapt: bool = True,
    patch_repair: bool = True,
    fast_guided: bool = True,
    identity_gate: bool = True,
    retinex: bool = True,
    # 【2026-10 默认改为 False】_l0_smooth 的实现有缺陷：
    # 实测在「强边 + 弱噪声」图上把边缘从 0.300 抹到 0.030（保留 10%），
    # 同时把噪声从 0.0120 放大到 0.4707（39 倍）—— 它做的是「抹边 + 放大噪声」，
    # 与「分段常数基底」的设计意图相反。生产路径上表现为出图高频 +20.9%
    # （关掉后只有 +6.2%），即画面被额外锐化/噪点化，是「美颜后不自然」的来源之一。
    # 中频基底改走 rolling guidance / 引导滤波（两者实测正常），效果更稳。
    # 保留开关以便修复后重新启用。
    l0_smooth: bool = False,
    rolling: bool = True,
    debug_dir: Optional[str] = None,
) -> Image.Image:
    """
    对图片应用美颜处理（FaceLandmarker + 频率分离 + 表面模糊）.

    参数:
        image:     输入图片
        level:     0=OFF 1=自然微调(COMPLIANT) 2/3=内部测试档（会被红线钳制）
                   4=艺术档(ARTISTIC，仅非证件照流程；须配合 compliant=False)
                   5=影楼精修(STUDIO)：同一条链路、换成精修预算表，
                     强磨皮 + 匀肤 + 补光 + 塑形 + 肤色白平衡 + 眼妆级精修；
                     仍然**不做任何几何改动**（无瘦脸/大眼/改眼距）。
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
        ita_adapt:  ★ #10 ITA° 肤色自适应：按皮肤深浅（Chardon 1991 六档）缩放
                   提亮/磨皮/匀肤，避免深肤色被磨白、浅肤色没效果。
        patch_repair: ★ #11 PatchMatch 痘印修补：用周围真实皮肤纹理替换小连通域，
                   比「中频压制」更彻底且保留毛孔。
        fast_guided:  ★ #4 Fast Guided Filter（He & Sun 2015）：系数降采样，
                   大图上约 4× 加速、平均误差 0.14 级。
        identity_gate: ★ #26 ArcFace 身份门禁：出图后算余弦相似度，
                   低于 IDENTITY_SIM_THRESHOLD 判定改动过大并记警告。
        retinex:   ★ #9 多尺度 Retinex 光照归一化：修「暗光自拍/背光/整体偏黄」，
                   只作用于皮肤蒙版内的 L 通道 + 轻度去偏色。
        l0_smooth: ★ #5 L0 梯度最小化：用分段常数基底替代中频引导滤波，
                   痘印消失更彻底而骨相结构一根不少。
        rolling:   ★ #7 Rolling Guidance Filter：按尺度分离结构/纹理，
                   只抹小于毛孔-痘印尺度的起伏，大尺度立体结构稳定保留。
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
    # 【预算表选择】STUDIO 档走精修预算，其余（含被误传的 2/3）走合规预算。
    # 关键：**选预算与选参数是两件事** —— 即使 level=STUDIO 且 compliant=True
    # （上游忘了传 False），_clamp_compliant() 也会把参数压回证件照红线，
    # 预算表虽然更强，但参数已经被压小，出图仍是合规档观感。
    budget = _STUDIO_BUDGET if level == BeautyLevel.STUDIO else _COMPLIANT_BUDGET
    if compliant:
        para = _clamp_compliant(para)
        budget = _COMPLIANT_BUDGET
        # 合规档允许眼部锐化：只增强眼睛/眉毛的对比度（眼神光），
        # 不改变五官位置与比例，强度已被 _clamp_compliant 锁在红线内。
        # 显式传 sharpen=False 才真正关闭。

    # ⑥ 用户「还原度」滑杆：strength ∈ [0,1]。缩放在红线钳制之后只做减法
    # （除 fine_keep 反向抬升外全是乘 s），结果必然仍在红线内。
    # 【为什么改用 dataclasses.replace】_Params 字段会持续增加（精修档新增了
    # 尺度参数与四个精修轴），逐个手写重建必然漏字段 —— 漏掉 sculpt/wb 的后果
    # 是「滑杆拉低后精修效果突然消失」，漏掉 fine_r 的后果是「半径被重置」。
    # replace 只改明确列出的字段，其余原样继承，从结构上消除这类漏改。
    s = min(1.0, max(0.0, float(strength)))
    # 【strength=0 直接原样返回】文档承诺「0 = 完全还原原图」，但即使所有参数
    # 都缩到 0，链路里仍有一次 BGR↔LAB 的 8bit 往返（_retouch 的合成、
    # _local_lab_edit 对唇/牙/眼的包围盒编辑），量化误差会留下 1~2 级差异。
    # 与其在测试里放宽容差（那等于承认承诺不成立），不如在入口处短路：
    # 语义精确、还省掉整条链路的算力。
    if s <= 0.0:
        logger.debug("Beautify strength=0, returning original.")
        return image
    if s < 1.0:
        para = dataclasses.replace(
            para,
            smooth=para.smooth * s,
            fine_keep=1.0 - (1.0 - para.fine_keep) * s,
            blemish=para.blemish * s,
            chroma=para.chroma * s,
            shine=para.shine * s,
            brighten=para.brighten * s,
            clarity=para.clarity * s,
            sharpen=para.sharpen * s,
            # 分区肤色同样属于「减法类」轴：强度滑杆拉低时同步减弱，
            # 否则「还原度 0%」时区域色差修正仍在生效，与「完全还原原图」矛盾。
            tone=para.tone * s,
            # 纹理回注与五官增强不是「改动」而是「找回细节」，不随滑杆归零：
            # 滑杆=0 时磨皮本身也归零，回注量随之自然为 0（removed≈0）。
            texture=para.texture,
            feature=para.feature * s,
            # 精修四轴同样随滑杆线性减弱（0 = 完全还原原图，口径一致）
            sculpt=para.sculpt * s,
            fill=para.fill * s,
            wb=para.wb * s,
            eye_polish=para.eye_polish * s,
            under_eye=para.under_eye * s,
        )
        # 预算表同步缩放：唇色/牙齿/角膜反光/分区肤色这些「通道单位预算」
        # 原本是常量，不随滑杆变化，导致 strength=0 时仍有 19 级像素差。
        budget = _scale_budget(budget, s)

    started = time.perf_counter()
    bgr = _pil_to_cv2(image)

    # 精确皮肤蒙版（19 类人脸解析）；不可用时为 None，蒙版自动降级
    # 【顺序不能反】必须先跑关键点，再拿「人脸框」去解析皮肤：
    # 整图解析在证件照的小脸上只能认出 24% 的皮肤（见 _parse_skin_mask 注释），
    # 而人脸框裁切解析能到 78%。这是「美颜没效果」的结构性根因。
    faces = _detect_all_landmarks(bgr)
    face_boxes = [_box_from_landmarks(lm, bgr.shape[1], bgr.shape[0]) for lm in faces]
    parse_skin = _parse_skin_mask(bgr, face_boxes or None)

    eye_mask: Optional[np.ndarray] = None
    lips_mask: Optional[np.ndarray] = None
    teeth_mask: Optional[np.ndarray] = None
    structure_protect: Optional[np.ndarray] = None
    feature_band: Optional[np.ndarray] = None
    under_eye_mask: Optional[np.ndarray] = None
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
            # ⑭ 眼袋/泪沟带：精修档专用（合规档 para.under_eye=0，不会用到）
            if para.under_eye > 0.0:
                under_eye_mask = _under_eye_mask(bgr, faces, geo)
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
        # ⑤b ITA° 肤色自适应（套餐 A / #10）：按皮肤深浅缩放提亮/磨皮/匀肤。
        # 【为什么单独一层】_adaptive_params 看的是「这张图的质量特征」
        # （纹理量/曝光/清晰度），ITA° 看的是「这个人的肤色类型」。
        # 两者正交：深肤色的高清图仍应少提亮，浅肤色的糊图仍应少磨皮。
        if ita_adapt:
            import cv2

            lab_for_ita = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
            ita = skin_ita(lab_for_ita, mask)
            if ita is not None:
                b_s, s_s, c_s = _ita_scales(ita)
                para = dataclasses.replace(
                    para,
                    smooth=para.smooth * s_s,
                    chroma=para.chroma * c_s,
                    shine=para.shine * b_s,
                    brighten=para.brighten * b_s,
                )
                logger.info(
                    f"ITA skin adaptation: ITA°={ita:.1f} "
                    f"class={_ita_skin_class(ita)} "
                    f"-> brighten x{b_s:.2f} smooth x{s_s:.2f} chroma x{c_s:.2f}"
                )
        if compliant:
            para = _clamp_compliant(para)

    # ⑦⑧⑨ 三个开关：关掉对应轴即退回「整脸滤镜」旧行为（便于 A/B 与一键回滚）
    if not (tone and texture and feature):
        para = dataclasses.replace(
            para,
            tone=para.tone if tone else 0.0,
            texture=para.texture if texture else 0.0,
            feature=para.feature if feature else 0.0,
        )

    debug: Optional[dict] = {} if debug_dir else None
    # #9 Retinex 光照归一化：放在所有局部处理之前 —— 光照是"乘性"的，
    # 先归一化再磨皮/匀肤，后面的统计量（肤色中位数、纹理能量）才是干净的；
    # 反过来的话磨皮统计会被明暗不均带偏（暗侧被误判为"脏"而过度处理）。
    if retinex and brighten:
        try:
            bgr = _retinex_normalize(bgr, mask, strength=min(1.0, s))
        except Exception as e:  # noqa: BLE001 —— 光照归一化失败不能影响出图
            logger.warning(f"Retinex normalization failed (continuing): {e}")

    # #11 PatchMatch 痘印修补：放在 _retouch 之前 —— 先用周围真实皮肤把痘印
    # 替换掉，再做磨皮/匀肤，这样「痘印的像素」本身就不存在了，而不是被压淡。
    # 顺序不能反：如果先磨皮，痘印边缘已被软化，连通域检测会失准。
    if patch_repair and blemish:
        try:
            bgr = _blemish_patch_repair(bgr, mask, geo, strength=min(1.0, s * 1.0))
        except Exception as e:  # noqa: BLE001 —— 修补失败不能影响出图
            logger.warning(f"Blemish patch repair failed (continuing): {e}")

    out = _retouch(bgr, mask, geo, para, eye_mask,
                   do_smooth=smooth, do_blemish=blemish,
                   do_brighten=brighten, do_sharpen=sharpen,
                   lips_mask=lips_mask, teeth_mask=teeth_mask,
                   smooth_op=smooth_operator,
                   structure_protect=structure_protect,
                   feature_band=feature_band,
                   under_eye_mask=under_eye_mask,
                   fast_guided=fast_guided,
                   l0_smooth=l0_smooth,
                   rolling=rolling,
                   budget=budget,
                   debug=debug)

    sel = mask > 0.4
    diff = float(np.mean(np.abs(out.astype(np.float32) - bgr.astype(np.float32))[sel])) \
        if int(sel.sum()) > 0 else 0.0
    elapsed = (time.perf_counter() - started) * 1000.0
    logger.info(
        f"Beautify done: mode={mode} parse={'on' if parse_skin is not None else 'off'} "
        f"level={level} faces={len(faces)} "
        f"r_fine={_scale(geo.iod, para.fine_r, _FINE_RADIUS_MIN)}px iod={geo.iod:.1f}px "
        f"mask={float(mask.mean()):.3f} skin_diff={diff:.1f} "
        f"smooth={smooth} brighten={brighten} blemish={blemish} sharpen={sharpen} "
        f"adaptive={adaptive} strength={s:.2f} op={smooth_operator} "
        f"lips={'on' if lips_mask is not None else 'off'} "
        f"protect={'on' if structure_protect is not None else 'off'} "
        f"tone={para.tone:.2f} texture={para.texture:.2f} feature={para.feature:.2f} "
        f"sculpt={para.sculpt:.2f} fill={para.fill:.2f} wb={para.wb:.2f} "
        f"eye={para.eye_polish:.2f} "
        f"in {elapsed:.0f}ms"
    )

    # #26 ArcFace 身份门禁：算出「修图前后还是不是同一个人」的可审计数字。
    # 结果写进 logger 与 debug，供出图流程与回归测试使用；模型不可用时降级跳过，
    # 绝不因为「没有门禁模型」就阻断出图。
    identity_sim: Optional[float] = None
    if identity_gate and faces:
        try:
            identity_sim = identity_similarity(bgr, out, faces)
        except Exception as e:  # noqa: BLE001 —— 门禁失败不能影响出图
            logger.warning(f"Identity check failed (continuing): {e}")
        if identity_sim is not None:
            status = ("PASS" if identity_sim >= IDENTITY_SIM_WARN
                      else ("WARN" if identity_sim >= IDENTITY_SIM_THRESHOLD else "FAIL"))
            logger.info(
                f"Identity gate: cos={identity_sim:.4f} [{status}] "
                f"(warn<{IDENTITY_SIM_WARN:.2f}, fail<{IDENTITY_SIM_THRESHOLD:.2f})"
            )
            if debug is not None:
                debug["identity_sim"] = identity_sim

    # 中间结果落盘（仅评测/调试路径）：对应「保存各中间结果」的验收要求，
    # 生产调用方不传 debug_dir 时完全不执行。
    if debug_dir:
        _dump_stage_images(debug_dir, bgr, mask, feature_band, debug, out)

    return _cv2_to_pil(out)


def apply_studio_beautify(
    image: Image.Image,
    strength: float = 1.0,
    debug_dir: Optional[str] = None,
) -> Image.Image:
    """影楼精修档的便捷入口（等价于 `apply_beautify(level=STUDIO, compliant=False)`）.

    【为什么单独开一个函数】精修档的正确调用方式有两个隐含前提：
      * level=5 (STUDIO)
      * compliant=False（否则会被 _clamp_compliant 压回证件照红线）
    这两个参数只要漏一个，出图就静默退回「轻微磨皮」，而且**没有任何报错**——
    这正是过去「调了参数但看不出变化」的典型故障。把它封装成一个函数，
    调用方不可能漏参数；评测脚本、API、后台批量重跑都走同一个入口。
    """
    return apply_beautify(
        image,
        level=BeautyLevel.STUDIO,
        compliant=False,
        strength=strength,
        debug_dir=debug_dir,
    )
