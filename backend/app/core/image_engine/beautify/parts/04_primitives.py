# ============================================================================
# beautify 分卷 04/11 · 04_primitives.py
# 职责: 基础数学与图像原语：_ramp / blur / guided filter / 尺度换算 / 色彩空间
# 来源: 原 image_engine/beautify.py L861-L1025
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

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


