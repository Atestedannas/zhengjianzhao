# ============================================================================
# beautify 分卷 06/11 · 06_tone.py
# 职责: 分区肤色 / 纹理回注 / 快速导向滤波 / ITA 肤色分档
# 来源: 原 image_engine/beautify.py L1447-L1722
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

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
