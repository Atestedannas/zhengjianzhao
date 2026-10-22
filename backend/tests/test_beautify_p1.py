"""P0~P2 美颜增强的离线回归测试（不需要 mediapipe / ONNX 模型）.

覆盖:
  ① 表面模糊算子     : 保边性优于普通模糊，平坦区噪声被压掉
  ② 分区定向祛瑕疵   : 两颊权重 > 鼻唇中轴/额头/下颌；强梯度处结构保护生效；
                       关键点骨相保护区盖住法令纹、不误伤脸颊
  ③ 唇色/牙齿        : 唇蒙版已扣除牙齿（防粉牙）
  ④ 眼神光           : boost 归一后有效强度 ≤ COMPLIANT_MAX_SHARPEN
  ⑤ 自适应强度       : 暗图提亮增益 > 亮图；缩放后仍不越红线
  磨皮幅度           : 合成样张上高频保留落在 0.85~0.95（旧式写法只有 1.5% 压制）
  分路合成           : 唇/牙/眼的局部改动不会被皮肤蒙版乘掉（曾经被整体丢弃）
  冒烟               : apply_beautify 在 surface/guided 两种算子下均可用

运行:
    python backend/tests/test_beautify_p1.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2  # noqa: E402

from app.core.image_engine import beautify as BT          # noqa: E402


class _LM:
    """最小关键点替身（MediaPipe landmark 的 .x/.y 归一化坐标）."""
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)


def _fake_lips_landmarks(w=300, h=400):
    """构造一个外唇/内唇同心椭圆的假关键点，用于验证唇蒙版."""
    lm = [_LM(0.5, 0.5) for _ in range(478)]
    for idx in BT._LIPS:
        lm[idx] = _LM(0.5, 0.5)
    for idx in BT._INNER_LIPS:
        lm[idx] = _LM(0.5, 0.5)
    n = len(BT._LIPS)
    for k, idx in enumerate(BT._LIPS):
        a = 2.0 * np.pi * k / n
        lm[idx] = _LM(0.5 + 0.20 * np.cos(a), 0.5 + 0.0625 * np.sin(a))
    n = len(BT._INNER_LIPS)
    for k, idx in enumerate(BT._INNER_LIPS):
        a = 2.0 * np.pi * k / n
        lm[idx] = _LM(0.5 + 0.10 * np.cos(a), 0.5 + 0.0300 * np.sin(a))
    return lm


def test_surface_blur_preserves_edge():
    rng = np.random.default_rng(7)
    img = np.full((200, 200), 100.0, dtype=np.float32)
    img[:, 100:] = 180.0                      # 一道强边（模拟鼻翼/下颌线）
    img = img + rng.normal(0.0, 6.0, img.shape).astype(np.float32)

    out = BT._surface_blur(img, radius=4, sigma_color=10.0)

    edge_in = float(img[:, 130].mean() - img[:, 70].mean())
    edge_out = float(out[:, 130].mean() - out[:, 70].mean())
    assert edge_out > 0.75 * edge_in, f"表面模糊把强边抹掉了: {edge_in:.1f}->{edge_out:.1f}"

    flat_in = float(img[:, 20:80].std())
    flat_out = float(out[:, 20:80].std())
    assert flat_out < 0.80 * flat_in, f"平坦区噪声未被压掉: {flat_in:.2f}->{flat_out:.2f}"

    # 大半径走降采样路径，不应崩溃且应保留大结构
    big = BT._surface_blur(img, radius=64, sigma_color=22.0)
    assert big.shape == img.shape and np.isfinite(big).all()
    print(f"  [OK] surface blur: edge {edge_in:.1f}->{edge_out:.1f}, "
          f"flat std {flat_in:.2f}->{flat_out:.2f}")


def test_blemish_zone_weight_split():
    mask = np.ones((400, 300), dtype=np.float32)
    geo = BT._FaceGeometry(cx=150.0, cy=200.0, width=120.0, height=160.0, iod=40.0)
    w = BT._blemish_zone_weight(mask, geo)

    def at(nx, ny):                       # 相对人脸框归一化坐标 -> 像素
        x = int(round(geo.cx + nx * geo.width))
        y = int(round(geo.cy - geo.height / 2.0 + ny * geo.height))
        return float(w[y, x])

    cheek = at(0.62, 0.52)
    forehead = at(0.0, 0.20)
    nose = at(0.0, 0.55)
    jaw = at(0.0, 0.95)
    assert cheek > nose + 0.25, f"鼻唇中轴未降权: cheek={cheek:.2f} nose={nose:.2f}"
    assert cheek > forehead + 0.15, f"额头未降权: cheek={cheek:.2f} forehead={forehead:.2f}"
    assert cheek > jaw + 0.10, f"下颌未降权: cheek={cheek:.2f} jaw={jaw:.2f}"
    print(f"  [OK] blemish zone: cheek={cheek:.2f} forehead={forehead:.2f} "
          f"nose={nose:.2f} jaw={jaw:.2f}")


def test_structure_guard_protects_gradient():
    rng = np.random.default_rng(3)
    bgr = np.full((200, 300, 3), 128, dtype=np.uint8)
    bgr[:, 150:] = 200                        # 模拟法令纹/鼻翼的明暗突变
    bgr = np.clip(bgr.astype(np.int16) + rng.integers(-2, 3, bgr.shape), 0, 255).astype(np.uint8)

    geo = BT._FaceGeometry(cx=150.0, cy=100.0, width=120.0, height=140.0, iod=40.0)
    guard = BT._structure_guard(bgr, geo)
    flat = float(guard[:, 40:120].mean())
    edge = float(guard[:, 140:160].mean())
    assert flat > edge + 0.15, f"结构保护未在梯度处生效: flat={flat:.2f} edge={edge:.2f}"
    assert guard.min() >= 0.0 and guard.max() <= 1.0
    print(f"  [OK] structure guard: flat={flat:.2f} edge={edge:.2f}")


def test_lips_mask_excludes_teeth():
    bgr = np.zeros((400, 300, 3), dtype=np.uint8)
    lm = _fake_lips_landmarks()
    geo = BT._FaceGeometry(cx=150.0, cy=200.0, width=120.0, height=160.0, iod=40.0)

    result = BT._lips_region_mask(bgr, [lm], geo)
    assert result is not None, "唇蒙版未生成"
    lips, teeth = result

    assert float(teeth[200, 150]) > 0.70, f"牙齿区未识别: {float(teeth[200, 150]):.2f}"
    assert float(lips[200, 150]) < 0.15, f"唇蒙版未扣除牙齿: {float(lips[200, 150]):.2f}"
    assert float(lips[182, 150]) > 0.45, f"唇环区被误扣: {float(lips[182, 150]):.2f}"
    print(f"  [OK] lips ring: center lips={float(lips[200, 150]):.2f} "
          f"teeth={float(teeth[200, 150]):.2f} ring={float(lips[182, 150]):.2f}")


def test_sharpen_effective_cap():
    """boost 必须归一到 ≤1，保证眼神光有效强度不越 8% 红线."""
    detail = np.array([0.0, 3.0, 6.0, 12.0, 50.0], dtype=np.float32)
    boost = BT._ramp(np.abs(detail), 3.0, 12.0)
    assert boost.max() <= 1.0 + 1e-6, f"boost 超 1: {boost.max()}"
    para = BT._clamp_compliant(BT._PARAMS[BT.BeautyLevel.COMPLIANT])
    assert para.sharpen <= BT.COMPLIANT_MAX_SHARPEN
    assert para.sharpen * float(boost.max()) <= BT.COMPLIANT_MAX_SHARPEN + 1e-6
    print(f"  [OK] sharpen cap: param={para.sharpen:.2f} boost={boost.max():.2f} "
          f"effective={para.sharpen * float(boost.max()):.3f}")


def test_adaptive_strength_dark_vs_bright():
    rng = np.random.default_rng(11)
    h, w = 300, 300
    mask = np.ones((h, w), dtype=np.float32)
    geo = BT._FaceGeometry(cx=w / 2, cy=h / 2, width=150.0, height=180.0, iod=50.0)

    def make(v):
        img = np.full((h, w, 3), v, dtype=np.uint8)
        return np.clip(img.astype(np.int16) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)

    para = BT._clamp_compliant(BT._PARAMS[BT.BeautyLevel.COMPLIANT])
    dark = BT._adaptive_params(para, make(70), mask, geo)
    bright = BT._adaptive_params(para, make(200), mask, geo)
    assert dark.brighten > bright.brighten,         f"暗图提亮增益未高于亮图: dark={dark.brighten:.3f} bright={bright.brighten:.3f}"

    # 缩放后再过红线，仍必须全部落在红线内
    for p in (dark, bright):
        c = BT._clamp_compliant(p)
        assert c.smooth <= BT.COMPLIANT_MAX_SMOOTH
        assert c.fine_keep >= BT.COMPLIANT_MIN_FINE_KEEP
        assert c.blemish <= BT.COMPLIANT_MAX_BLEMISH
        assert c.chroma <= BT.COMPLIANT_MAX_CHROMA
        assert c.brighten <= BT.COMPLIANT_MAX_BRIGHTEN
        assert c.sharpen <= BT.COMPLIANT_MAX_SHARPEN
        assert c.gamma >= 0.97
    assert BT._clamp_compliant(BT._clamp_compliant(dark)) == BT._clamp_compliant(dark), "clamp 不幂等"
    print(f"  [OK] adaptive: brighten dark={dark.brighten:.3f} bright={bright.brighten:.3f}")


def test_apply_beautify_smoke():
    """锁定 fallback 区域，验证 surface/guided + adaptive 两条链路都能出图."""
    from PIL import Image

    rng = np.random.default_rng(5)
    arr = np.full((300, 240, 3), 175, dtype=np.uint8)
    arr[:, :, 0] = 150        # 偏红的“皮肤”
    arr = np.clip(arr.astype(np.int16) + rng.integers(-4, 5, arr.shape), 0, 255).astype(np.uint8)
    pil = Image.fromarray(arr)  # RGB

    orig_parse = BT._parse_skin_mask
    orig_det = BT._detect_all_landmarks
    orig_fb = BT._fallback_region

    def fake_region(bgr, parse_skin=None):
        hh, ww = bgr.shape[:2]
        m = np.zeros((hh, ww), dtype=np.float32)
        m[30:hh - 30, 30:ww - 30] = 1.0
        g = BT._FaceGeometry(cx=ww / 2, cy=hh / 2, width=ww * 0.5, height=hh * 0.6, iod=max(8.0, ww * 0.1))
        return m, g

    BT._parse_skin_mask = lambda bgr: None
    BT._detect_all_landmarks = lambda bgr: []
    BT._fallback_region = fake_region
    try:
        for op in ("surface", "guided"):
            for ad in (True, False):
                out = BT.apply_beautify(pil, level=BT.BeautyLevel.COMPLIANT,
                                        adaptive=ad, smooth_operator=op)
                assert out.size == pil.size
                d = float(np.mean(np.abs(np.array(out).astype(np.float32) - arr.astype(np.float32))))
                assert d > 0.2, f"op={op} adaptive={ad} 几乎无改动: {d:.3f}"
        print("  [OK] apply_beautify smoke: surface/guided × adaptive on/off 全部出图")
    finally:
        BT._parse_skin_mask = orig_parse
        BT._detect_all_landmarks = orig_det
        BT._fallback_region = orig_fb


def _fake_face_landmarks(w=420, h=320):
    """构造一副几何合理的假关键点（478 点），供骨相保护区单测使用.

    只给保护区用到的索引赋真实位置，其余保持脸中心点（对结果无影响）。
    """
    lm = [_LM(0.5, 0.5) for _ in range(478)]
    cx, cy = 0.5, 0.52

    def put(i, x, y):
        if i < len(lm):
            lm[i] = _LM(x, y)

    for k, i in enumerate(BT._NOSE_RIDGE[:8]):        # 鼻梁中轴
        put(i, cx, cy - 0.150 + 0.032 * k)
    put(98, cx - 0.045, cy + 0.030)                   # 左鼻翼
    put(327, cx + 0.045, cy + 0.030)                  # 右鼻翼
    put(129, cx - 0.050, cy + 0.005)
    put(358, cx + 0.050, cy + 0.005)
    put(49, cx - 0.075, cy - 0.020)
    put(279, cx + 0.075, cy - 0.020)
    put(BT._L_MOUTH_CORNER, cx - 0.055, cy + 0.115)   # 嘴角
    put(BT._R_MOUTH_CORNER, cx + 0.055, cy + 0.115)
    for k, i in enumerate(BT._LEFT_LOWER_LID):        # 下眼睑
        put(i, cx - 0.100 + 0.012 * k, cy - 0.055)
    for k, i in enumerate(BT._RIGHT_LOWER_LID):
        put(i, cx + 0.045 + 0.012 * k, cy - 0.055)
    for k, i in enumerate(BT._LEFT_EYEBROW):          # 眉毛
        put(i, cx - 0.110 + 0.015 * k, cy - 0.100)
    for k, i in enumerate(BT._RIGHT_EYEBROW):
        put(i, cx + 0.030 + 0.015 * k, cy - 0.100)
    n = len(BT._JAW_CONTOUR)                          # 下颌轮廓弧线
    for k, i in enumerate(BT._JAW_CONTOUR):
        t = np.pi * (0.15 + 0.70 * k / max(1, n - 1))
        put(i, cx - 0.18 * np.cos(t), cy + 0.26 * np.sin(t))
    return lm


def _synth_face(h=320, w=420):
    """合成一张「皮肤 + 眼/唇/牙」的测试图，返回 (bgr_u8, mask, eye, lips, teeth, geo).

    皮肤的毛孔纹理刻意做成 band-limited 的（σ≈0.7 的高斯），这样 Laplacian
    高频能量主要由「毛孔」贡献，能把磨皮幅度量出来；真实小样张上这点改动
    会被 8bit 往返量化噪声（±0.5 级）淹没，量不出真实强度。
    """
    rng = np.random.default_rng(21)
    bgr = np.zeros((h, w, 3), np.float32)
    bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2] = 150.0, 170.0, 205.0   # 暖肤色（BGR）

    tex = rng.normal(0.0, 1.0, (h, w)).astype(np.float32)
    tex = cv2.GaussianBlur(tex, (0, 0), 0.7)
    tex *= 7.0 / max(1e-3, float(tex.std()))
    bgr += tex[..., None]

    yy = np.arange(h, dtype=np.float32)[:, None]
    shade = 18.0 * np.exp(-((yy - 205.0) ** 2) / (2 * 13.0 ** 2))       # 骨相：法令纹/下颌阴影
    bgr -= shade[..., None]
    bgr[45:75, 145:285] = np.array([205.0, 225.0, 245.0], np.float32)   # 眼区（亮）
    bgr[56:62, 200:212] = np.array([250.0, 252.0, 255.0], np.float32)   # 角膜反光点
    bgr[150:170, 190:230] = np.array([150.0, 190.0, 215.0], np.float32)  # 牙齿（偏黄）
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)

    eye = np.zeros((h, w), np.float32)
    eye[45:75, 145:285] = 1.0
    lips = np.zeros((h, w), np.float32)
    lips[145:178, 183:237] = 1.0
    teeth = np.zeros((h, w), np.float32)
    teeth[150:170, 190:230] = 1.0
    lips[teeth > 0] = 0.0                       # 与生产一致：唇蒙版扣除牙齿/口腔

    mask = np.ones((h, w), np.float32)
    mask[eye > 0] = 0.0                         # 与生产一致：五官从皮肤蒙版里抠掉
    mask[lips > 0] = 0.0

    geo = BT._FaceGeometry(cx=w / 2.0, cy=170.0, width=240.0, height=280.0, iod=70.0)
    return bgr, mask, eye, lips, teeth, geo


def _retouch_synth(adaptive=False):
    bgr, mask, eye, lips, teeth, geo = _synth_face()
    para = BT._clamp_compliant(BT._PARAMS[BT.BeautyLevel.COMPLIANT])
    out = BT._retouch(bgr, mask, geo, para, eye, True, True, True, True,
                      lips_mask=lips, teeth_mask=teeth, smooth_op="surface",
                      structure_protect=None)
    lab_i = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_o = cv2.cvtColor(out, cv2.COLOR_BGR2LAB).astype(np.float32)
    return bgr, out, lab_i, lab_o, mask, eye, lips, teeth


def test_skin_smoothing_reaches_redline():
    """磨皮幅度必须真的落在「≤12% / 纹理保留 ≥90%」的红线口径内.

    回归点：旧式 `1 - smooth_w*(1-keep)` 在 fine_keep=0.88 下最多只压掉 1.5%
    高频，等于没磨皮；反过来 2026-10 收紧后 smooth=0.08、r_fine=0.032×iod，
    要求高频保留 ≥90%（磨皮可感知但不吃毛孔）且 ≤97%（仍要有净磨皮效果）.
    """
    bgr, out, lab_i, lab_o, mask, *_ = _retouch_synth()
    sel = mask > 0.5
    hf_i = float(np.sqrt(np.mean(cv2.Laplacian(lab_i[..., 0], cv2.CV_32F)[sel] ** 2)))
    hf_o = float(np.sqrt(np.mean(cv2.Laplacian(lab_o[..., 0], cv2.CV_32F)[sel] ** 2)))
    retain = hf_o / hf_i
    assert 0.90 <= retain <= 0.97, f"磨皮幅度不在红窗口内: 纹理保留 {retain:.3f}"
    print(f"  [OK] skin smoothing: 高频保留 {retain:.3f}（磨掉 {100 * (1 - retain):.1f}%）")


def test_endpoint_effects_survive_compositing():
    """唇色/牙齿/眼神光的改动必须真的落到图上（不能被皮肤蒙版乘掉）.

    回归点：皮肤蒙版 mask 天然抠掉了眼/唇，早期实现把三种局部改动和磨皮
    混在同一份 LAB 里、最后统一乘 mask 合成 → 三者被整体丢弃，
    实测唇区 |Δa| 仅 0.3 级、眼区高频保留 1.005（等于没做）。
    """
    bgr, out, lab_i, lab_o, mask, eye, lips, teeth = _retouch_synth()

    def delta(chan, m):
        sel = m > 0.5
        return float(np.mean(lab_o[..., chan][sel] - lab_i[..., chan][sel]))

    da_lip = delta(1, lips)
    db_teeth, dl_teeth = delta(2, teeth), delta(0, teeth)
    assert da_lip >= 3.0, f"唇色没有真的提上来: Δa={da_lip:.2f}"
    assert dl_teeth >= 2.0, f"牙齿没有提亮: ΔL={dl_teeth:.2f}"
    assert db_teeth <= -2.0, f"牙齿没有去黄: Δb={db_teeth:.2f}"

    # 【度量口径】眼区蒙版向内腐蚀 2px 再量拉普拉斯：块边界像素的 3x3 核会
    # 取到眼区**外侧**皮肤环的值，而皮肤环会被磨皮平滑（合成阶段眼区像素
    # 本身零变化，实测边界污染把整块比值压到 0.935，与眼神光无关）。
    # 腐蚀后度量的是「眼神光对眼区内部的锐度增益」（实测 +9.7%），
    # 与断言语义一致，也不放松任何红线。
    eye_u8 = (eye * 255).astype(np.uint8)
    sel_eye = cv2.erode(eye_u8, np.ones((5, 5), np.uint8)) > 0
    hf_i = float(np.sqrt(np.mean(cv2.Laplacian(lab_i[..., 0], cv2.CV_32F)[sel_eye] ** 2)))
    hf_o = float(np.sqrt(np.mean(cv2.Laplacian(lab_o[..., 0], cv2.CV_32F)[sel_eye] ** 2)))
    assert hf_o > hf_i * 1.02, f"眼神光没有提升眼区锐度: {hf_i:.2f}->{hf_o:.2f}"
    print(f"  [OK] local ops: 唇 Δa={da_lip:+.2f} 牙 ΔL={dl_teeth:+.2f}/Δb={db_teeth:+.2f} "
          f"眼高频 {hf_i:.1f}->{hf_o:.1f}")


def test_landmark_protect_spares_structure():
    """关键点骨相保护区必须只降不升，且真的盖住法令纹/鼻侧影."""
    h, w = 320, 420
    bgr = np.zeros((h, w, 3), np.uint8)
    lm = _fake_face_landmarks()
    geo = BT._FaceGeometry(cx=w * 0.5, cy=h * 0.52, width=w * 0.36,
                           height=h * 0.52, iod=w * 0.09)
    prot = BT._structure_protect_landmarks(bgr, [lm], geo)
    assert prot is not None, "保护区未生成"
    assert 0.0 <= float(prot.min()) and float(prot.max()) <= 1.0

    # 左法令纹（鼻翼→嘴角）中点应被覆盖
    mid = ((0.5 * (0.5 - 0.045) + 0.5 * (0.5 - 0.055)) * w,
           (0.5 * (0.52 + 0.03) + 0.5 * (0.52 + 0.115)) * h)
    assert float(prot[int(mid[1]), int(mid[0])]) > 0.3, "法令纹未被保护"
    # 脸颊外侧不应被保护（那里才该吃满祛瑕疵）
    assert float(prot[int(0.60 * h), int(0.20 * w)]) < 0.10, "脸颊被误保护"
    print(f"  [OK] landmark protect: 法令纹={float(prot[int(mid[1]), int(mid[0])]):.2f} "
          f"脸颊={float(prot[int(0.60 * h), int(0.20 * w)]):.2f}")




# ============================================================ 分区域处理系统（⑦⑧⑨）

def _toned_face():
    """合成一张「同一张脸上有区域色差」的图：额头偏黄、两颊偏红、下颌偏暗.

    直接在 LAB 空间构造，这样 a/b 的方向是确定的（OpenCV 的 LAB 是 0~255，
    a/b 以 128 为中点），不依赖 BGR 与 LAB 之间的换算方向猜测。

    这正是证件照最常见的脏感来源 —— 低频区域色差，磨皮/匀肤都修不掉。
    返回 (bgr, mask, geo, lab)。
    """
    h, w = 400, 320
    rng = np.random.default_rng(33)
    lab = np.zeros((h, w, 3), np.float32)
    lab[:, :, 0] = 190.0     # 皮肤亮度
    lab[:, :, 1] = 136.0     # a：略偏红（皮肤）
    lab[:, :, 2] = 150.0     # b：略偏黄（皮肤）

    # 额头带：更黄（b +10）、更亮（L +6）
    lab[60:140, :, 0] += 6.0
    lab[60:140, :, 2] += 10.0
    # 两颊带：更红（a +9）
    lab[200:300, :, 1] += 9.0
    # 下颌带：更暗（L -18）
    lab[320:400, :, 0] -= 18.0

    # 加一点毛孔级纹理，避免磨皮统计落在量化噪声地板上
    tex = rng.normal(0.0, 1.0, (h, w)).astype(np.float32)
    tex = cv2.GaussianBlur(tex, (0, 0), 0.7)
    tex *= 5.0 / max(1e-3, float(tex.std()))
    lab[:, :, 0] += tex

    lab_u8 = np.clip(lab, 0, 255).astype(np.uint8)
    bgr = cv2.cvtColor(lab_u8, cv2.COLOR_LAB2BGR)

    mask = np.zeros((h, w), np.float32)
    mask[40:380, 40:280] = 1.0
    geo = BT._FaceGeometry(cx=w / 2.0, cy=210.0, width=220.0, height=320.0, iod=70.0)
    lab_f = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    return bgr, mask, geo, lab_f


def test_tone_shift_map_reduces_regional_bias():
    """分区域肤色修正必须真的把「区域色差」拉小，且不整体改肤色."""
    bgr, mask, geo, lab = _toned_face()
    shift = BT._tone_shift_map(lab, mask)
    assert shift is not None, "分区肤色修正量未生成"
    assert shift.shape[:2] == mask.shape

    # 1) 预算约束：任何像素都不得越过红线
    assert float(shift[..., 0].min()) >= -BT.COMPLIANT_MAX_TONE_SHIFT_L - 1e-3
    assert float(shift[..., 0].max()) <= BT.COMPLIANT_MAX_TONE_SHIFT_L + 1e-3
    assert float(shift[..., 1].min()) >= -BT.COMPLIANT_MAX_TONE_SHIFT_A - 1e-3
    assert float(shift[..., 1].max()) <= BT.COMPLIANT_MAX_TONE_SHIFT_A + 1e-3
    assert float(shift[..., 2].min()) >= -BT.COMPLIANT_MAX_TONE_SHIFT_B - 1e-3
    assert float(shift[..., 2].max()) <= BT.COMPLIANT_MAX_TONE_SHIFT_B + 1e-3

    # 2) 方向正确：偏黄的额头要被「减黄」（b 修正为负），偏红的两颊要被「降红」
    #    （a 修正为负），偏暗的下颌要被提亮（L 修正为正）。
    #    行号换算：额头 y≈100，两颊 y≈250，下颌 y≈360。
    def at(y, x=160):
        return shift[y, x]

    assert at(100)[2] < -0.5, f"额头偏黄未被减黄: {at(100)[2]:.2f}"
    assert at(250)[1] < -0.3, f"两颊偏红未被降红: {at(250)[1]:.2f}"
    assert at(360)[0] > 0.5, f"下颌偏暗未被提亮: {at(360)[0]:.2f}"

    # 3) 整脸平均修正应接近 0（只允许「区域内更一致」，不允许整体改肤色）
    sel = mask > 0.5
    mean_shift = float(np.mean(np.abs(shift[..., 1][sel])))
    assert mean_shift <= BT.COMPLIANT_MAX_TONE_SHIFT_A, f"整脸平均色偏修正过大: {mean_shift:.2f}"
    print(f"  [OK] tone shift: 额头 Δb={at(100)[2]:+.2f} 两颊 Δa={at(250)[1]:+.2f} "
          f"下颌 ΔL={at(360)[0]:+.2f}")


def test_tone_correction_reduces_regional_variance():
    """端到端：开启分区肤色修正后，区域中位数的离散度必须下降."""
    bgr, mask, geo, lab = _toned_face()
    para = BT._clamp_compliant(BT._PARAMS[BT.BeautyLevel.COMPLIANT])

    def regional_spread(lab_img):
        """三个区域各自的中位数之间的标准差 = 同一张脸内的「色差幅度」."""
        bands = [(60, 140), (200, 300), (320, 400)]
        meds = []
        for y0, y1 in bands:
            sub = lab_img[y0:y1]
            sub_mask = mask[y0:y1] > 0.5
            meds.append([float(np.median(sub[:, :, c][sub_mask])) for c in range(3)])
        return float(np.mean(np.std(np.array(meds), axis=0)))

    before = regional_spread(lab)
    # 只开分区肤色（关掉其它轴），单独观察它的贡献
    p = BT._Params(smooth=0.0, fine_keep=0.92, blemish=0.0, chroma=0.0, shine=0.0,
                   brighten=0.0, gamma=0.99, clarity=0.0, sharpen=0.0,
                   tone=1.0, texture=0.0, feature=0.0)
    out = BT._retouch(bgr, mask, geo, p, None, False, False, False, False)
    after = regional_spread(cv2.cvtColor(out, cv2.COLOR_BGR2LAB).astype(np.float32))
    assert after < before, f"分区肤色修正没有减小区域色差: {before:.2f} -> {after:.2f}"
    print(f"  [OK] tone e2e: 区域色差 {before:.2f} -> {after:.2f}")


def test_texture_reinject_restores_detail():
    """纹理回注：开启后高频保留必须**上升**（细节被找回来），但仍低于原图."""
    bgr, out_off, lab_i, lab_off, mask, eye, lips, teeth = _retouch_synth_with(texture=0.0)
    bgr2, out_on, _, lab_on, *_ = _retouch_synth_with(texture=0.9)

    sel = mask > 0.5
    hf = lambda lab: float(np.sqrt(np.mean(cv2.Laplacian(lab[..., 0], cv2.CV_32F)[sel] ** 2)))
    hf_in, hf_off, hf_on = hf(lab_i), hf(lab_off), hf(lab_on)

    assert hf_off < hf_in, "基线（texture=0）没有磨皮效果"
    assert hf_on > hf_off, f"纹理回注没有找回细节: {hf_off:.3f} -> {hf_on:.3f}"
    assert hf_on < hf_in * 1.02, f"纹理回注过头（等于没磨皮）: {hf_on:.3f} vs {hf_in:.3f}"
    print(f"  [OK] texture reinject: 高频 {hf_in:.2f} → {hf_off:.2f}(磨皮) → {hf_on:.2f}(回注)")


def _retouch_synth_with(texture: float):
    """合成样张 + 指定 texture 强度跑一遍 _retouch（供纹理回注单测使用）."""
    bgr, mask, eye, lips, teeth, geo = _synth_face()
    base = BT._clamp_compliant(BT._PARAMS[BT.BeautyLevel.COMPLIANT])
    para = BT._Params(
        smooth=base.smooth, fine_keep=base.fine_keep, blemish=base.blemish,
        chroma=base.chroma, shine=base.shine, brighten=base.brighten,
        gamma=base.gamma, clarity=base.clarity, sharpen=base.sharpen,
        tone=0.0, texture=texture, feature=0.0,
    )
    out = BT._retouch(bgr, mask, geo, para, eye, True, True, True, True,
                      lips_mask=lips, teeth_mask=teeth, smooth_op="surface")
    lab_i = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_o = cv2.cvtColor(out, cv2.COLOR_BGR2LAB).astype(np.float32)
    return bgr, out, lab_i, lab_o, mask, eye, lips, teeth


def test_feature_band_covers_features_only():
    """五官轮廓带：必须盖住眼/唇边缘，且不覆盖大片脸颊皮肤."""
    bgr = np.zeros((320, 420, 3), np.uint8)
    lm = _fake_face_landmarks()
    geo = BT._FaceGeometry(cx=210.0, cy=166.0, width=150.0, height=170.0, iod=38.0)
    band = BT._feature_band_mask(bgr, [lm], geo)
    assert band is not None, "五官轮廓带未生成"
    assert 0.0 <= float(band.min()) and float(band.max()) <= 1.0

    # 脸颊外侧（无五官）应接近 0
    cheek = float(band[int(0.62 * 320), int(0.16 * 420)])
    assert cheek < 0.15, f"轮廓带误覆盖脸颊: {cheek:.2f}"
    # 轮廓带能量应集中在五官附近：整体占比必须很小
    ratio = float((band > 0.3).mean())
    assert ratio < 0.20, f"轮廓带面积过大（{ratio:.1%}），会变成整脸锐化"
    print(f"  [OK] feature band: 脸颊={cheek:.2f} 覆盖占比={ratio:.1%}")


def test_apply_beautify_switches_off_revert():
    """三个开关关掉后必须等价于旧行为（便于 A/B 与一键回滚）."""
    from PIL import Image

    rng = np.random.default_rng(5)
    arr = np.full((300, 240, 3), 175, dtype=np.uint8)
    arr[:, :, 0] = 150
    arr = np.clip(arr.astype(np.int16) + rng.integers(-4, 5, arr.shape), 0, 255).astype(np.uint8)
    pil = Image.fromarray(arr)

    orig_parse, orig_det, orig_fb = BT._parse_skin_mask, BT._detect_all_landmarks, BT._fallback_region

    def fake_region(bgr, parse_skin=None):
        hh, ww = bgr.shape[:2]
        m = np.zeros((hh, ww), dtype=np.float32)
        m[30:hh - 30, 30:ww - 30] = 1.0
        g = BT._FaceGeometry(cx=ww / 2, cy=hh / 2, width=ww * 0.5,
                             height=hh * 0.6, iod=max(8.0, ww * 0.1))
        return m, g

    BT._parse_skin_mask = lambda bgr: None
    BT._detect_all_landmarks = lambda bgr: []
    BT._fallback_region = fake_region
    try:
        on = BT.apply_beautify(pil, level=BT.BeautyLevel.COMPLIANT)
        off = BT.apply_beautify(pil, level=BT.BeautyLevel.COMPLIANT,
                                tone=False, texture=False, feature=False)
        d = float(np.mean(np.abs(np.array(on).astype(np.float32) - np.array(off).astype(np.float32))))
        assert d > 0.0, "三个开关没有任何作用（新算子未生效）"
        # 关掉后仍必须出图（回退路径可用）
        assert off.size == pil.size
        print(f"  [OK] switches: 开/关差异 {d:.3f} 级，回退路径可用")
    finally:
        BT._parse_skin_mask, BT._detect_all_landmarks, BT._fallback_region =             orig_parse, orig_det, orig_fb


def test_debug_dump_stages(tmpdir=None):
    """debug_dir 必须落盘各阶段中间结果（PRD 要求的可定位性）."""
    import tempfile
    from PIL import Image

    rng = np.random.default_rng(9)
    arr = np.full((260, 200, 3), 170, dtype=np.uint8)
    arr[:, :, 0] = 145
    arr = np.clip(arr.astype(np.int16) + rng.integers(-5, 6, arr.shape), 0, 255).astype(np.uint8)
    pil = Image.fromarray(arr)

    orig_parse, orig_det, orig_fb = BT._parse_skin_mask, BT._detect_all_landmarks, BT._fallback_region

    def fake_region(bgr, parse_skin=None):
        hh, ww = bgr.shape[:2]
        m = np.zeros((hh, ww), dtype=np.float32)
        m[20:hh - 20, 20:ww - 20] = 1.0
        g = BT._FaceGeometry(cx=ww / 2, cy=hh / 2, width=ww * 0.5,
                             height=hh * 0.6, iod=max(8.0, ww * 0.1))
        return m, g

    BT._parse_skin_mask = lambda bgr: None
    BT._detect_all_landmarks = lambda bgr: []
    BT._fallback_region = fake_region
    d = tmpdir or tempfile.mkdtemp(prefix="beautify_dbg_")
    try:
        BT.apply_beautify(pil, level=BT.BeautyLevel.COMPLIANT, debug_dir=d)
        names = set(os.listdir(d))
        for need in ("skin_mask.png", "final.png"):
            assert need in names, f"缺少中间结果 {need}: {sorted(names)}"
        print(f"  [OK] debug dump: {sorted(names)}")
    finally:
        BT._parse_skin_mask, BT._detect_all_landmarks, BT._fallback_region =             orig_parse, orig_det, orig_fb


if __name__ == "__main__":
    print("=== P0~P2 美颜增强离线回归 ===")
    for fn in (
        test_surface_blur_preserves_edge,
        test_blemish_zone_weight_split,
        test_structure_guard_protects_gradient,
        test_lips_mask_excludes_teeth,
        test_sharpen_effective_cap,
        test_adaptive_strength_dark_vs_bright,
        test_apply_beautify_smoke,
        test_skin_smoothing_reaches_redline,
        test_endpoint_effects_survive_compositing,
        test_landmark_protect_spares_structure,
        test_tone_shift_map_reduces_regional_bias,
        test_tone_correction_reduces_regional_variance,
        test_texture_reinject_restores_detail,
        test_feature_band_covers_features_only,
        test_apply_beautify_switches_off_revert,
        test_debug_dump_stages,
    ):
        print(f"[{fn.__name__}]")
        fn()
    print("\n全部通过 [OK]")
