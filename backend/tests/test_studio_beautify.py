"""影楼精修档（STUDIO=5）的离线回归测试.

覆盖:
  ① 预算隔离        : STUDIO 不被 _clamp_compliant 压回；COMPLIANT 不受 STUDIO 预算污染
  ② 尺度参数        : fine_r / sigma_fine / eps_mid 真的被用上（不是摆设）
  ③ 新算子生效      : fill / sculpt / wb / eye_polish / under_eye 各自可观测
  ④ 纹理保真        : 精修档高频保留率 > 0.80（不能糊成塑料）
  ⑤ 身份保真        : 无几何改动 —— 蒙版/关键点坐标系完全一致（结构性断言）
  ⑥ 强度滑杆        : strength=0 时输出 == 原图（逐像素）
  ⑦ 便捷入口        : apply_studio_beautify 与 apply_beautify(level=5, compliant=False) 等价

运行:
    python backend/tests/test_studio_beautify.py
"""
import dataclasses
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.image_engine import beautify as BT  # noqa: E402


class _LM:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)


def _fake_face(w=300, h=400):
    """构造一副几何合理的假关键点（478 点），覆盖精修档用到的全部索引."""
    lm = [_LM(0.5, 0.5) for _ in range(478)]
    cx, cy = 0.5, 0.48

    def put(i, x, y):
        if i < len(lm):
            lm[i] = _LM(x, y)

    # 脸型椭圆
    n = len(BT._FACE_OVAL)
    for k, i in enumerate(BT._FACE_OVAL):
        a = 2.0 * np.pi * k / n
        put(i, cx + 0.20 * np.cos(a), cy + 0.26 * np.sin(a))
    # 眼睛（含虹膜中心 468/473）
    for idxs, ex in ((BT._LEFT_EYE, cx - 0.085), (BT._RIGHT_EYE, cx + 0.085)):
        m = len(idxs)
        for k, i in enumerate(idxs):
            a = 2.0 * np.pi * k / m
            put(i, ex + 0.045 * np.cos(a), cy - 0.045 + 0.018 * np.sin(a))
    put(468, cx - 0.085, cy - 0.045)
    put(473, cx + 0.085, cy - 0.045)
    # 眉毛
    for idxs, ex in ((BT._LEFT_EYEBROW, cx - 0.085), (BT._RIGHT_EYEBROW, cx + 0.085)):
        m = len(idxs)
        for k, i in enumerate(idxs):
            put(i, ex - 0.05 + 0.10 * k / max(1, m - 1), cy - 0.105)
    # 嘴唇（外 + 内）
    for idxs, ry in ((BT._LIPS, 0.030), (BT._INNER_LIPS, 0.014)):
        m = len(idxs)
        for k, i in enumerate(idxs):
            a = 2.0 * np.pi * k / m
            put(i, cx + 0.075 * np.cos(a), cy + 0.145 + ry * np.sin(a))
    # 下眼睑（眼袋带用）
    for idxs, ex in ((BT._LEFT_LOWER_LID, cx - 0.085), (BT._RIGHT_LOWER_LID, cx + 0.085)):
        m = len(idxs)
        for k, i in enumerate(idxs):
            put(i, ex - 0.04 + 0.08 * k / max(1, m - 1), cy - 0.028)
    # 鼻梁/鼻翼/下颌
    for k, i in enumerate(BT._NOSE_RIDGE):
        put(i, cx, cy - 0.03 + 0.006 * k)
    put(98, cx - 0.035, cy + 0.05)
    put(327, cx + 0.035, cy + 0.05)
    put(BT._CHIN, cx, cy + 0.26)
    put(BT._JAW_LEFT, cx - 0.20, cy + 0.12)
    put(BT._JAW_RIGHT, cx + 0.20, cy + 0.12)
    for k, i in enumerate(BT._JAW_CONTOUR):
        a = np.pi * (0.15 + 0.70 * k / max(1, len(BT._JAW_CONTOUR) - 1))
        put(i, cx - 0.20 * np.cos(a), cy + 0.26 * np.sin(a))
    return lm


