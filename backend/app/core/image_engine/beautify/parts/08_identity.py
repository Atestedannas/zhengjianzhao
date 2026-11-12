# ============================================================================
# beautify 分卷 08/11 · 08_identity.py
# 职责: ArcFace 身份相似度 / L0 平滑 / rolling guidance
# 来源: 原 image_engine/beautify.py L2017-L2318
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

IDENTITY_SIM_THRESHOLD = 0.90     # 出图门禁阈值（低于即判定改动过大）
IDENTITY_SIM_WARN = 0.96          # 低于此值记录警告（仍出图）
_ARC_MODEL_FILE = "arcface_resnet.onnx"
_ARC_INPUT = 112                  # ArcFace 标准输入尺寸
_ARC_DISABLED = False


def _arc_model_path() -> str:
    global _MODELS_DIR
    if _MODELS_DIR is None:
        _MODELS_DIR = os.path.join(
            str(_BACKEND_DIR),
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
