# ============================================================================
# beautify 分卷 11/11 · 11_api.py
# 职责: 对外入口 apply_beautify / apply_studio_beautify 与报告
# 来源: 原 image_engine/beautify.py L3267-L3633
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

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
    spot: bool = True,
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
    report: Optional[dict] = None,
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
        spot:      ★ ⑮ 局部色斑抑制（痘印/红斑的 a*/b* 小尺度异常，P1 新增）。
                   关掉即回到 P0 口径，便于 A/B 归因与一键回滚。
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
        report:   ★ 【P0 可观测性】传入 dict 时**就地写入**生效报告：
                   applied / reason / mode / parse / faces / iod_px /
                   mask_coverage / skin_diff / identity_sim / work_size。
                   过去「0 张脸 / 模型缺失 / 蒙版过小」都只打一条日志，
                   用户拿到的是「没美颜的图」却毫不知情；调用方现在可据此
                   显式提示或触发门禁。不传时行为与旧版完全一致（返回 Image）。

    返回:
        PIL.Image 美颜后的图片；未检测到人脸时降级处理，
        完全无法处理时原样返回.
    """
    def _emit(**fields):
        if report is not None:
            report.update(fields)

    if level == BeautyLevel.OFF:
        logger.debug("Beautify is OFF, returning original.")
        _emit(applied=False, reason="level_off")
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
        _emit(applied=False, reason="strength_zero")
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
            # 【P1】局部色斑抑制是「减法类」改动，必须随滑杆归零，
            # 否则 strength=0 时痘印仍在被压，破坏「完全还原原图」契约。
            tone=para.tone * s,
            spot=para.spot * s,
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
        _emit(applied=False, reason="no_skin_region", mode=mode,
              parse="on" if parse_skin is not None else "off",
              landmark_faces=len(faces), iod_px=round(float(geo.iod), 2) if geo else 0.0,
              mask_coverage=0.0, work_size=[image.width, image.height])
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

    # ⑦⑧⑨⑮ 四个开关：关掉对应轴即退回「整脸滤镜」旧行为（便于 A/B 与一键回滚）
    if not (tone and texture and feature and spot):
        para = dataclasses.replace(
            para,
            tone=para.tone if tone else 0.0,
            texture=para.texture if texture else 0.0,
            feature=para.feature if feature else 0.0,
            spot=para.spot if spot else 0.0,
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
    # 【P0 可观测性】把「这一帧到底修没修、修了多少」交给调用方。
    # 前端/CI 门禁只需看 applied / mode / iod_px / mask_coverage / skin_diff，
    # 不必再从日志里捞字符串。
    _emit(
        applied=True, reason="ok", mode=mode,
        parse="on" if parse_skin is not None else "off",
        landmark_faces=len(faces),
        iod_px=round(float(geo.iod), 2),
        mask_coverage=round(float((mask > 0.4).mean()), 4),
        skin_diff=round(diff, 3),
        level=level, strength=round(s, 3), compliant=bool(compliant),
        # 【P1 可观测性】spot 是「看得见的干净」的主要来源，门禁需要能直接
        # 判断它有没有生效（例如上游误关 blemish 开关时 spot_w 会一起归零）。
        spot_strength=round(float(para.spot), 3),
        work_size=[image.width, image.height],
        identity_sim=None,
    )
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
            _emit(identity_sim=round(float(identity_sim), 5))
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
