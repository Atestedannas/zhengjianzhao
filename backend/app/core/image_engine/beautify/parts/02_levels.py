# ============================================================================
# beautify 分卷 02/11 · 02_levels.py
# 职责: _Budget / 档位参数表 _PARAMS / _clamp_compliant 与强度缩放
# 来源: 原 image_engine/beautify.py L251-L492
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

                                # 【2026-10 从 0.15 提到 0.50；2026-11 根据实测反馈回调到 0.44】
                                # 0.15 时实测皮肤区 ΔL 仅 +0.14 级（8-bit 图上不可见），
                                # 0.56 在部分照片上会造成「提亮过度、阴影被抹平、脸显扁平/灰白」。
                                # 0.44 配 brighten=0.08 → 暗部峰值约 +2.3 级：保留气色与通透感，
                                # 同时更好保住鼻梁/颧骨/下颌的明暗过渡（立体感）。
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
    # smooth=0.12 / fine_keep=0.90：约 10% 高频压制上限（毛孔保留 ≥90%），
    #   r_fine=0.055×iod 覆盖痘印尺度；smooth/fine_keep 受红线钳制，实际生效
    #   由 keep 下限联动决定（见 _retouch）。
    # chroma=0.15：肤色均匀化吃满红线（0.15），蒙版加权平滑去红斑/色块；
    # brighten=0.08 × 增益 0.56：暗部峰值约 +2.9 级，提神但不抬平阴影；
    # sharpen=0.06：眼神光级微锐化（红线内），避免睫毛白边。
    # tone/texture/feature 是「分区域处理系统」的三个新轴：
    #   tone=0.68 只做「同一张脸内更一致」（区域色差拉向中位数，预算见
    #              COMPLIANT_MAX_TONE_SHIFT_*，绝不允许整体改肤色）；
    #   texture=0.52 把磨皮拿走的毛孔细节回注（磨皮可感知 + 毛孔仍在）；
    #   feature=0.92 在五官轮廓带做微对比，不改形状不改位置。
    # 【2026-10 参数重标定 + 2026-11 观感优化】
    # 依据：线上实测皮肤 ΔL 仅 +0.14 级、高频仅 -1.6%，用户反馈「美颜看不出来」。
    # 本轮在不突破红线（smooth/blemish/chroma/brighten/sharpen 均已在上限或由红线钳制）的前提下，
    # 提升分区肤色修正、纹理回注、五官轮廓与提亮增益，让「自然微调」更可感知。
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
    # 2026-11 根据“塑料感/过度提亮/立体感丢失”反馈二次微调：
    #   blemish 0.18→0.15（中频压制收敛，减少“蜡像/糊边”），smooth 保持 0.12（受 10% 上限约束）；
    #   texture 0.52→0.62（更多毛孔/纹理回注，保肌理）；tone/feature 保持，提亮由增益与 Retinex 收敛。
    # 【2026-11 P0 预算重分配】把有限预算从「看不见的高频亮度」挪到
    # 「人眼敏感的低频色度与光影」，两条红线一个都不动：
    #   * 实测工作分辨率下磨皮拿走的 |high| 平均仅 0.15 级（8-bit 不可见），
    #     再怎么调 smooth 都是空转；而分区肤色 ±(7,5,5)、色度匀肤 0.15、
    #     去瑕疵 0.18 这些低频预算原本只吃到 68% / 83%。
    #   * blemish 0.15→0.18（吃满 COMPLIANT_MAX_BLEMISH）、tone 0.68→1.0
    #     （吃满 ±(7,5,5)），配合下面的色度异常门控与中频 gate 放宽。
    # 【2026-11 P1】新增 spot 局部色斑抑制：上面两项仍只在「亮度/中尺度色块」
    # 上做文章，实测痘印/红斑是 a*/b* 的小尺度局部异常，现有链路把强色斑
    # 像素占比反而从 9.95% 抬到 10.16%（提亮放大了色度差）。spot 用更小
    # 半径的邻域参考专打这一层，是「看得见的干净」的主要来源。
    BeautyLevel.COMPLIANT: _Params(0.12, 0.90, 0.18, 0.15, 0.08, 0.08, 0.99, 0.00, 0.06,
                                   1.00, 0.62, 0.92, spot=0.85),
    # 内部对比测试档（会被 _clamp_compliant 压回上限，仅用于美学回归测试）
    BeautyLevel.MEDIUM:    _Params(0.78, 0.44, 0.68, 0.62, 0.55, 0.46, 0.85, 0.00, 0.22,
                                   0.85, 0.25, 0.70, spot=1.00),
    BeautyLevel.HIGH:      _Params(0.92, 0.30, 0.85, 0.78, 0.72, 0.60, 0.78, 0.00, 0.30,
                                   0.95, 0.15, 0.85, spot=1.00),
    # 艺术档（仅非证件照流程，apply_beautify 需显式 compliant=False 才真正生效；
    # 误传 compliant=True 时会被 _clamp_compliant 压回红线，等价自然微调）：
    # 不改骨相/五官几何，只在肤色、光影、通透与锐度上向「PS 中等修图」靠拢，
    # 纹理保留 ≥80% 仍由 fine_keep 下限保证。各轴必须**严格强于** COMPLIANT。
    BeautyLevel.ARTISTIC:  _Params(0.32, 0.72, 0.32, 0.28, 0.16, 0.14, 0.96, 0.00, 0.10,
                                   0.70, 0.35, 1.00, spot=1.00),
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
        tone=0.92, texture=0.55, feature=1.00, spot=1.00,
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
    lip_a=11.0,
    lip_b=5.5,
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
        # 【P1】spot 只压「超出邻域」的 a*/b* 偏离，单像素最多压约 3 个
        # a*/b* 单位（与唇色预算同量级），且像素绝对色度、整体肤色类型、
        # 五官几何都不动 —— 天然有界，不需要新增红线常量，但仍在此统一
        # 钳到 1.0，保证 MEDIUM/HIGH 等内部档位不会越界。
        spot=min(float(para.spot), 1.0),
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