def _synthetic_image(w=300, h=400, lm=None):
    """合成一张「有皮肤质感 + 有五官」的测试图.

    【为什么必须画眼睛】眼妆级精修（eye_polish）只作用于眼区里的
    「亮且低饱和」像素（眼白）与强边（虹膜/睫毛）。如果合成图是一片纯肤色，
    眼区里根本没有眼白和虹膜，eye_polish 自然无从下手 —— 那样的断言测不到
    算子，只会测到「图里没有眼睛」。这里按关键点位置画出巩膜 + 虹膜 + 瞳孔，
    让眼区具备真实结构。
    """
    rng = np.random.default_rng(11)
    # RGB 肤色（偏黄一点，给 wb 留出可修正量）
    arr = np.zeros((h, w, 3), dtype=np.float32)
    arr[:, :, 0] = 206
    arr[:, :, 1] = 178
    arr[:, :, 2] = 152
    # 毛孔级噪声（高频）
    arr += rng.normal(0.0, 3.0, (h, w, 1)).astype(np.float32)
    # 低频明暗（模拟面部立体）
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    shade = 10.0 * np.sin(xx / w * np.pi) - 6.0 * np.cos(yy / h * np.pi)
    arr += shade[..., None]
    # 几颗痘印（中频偏红）
    for (cyy, cxx) in ((0.52, 0.40), (0.58, 0.60), (0.44, 0.62)):
        y0, x0 = int(cyy * h), int(cxx * w)
        cv2.circle(arr, (x0, y0), 4, (198, 150, 148), -1, lineType=cv2.LINE_AA)
    # 眼睛：巩膜（偏白偏冷）+ 虹膜 + 瞳孔 + 下眼睑阴影（眼袋）
    if lm is not None:
        for idxs in (BT._LEFT_EYE, BT._RIGHT_EYE):
            pts = np.array([[lm[i].x * w, lm[i].y * h] for i in idxs], np.float32)
            cx_e, cy_e = pts[:, 0].mean(), pts[:, 1].mean()
            rw = max(3, float(pts[:, 0].max() - pts[:, 0].min()) * 0.55)
            rh = max(2, float(pts[:, 1].max() - pts[:, 1].min()) * 0.60)
            cv2.ellipse(arr, (int(cx_e), int(cy_e)), (int(rw), int(rh)),
                        0, 0, 360, (238, 240, 244), -1, lineType=cv2.LINE_AA)
            cv2.circle(arr, (int(cx_e), int(cy_e)), int(rh * 0.95),
                       (96, 78, 62), -1, lineType=cv2.LINE_AA)
            cv2.circle(arr, (int(cx_e), int(cy_e)), max(1, int(rh * 0.45)),
                       (28, 24, 22), -1, lineType=cv2.LINE_AA)
        # 眼袋：下眼睑下方一条偏暗偏青的带（under_eye 的修正对象）
        for idxs in (BT._LEFT_LOWER_LID, BT._RIGHT_LOWER_LID):
            pts = np.array([[lm[i].x * w, lm[i].y * h] for i in idxs], np.float32)
            for p in pts:
                cv2.circle(arr, (int(p[0]), int(p[1] + 6)), 5,
                           (168, 146, 140), -1, lineType=cv2.LINE_AA)
    return np.clip(arr, 0, 255).astype(np.uint8)


def _run(level: int, img: np.ndarray, lm, **kw) -> np.ndarray:
    """在假关键点 + 假解析蒙版下跑一次 apply_beautify，返回 RGB ndarray."""
    pil = Image.fromarray(img)
    orig_det = BT._detect_all_landmarks
    orig_parse = BT._parse_skin_mask
    BT._detect_all_landmarks = lambda bgr: [lm]
    BT._parse_skin_mask = lambda bgr, face_boxes=None: None
    try:
        out = BT.apply_beautify(pil, level=level, **kw)
    finally:
        BT._detect_all_landmarks = orig_det
        BT._parse_skin_mask = orig_parse
    return np.array(out).astype(np.float32)


def _hf(img: np.ndarray, mask: np.ndarray) -> float:
    gray = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    return float(np.sqrt(np.mean(cv2.Laplacian(gray, cv2.CV_32F)[mask > 0.5] ** 2)))


def _skin_mask(lm, img: np.ndarray) -> np.ndarray:
    """用**真实图像**算皮肤蒙版.

    【不能传全黑图】解析不可用时蒙版走「肤色概率」降级路径（YCbCr 软阈值），
    纯黑图上肤色概率恒为 0，蒙版会直接返回 None —— 那样后续断言测的就不是
    「精修算子」，而是「假图不像皮肤」。
    """
    bgr = np.ascontiguousarray(img[:, :, ::-1])
    m, _ = BT._landmark_region_mask(bgr, [lm], None)
    return m


