# ============================================================================
# beautify 分卷 09/11 · 09_retouch_support.py
# 职责: Retinex 归一化 / 感知距离 / fallback 区域 / 自适应参数 / 局部 LAB 编辑
# 来源: 原 image_engine/beautify.py L2319-L2717
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

RETINEX_SCALES = (15, 80, 250)   # 三个高斯尺度（论文经典配置）
RETINEX_MAX_LIFT = 12.0          # 暗部最大抬升（L 级），防止过曝与"灰雾感"
# 【2026-10 从 0.35 降到 0.12（去偏色）】2026-11 把最大抬升 22→12：
# Retinex 是「光照归一化」，抬升过强会把面部明暗结构（鼻影/颧骨过渡/下颌阴影）
# 一起拉平，表现为「脸变扁、发灰、失去立体感」——与用户反馈的假白/扁平一致。
# 12 级只补明显暗部，不抹平正常光影。
# 去偏色的目标是「去掉环境光的整体偏色」（低频），不是「把脸洗白」；
# 0.10 更保守地保留脸颊/唇周的血色，避免肤色发灰。
RETINEX_COLOR_RESTORE = 0.10     # a/b 去偏色强度（MSRCR 的 color restoration 系数）
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
    # 【2026-11 P0：从「常态打折」改为「异常才收敛」】
    # 旧版每个系数的基线都小于 1（tex 0.72、sharp 0.72、bright 0.62、
    # face 0.82），正常照片连乘后只剩 0.7~0.85；再叠 strength=0.7、
    # 红线钳制、纹理回注与两级锐化，净效果趋近于零 —— 这正是「测试全过、
    # 用户看不出变化」的算术根因。
    # 现在：统计落在正常带内时所有系数 = 1.0（吃满档位预算）；
    # 只有「极干净 / 极糊 / 过曝 / 小脸」才按比例收紧，下限抬到 0.90。
    # 缩放结果仍经 _clamp_compliant() 幂等钳制，绝不可能突破红线。
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
    # 皮肤已经极干净 → 少磨；正常/粗糙 → 1.0
    tex_scale = min(1.0, 0.85 + 0.25 * _lin(texture, 0.8, 2.5))

    # 红斑量（a 通道相对皮肤中位数的上偏）：红斑多时最多给 1.15，仍受红线钳制
    a_ref = float(np.median(a_chan[sel]))
    redness = float(np.mean(np.clip(a_chan[sel] - a_ref, 0.0, None)))
    red_scale = min(1.15, 1.0 + 0.15 * _lin(redness, 3.0, 8.0))

    # 只有过曝才收敛提亮（暗图不再额外加码 —— 合规上限本来就固定）
    bright_scale = 1.0 - 0.25 * _lin(mean_l, 170.0, 210.0)

    lap = cv2.Laplacian(l_chan, cv2.CV_32F)
    sharpness = float(np.mean(np.abs(lap[sel])))
    # 只有极糊才收敛磨皮/锐化（避免把噪点当纹理放大）
    sharp_scale = min(1.0, 0.85 + 0.25 * _lin(sharpness, 1.5, 5.0))

    face_ratio = float(geo.width) / max(float(mask.shape[1]), 1.0)
    # 只有小脸才收敛（区域统计与结构保护不可靠）；工作分辨率下通常 ≥0.5 → 1.0
    face_scale = min(1.0, 0.85 + 0.25 * _lin(face_ratio, 0.08, 0.25))

    skin_scale = min(1.0, max(0.90, tex_scale * sharp_scale * face_scale))
    blemish_scale = min(1.0, max(0.90, tex_scale * face_scale))

    # 所有轴统一乘 ≤1.0 的「收敛系数」：正常照片恰好 1.0（吃满档位预算），
    # 异常照片才打折。不再出现「系数基线 <1 → 正常照片也被连乘饿死」。
    out = dataclasses.replace(
        para,
        smooth=para.smooth * skin_scale,
        # 极糊的图要保住已有纹理（保留率下限抬一点 = 更保守）
        fine_keep=min(0.95, para.fine_keep + (0.05 if sharp_scale < 0.95 else 0.0)),
        blemish=para.blemish * blemish_scale,
        chroma=para.chroma * red_scale * face_scale,
        shine=para.shine * bright_scale,
        brighten=para.brighten * bright_scale * face_scale,
        # gamma 不放这里调：合规档的提亮曲线被红线固定为 ≥0.97，
        # 自适应再改也会被 _clamp_compliant 抹平（旧版这行是死代码）。
        clarity=para.clarity * sharp_scale,
        sharpen=para.sharpen * sharp_scale * face_scale,
        # ⑦⑧⑨ 三个新轴：同样只在异常输入下收敛，正常照片 = 1.0
        tone=para.tone * face_scale,
        texture=min(1.0, para.texture * min(1.0, 0.90 + 0.10 * sharp_scale)),
        feature=para.feature * sharp_scale * face_scale,
        # 【P1】spot 不随清晰度/肤色/人脸占比收敛：痘印在哪张图上都是痘印，
        # 且它看的是「相对紧邻皮肤的色度偏离」而非绝对亮度或纹理能量，
        # 让糊图/小脸打折只会让最需要修的照片修不到（历史连乘饿死的老问题）。
        spot=para.spot,
        # 精修四轴：口径一致（小脸/糊图才收敛），不再有 <1 的常数基线
        fill=para.fill * face_scale,
        wb=para.wb * face_scale,
        eye_polish=para.eye_polish * sharp_scale * face_scale,
        sculpt=para.sculpt * face_scale,
        under_eye=para.under_eye * face_scale,
    )
    logger.debug(
        f"Adaptive: mean_l={mean_l:.1f} texture={texture:.2f} sharp={sharpness:.2f} "
        f"redness={redness:.2f} face_ratio={face_ratio:.2f} -> smooth x{skin_scale:.2f} "
        f"blemish x{blemish_scale:.2f} chroma x{red_scale * face_scale:.2f} "
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


