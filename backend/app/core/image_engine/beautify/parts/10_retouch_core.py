# ============================================================================
# beautify 分卷 10/11 · 10_retouch_core.py
# 职责: _retouch 主算子（高频/中频/色度/气色/锐化）/ 分阶段调试图
# 来源: 原 image_engine/beautify.py L2718-L3266
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

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
        # 【2026-11 P0】旧阈值 (4.0, 26.0) 在工作分辨率上把绝大多数真实痘印
        # （|mid| 约 2~10 级）判成「结构」而全额保留；放宽到 (1.5, 16.0)
        # 后中频色斑才真正进入压制区。强度仍由 blemish_w ≤0.18 红线约束。
        gate = _ramp(np.abs(mid), 1.5, 16.0)
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
        tone_shift = _tone_shift_map(lab_tone, mask, grid=TONE_GRID_ACTIVE, budget=budget)
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
        # 0.22×iod 只匀邻近小色块。2026-11 微调到 0.27×iod：对额头偏黄/两颊
        # 偏红这类「中等尺度」色块更有效，同时仍远小于 0.35，保留血色与自然色差。
        r_chroma = _scale(geo.iod, 0.27, 4)
        m_soft = np.clip(_fast_blur(mask, r_chroma), 1e-2, 1.0)
        a_ref = np.clip(_fast_blur(a_chan * mask, r_chroma) / m_soft, -128.0, 127.0)
        b_ref = np.clip(_fast_blur(b_chan * mask, r_chroma) / m_soft, -128.0, 127.0)
        # 【2026-11 P0 预算重分配】旧式「无门控全局混合」把每个像素的 a/b
        # 都拉向局部均值 15%：两颊自然血色被一起抹平（苍白面具感），而真正
        # 该修的红斑/黄斑只被压掉同样 0.15 倍，净效果既假又看不出来。
        # 现在用色偏幅度门控：|dev| ≤0.8 属自然起伏全额保留，≥6 属局部色斑
        # 才按 chroma_w(≤0.15 红线) 抑制 —— 预算集中在「看得见的脏色块」上。
        a_dev = a_chan - a_ref
        b_dev = b_chan - b_ref
        a_gate = _ramp(np.abs(a_dev), CHROMA_GATE_LO, CHROMA_GATE_HI)
        b_gate = _ramp(np.abs(b_dev), CHROMA_GATE_LO, CHROMA_GATE_HI)
        a_chan = a_chan - chroma_w * a_dev * a_gate
        b_chan = b_chan - chroma_w * b_dev * b_gate

    # ⑮ 局部色斑抑制（P1）：痘印 / 红斑 / 晒斑是**色度**现象，不是亮度现象。
    # 【实测依据】test_photo.jpg 皮肤区 15 万像素：
    #   L 高频 |high| p90=4.0，压 10% 仅 0.4 级 → 8bit 量化直接吞掉；
    #   L 中频 |mid| p90=0.71，blemish 轴再怎么调都是空转；
    #   a* 局部偏离 p90=2.29、b* 局部偏离 p90=2.61，>3 级像素约 5%
    #   => 「看得见的瑕疵」几乎全部位于 a*/b* 的小尺度局部异常上。
    # 【为什么不能并进 chroma】chroma 的参考半径是 0.27·iod（≈48px），
    # 一块 10~20px 的痘印会把自己的参考均值一起拉偏，越匀越压不掉；
    # 这里改用 0.18·iod（≈32px，痘印尺度）求邻域参考，只压「比紧邻皮肤
    # 更红/更黄」的那部分，不动像素绝对色度 —— 脸颊健康红润、整体肤色
    # 类型（ITA°）、五官几何全部原样保留。
    # 【与 chroma 的叠加】本段在 chroma 之后执行，dev 基于**已匀过肤**的
    # a/b，因此压的是「chroma 之后仍突出的残差」，两者互补而非重复计费。
    spot_w = para.spot if do_blemish else 0.0
    if spot_w > 0.0:
        r_spot = _scale(geo.iod, SPOT_RADIUS_RATIO, 3)
        m_spot = np.clip(_fast_blur(mask, r_spot), 1e-2, 1.0)
        a_loc = np.clip(_fast_blur(a_chan * mask, r_spot) / m_spot, -128.0, 127.0)
        b_loc = np.clip(_fast_blur(b_chan * mask, r_spot) / m_spot, -128.0, 127.0)
        a_sdev = a_chan - a_loc
        b_sdev = b_chan - b_loc
        # 软阈值：≤0.8 级属正常肤色起伏，全额保留；≥6 级（明显红斑）压满。
        a_sgate = _ramp(np.abs(a_sdev), SPOT_GATE_LO, SPOT_GATE_HI)
        b_sgate = _ramp(np.abs(b_sdev), SPOT_GATE_LO, SPOT_GATE_HI)
        # 幅度预算：spot=0.85 × SPOT_GAIN=1.0，且单像素改变量硬夹在
        # ±SPOT_MAX_DELTA(4) 级 —— 与分区肤色/唇色预算同量级，不会把皮肤压灰。
        # 偏离先 clip 到 ±8 杜绝极端噪声点被放大成一圈白边。
        spot_amt = float(spot_w) * SPOT_GAIN
        da = np.clip(spot_amt * a_sgate * np.clip(a_sdev, -8.0, 8.0),
                     -SPOT_MAX_DELTA, SPOT_MAX_DELTA)
        db = np.clip(spot_amt * b_sgate * np.clip(b_sdev, -8.0, 8.0),
                     -SPOT_MAX_DELTA, SPOT_MAX_DELTA)
        a_chan = a_chan - da
        b_chan = b_chan - db

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
                thr = float(np.percentile(l[sel], EYE_CATCHLIGHT_PCT))
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
            need = 1.0 - _ramp(a - a_ref, LIP_NEED_LO, LIP_NEED_HI)
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