def test_budget_isolation():
    """STUDIO 参数不被红线压回；COMPLIANT 参数不被精修预算污染."""
    studio = BT._PARAMS[BT.BeautyLevel.STUDIO]
    clamped = BT._clamp_compliant(studio)
    assert clamped.smooth < studio.smooth, "合规钳制没生效"
    assert clamped.sculpt == 0.0 and clamped.fill == 0.0, "合规档必须关掉精修轴"
    assert clamped.fine_r == BT._FINE_RADIUS_RATIO, "合规档磨皮尺度必须回到历史常量"
    assert studio.sculpt > 0 and studio.under_eye > 0, "精修档必须有塑形/眼袋轴"
    assert studio.fine_r > BT._FINE_RADIUS_RATIO, "精修档磨皮尺度必须更大"
    print("  [OK] budget isolation: 精修参数被钳回、合规参数不被污染")


def test_scale_params_are_used():
    """fine_r / sigma_fine / eps_mid 必须真的被传给算子（不是写着好看）.

    【为什么用「记录调用参数」而不是「比输出像素差」】合成图的噪声幅度只有
    3 级，sigma 90 与 150 都远大于它，表面模糊的输出几乎相同（实测 Δ=0.025 级），
    比像素差会把「参数确实生效」误判成「没生效」。这里直接抓算子入参，
    断言的是**接线正确**这件事本身，与图像内容无关，稳定性更高。
    """
    lm = _fake_face()
    img = _synthetic_image(lm=lm)
    calls = {}
    orig_surface = BT._surface_blur
    orig_guided = BT._guided_1
    orig_guided_fast = BT._guided_1_fast

    def spy_surface(arr, radius, sigma_color):
        calls.setdefault("surface", []).append((int(radius), float(sigma_color)))
        return orig_surface(arr, radius, sigma_color)

    def spy_guided(guide, src, radius, eps):
        calls.setdefault("guided", []).append((int(radius), float(eps)))
        return orig_guided(guide, src, radius, eps)

    def spy_guided_fast(guide, src, radius, eps, subsample=4):
        calls.setdefault("guided", []).append((int(radius), float(eps)))
        return orig_guided_fast(guide, src, radius, eps, subsample)

    BT._surface_blur = spy_surface
    BT._guided_1 = spy_guided
    BT._guided_1_fast = spy_guided_fast
    try:
        _run(BT.BeautyLevel.STUDIO, img, lm, compliant=False)
        studio_calls = {k: list(v) for k, v in calls.items()}
        calls.clear()
        _run(BT.BeautyLevel.COMPLIANT, img, lm)
        compliant_calls = {k: list(v) for k, v in calls.items()}
    finally:
        BT._surface_blur = orig_surface
        BT._guided_1 = orig_guided
        BT._guided_1_fast = orig_guided_fast

    studio = BT._PARAMS[BT.BeautyLevel.STUDIO]
    # 精修档必须用 para.sigma_fine（150），而不是模块常量（90）
    sigmas = [s for (_r, s) in studio_calls.get("surface", [])]
    assert sigmas and all(abs(s - studio.sigma_fine) < 1e-6 for s in sigmas), \
        f"精修档没用 para.sigma_fine：{sigmas} vs {studio.sigma_fine}"
    # 合规档必须仍用模块常量（90），不能被精修预算污染
    csig = [s for (_r, s) in compliant_calls.get("surface", [])]
    assert csig and all(abs(s - BT._SURFACE_SIGMA_FINE) < 1e-6 for s in csig), \
        f"合规档 sigma 被污染：{csig} vs {BT._SURFACE_SIGMA_FINE}"
    # 精修档磨皮半径必须严格大于合规档（同 iod 下）
    s_r = max((r for (r, _s) in studio_calls.get("surface", [])), default=0)
    c_r = max((r for (r, _s) in compliant_calls.get("surface", [])), default=0)
    assert s_r > c_r, f"精修磨皮半径 {s_r}px 未大于合规 {c_r}px"
    print(f"  [OK] scale params: 精修 r={s_r}px/sigma={studio.sigma_fine}，"
          f"合规 r={c_r}px/sigma={BT._SURFACE_SIGMA_FINE}")


def test_new_operators_effective():
    """fill / sculpt / wb / eye_polish / under_eye 每个都必须可观测.

    【口径：在算子自己的蒙版里量】每个算子只作用于自己那一小块区域
    （眼袋带只占画面 0.4%，眼区约 2%），用「整图平均差」衡量等于把信号
    除以 200 —— 明明生效了也会测成 0.005 级。正确做法是取该算子的作用
    蒙版，在蒙版内比较均值差，这才是「这一步有没有做事」的直接度量。
    """
    lm = _fake_face()
    img = _synthetic_image(lm=lm)
    bgr = np.ascontiguousarray(img[:, :, ::-1])
    geo = BT._geometry_from_landmarks(lm, img.shape[1], img.shape[0])
    masks = {
        "skin": _skin_mask(lm, img),
        "eye": BT._eye_region_mask(bgr, [lm], geo),
        "under_eye": BT._under_eye_mask(bgr, [lm], geo),
    }
    base = _run(BT.BeautyLevel.STUDIO, img, lm, compliant=False)
    orig = BT._PARAMS[BT.BeautyLevel.STUDIO]
    for axis, region in (("fill", "skin"), ("sculpt", "skin"), ("wb", "skin"),
                         ("eye_polish", "eye"), ("under_eye", "under_eye")):
        BT._PARAMS[BT.BeautyLevel.STUDIO] = dataclasses.replace(orig, **{axis: 0.0})
        try:
            off = _run(BT.BeautyLevel.STUDIO, img, lm, compliant=False)
        finally:
            BT._PARAMS[BT.BeautyLevel.STUDIO] = orig
        m = masks[region]
        assert m is not None, f"{axis} 的参照蒙版（{region}）为空，测试无法进行"
        sel = m > 0.35
        d = float(np.mean(np.abs(base - off)[sel]))
        assert d > 0.05, (f"{axis} 关掉后在 {region} 区几乎不变（Δ={d:.4f}），"
                          f"等于没生效")
        print(f"    {axis:11s} 在 {region:9s} 区关掉后 Δ={d:.3f} 级"
              f"（{int(sel.sum())} px）")


def test_texture_retention():
    """精修档必须保留足够高频（防塑料脸）."""
    lm = _fake_face()
    img = _synthetic_image(lm=lm)
    h, w = img.shape[:2]
    mask = _skin_mask(lm, img)
    out = _run(BT.BeautyLevel.STUDIO, img, lm, compliant=False)
    keep = _hf(out, mask) / max(_hf(img, mask), 1e-6)
    assert keep > 0.80, f"精修档纹理保留率过低（{keep:.3f}），观感会是塑料脸"
    print(f"  [OK] texture retention: 高频保留 {keep:.3f}（>0.80）")


def test_no_geometry_change():
    """结构性断言：整条链路里不存在 warp/形变，几何量完全不变."""
    import inspect

    src = inspect.getsource(BT._retouch)
    for banned in ("warpAffine", "remap", "resize(", "getAffineTransform",
                   "thinPlate", "griddata"):
        assert banned not in src, f"_retouch 里出现几何变换 {banned} —— 违反红线"
    # 精修档的输出尺寸必须与原图一致
    lm = _fake_face()
    img = _synthetic_image(lm=lm)
    out = _run(BT.BeautyLevel.STUDIO, img, lm, compliant=False)
    assert out.shape == img.shape, "输出尺寸变了"
    print("  [OK] no geometry change: 无 warp/remap/resize，尺寸一致")


def test_strength_zero_is_identity():
    """strength=0 必须逐像素等于原图."""
    lm = _fake_face()
    img = _synthetic_image(lm=lm)
    out = _run(BT.BeautyLevel.STUDIO, img, lm, compliant=False, strength=0.0)
    d = float(np.max(np.abs(out - img.astype(np.float32))))
    assert d <= 1.0, f"strength=0 仍有改动（max diff={d:.1f}）"
    print(f"  [OK] strength=0: max diff {d:.1f} 级（≤1）")


def test_convenience_entry_equivalent():
    """apply_studio_beautify 必须等价于 apply_beautify(level=5, compliant=False)."""
    lm = _fake_face()
    img = _synthetic_image(lm=lm)
    pil = Image.fromarray(img)
    orig_det = BT._detect_all_landmarks
    orig_parse = BT._parse_skin_mask
    BT._detect_all_landmarks = lambda bgr: [lm]
    BT._parse_skin_mask = lambda bgr, face_boxes=None: None
    try:
        a = np.array(BT.apply_studio_beautify(pil)).astype(np.float32)
        b = np.array(BT.apply_beautify(pil, level=BT.BeautyLevel.STUDIO,
                                      compliant=False)).astype(np.float32)
    finally:
        BT._detect_all_landmarks = orig_det
        BT._parse_skin_mask = orig_parse
    assert float(np.max(np.abs(a - b))) == 0.0, "两个入口输出不一致"
    print("  [OK] convenience entry: apply_studio_beautify 与 level=5 完全一致")


if __name__ == "__main__":
    print("=== 影楼精修档（STUDIO）回归 ===")
    for fn in (test_budget_isolation, test_scale_params_are_used,
               test_new_operators_effective, test_texture_retention,
               test_no_geometry_change, test_strength_zero_is_identity,
               test_convenience_entry_equivalent):
        print(f"[{fn.__name__}]")
        fn()
    print("全部通过 [OK]")
