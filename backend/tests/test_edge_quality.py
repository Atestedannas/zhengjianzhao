"""边缘精修 / 环境光 / 软投影 / 美颜红线的离线烟雾测试（不需要 ONNX 模型）.

运行:
    python backend/tests/test_edge_quality.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.image_engine import background as B          # noqa: E402
from app.core.image_engine import beautify as BT           # noqa: E402


def _synth_portrait(h=413, w=295):
    """合成一张「人像」：白底 + 椭圆头 + 身躯，边缘硬切，用于验证羽化/去污染。"""
    rgb = np.full((h, w, 3), 255, dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    head = ((xx - w * 0.5) ** 2 / (w * 0.18) ** 2 + (yy - h * 0.33) ** 2 / (h * 0.16) ** 2) <= 1
    body = (np.abs(xx - w * 0.5) < w * 0.30) & (yy > h * 0.52)
    mask = head | body
    rgb[mask] = (120, 95, 80)          # 肤色/衣物
    # 造一条「污染带」：人物外侧一圈近白但非纯白，模拟旧背景下残留
    return rgb, mask.astype(np.float32)


def test_alpha_refine_no_jagged():
    rgb, mask = _synth_portrait()
    a = mask.copy()
    a_ref = B._refine_alpha(a, rgb.astype(np.float32))

    assert a_ref.min() >= 0.0 and a_ref.max() <= 1.0, "alpha 必须归一到 0~1"
    # 羽化后必须存在过渡带（0.02<α<0.98），不能是二值切边
    band = int(np.sum((a_ref > 0.02) & (a_ref < 0.98)))
    assert band > 200, f"过渡带像素过少({band})，说明边缘仍被硬切"
    # 腐蚀生效：前景面积必须略小于原始 mask
    assert a_ref.sum() < a.sum(), "腐蚀未生效，污染带没有被割掉"
    print(f"  [OK] alpha refine: band={band}px, fg {a.sum():.0f} -> {a_ref.sum():.0f}")


def test_ambient_blend_removes_cast():
    rgb, mask = _synth_portrait()
    a = B._refine_alpha(mask, rgb.astype(np.float32))
    target = B.BG_COLOR_MAP["blue"]

    out = B._blend_ambient(rgb.astype(np.float32), a, target)
    # 过渡带内应被背景色拉近（蓝底：B 通道相对 R 上升）
    band = ((a > 0.2) & (a < 0.8))
    assert band.sum() > 100, "无过渡带，无法验证环境光"
    d_blue = np.mean((out[..., 2] - out[..., 0])[band] - (rgb.astype(np.float32)[..., 2] - rgb.astype(np.float32)[..., 0])[band])
    assert d_blue > 0, f"环境光未生效（Δ蓝={d_blue:.2f}）"
    print(f"  [OK] ambient blend: Δblue={d_blue:.2f} (蓝底冷色晕染已生效)")


def test_drop_shadow_shape():
    _, mask = _synth_portrait()
    sh = B._drop_shadow(mask.astype(np.float32))
    assert sh.max() <= B._SHADOW_OPACITY + 1e-6, "投影不透明度超上限"
    assert sh.max() > 0.0, "投影未生成"
    # 投影能量应集中在人物下方（下移生效）
    top = sh[: sh.shape[0] // 2].sum()
    bot = sh[sh.shape[0] // 2:].sum()
    assert bot > top, "投影没有下移"
    print(f"  [OK] drop shadow: opacity={sh.max():.3f} top={top:.0f} bottom={bot:.0f}")


def test_compliant_clamp_red_lines():
    """任何档位经钳制后都必须落在 PRD 红线上限内。"""
    for lvl in (BT.BeautyLevel.COMPLIANT, BT.BeautyLevel.MEDIUM, BT.BeautyLevel.HIGH):
        p = BT._clamp_compliant(BT._PARAMS[lvl])
        assert p.smooth <= BT.COMPLIANT_MAX_SMOOTH, f"level{lvl} 磨皮超红线: {p.smooth}"
        assert p.fine_keep >= BT.COMPLIANT_MIN_FINE_KEEP, f"level{lvl} 纹理保留不足: {p.fine_keep}"
        assert p.blemish <= BT.COMPLIANT_MAX_BLEMISH
        assert p.chroma <= BT.COMPLIANT_MAX_CHROMA
        assert p.brighten <= BT.COMPLIANT_MAX_BRIGHTEN
        assert p.shine <= BT.COMPLIANT_MAX_SHINE
        assert p.clarity <= BT.COMPLIANT_MAX_CLARITY
        assert p.sharpen <= BT.COMPLIANT_MAX_SHARPEN
        print(f"  [OK] clamp level={lvl}: smooth={p.smooth:.2f} keep={p.fine_keep:.2f} "
              f"brighten={p.brighten:.2f} sharpen={p.sharpen:.2f}")

    # 幂等性
    once = BT._clamp_compliant(BT._PARAMS[BT.BeautyLevel.HIGH])
    twice = BT._clamp_compliant(once)
    assert once == twice, "clamp 不幂等"
    print("  [OK] clamp 幂等")


def test_compose_red_lines():
    from app.core.image_engine import face_detect as FD
    assert FD.COMPOSE_TOP_MARGIN_MIN <= FD.COMPOSE_TOP_MARGIN <= FD.COMPOSE_TOP_MARGIN_MAX
    assert FD.COMPOSE_EYE_LINE_MIN <= FD.COMPOSE_EYE_LINE <= FD.COMPOSE_EYE_LINE_MAX
    assert FD.COMPOSE_FACE_WIDTH_MIN <= FD.COMPOSE_FACE_WIDTH <= FD.COMPOSE_FACE_WIDTH_MAX
    assert FD.MIN_OUTPUT_DPI >= 300
    std = {tuple(v["px"]) for v in FD.ID_PHOTO_STANDARDS.values()}
    assert (295, 413) in std and (413, 579) in std
    print(f"  [OK] 构图红线: 留白={FD.COMPOSE_TOP_MARGIN} 眼位={FD.COMPOSE_EYE_LINE} "
          f"脸宽={FD.COMPOSE_FACE_WIDTH} dpi>={FD.MIN_OUTPUT_DPI}")


def test_align_face_composition_measured():
    """端到端测量 align_face 的实际构图（黑盒：在源图上画色标线，出图后回测）."""
    from PIL import Image, ImageDraw
    from app.core.image_engine import face_detect as FD

    W0, H0 = 1200, 1600
    img = Image.new("RGB", (W0, H0), (200, 180, 170))
    fake = FD.FaceInfo(x=450, y=380, width=300, height=360, confidence=1.0)

    eye_y = int(fake.y + 0.30 * fake.height)
    head_top = int(max(0.0, fake.y - 0.55 * fake.height))

    d = ImageDraw.Draw(img)
    d.rectangle([0, eye_y - 2, W0, eye_y + 2], fill=(255, 0, 0))       # 红 = 眼位
    d.rectangle([0, head_top - 2, W0, head_top + 2], fill=(0, 255, 0))  # 绿 = 头顶

    orig = FD.detect_faces
    FD.detect_faces = lambda image, **kw: [fake]
    try:
        for (tw, th) in ((295, 413), (413, 579), (260, 378)):
            out = FD.align_face(img, tw, th)
            assert out.size == (tw, th), f"输出尺寸错误 {out.size}"

            a = np.array(out).astype(np.int16)
            eye_row = int(np.argmax((a[:, :, 0] - a[:, :, 1]).mean(axis=1)))
            head_row = int(np.argmax((a[:, :, 1] - a[:, :, 0]).mean(axis=1)))

            eye_ratio = eye_row / out.height
            top_ratio = head_row / out.height

            assert FD.COMPOSE_TOP_MARGIN_MIN - 0.02 <= top_ratio <= FD.COMPOSE_TOP_MARGIN_MAX + 0.02, \
                f"{tw}x{th} 头顶留白 {top_ratio:.1%} 超出 5%~10%"
            assert FD.COMPOSE_EYE_LINE_MIN - 0.02 <= eye_ratio <= FD.COMPOSE_EYE_LINE_MAX + 0.02, \
                f"{tw}x{th} 眼位 {eye_ratio:.1%} 超出 50%~60%"

            # 由两线间距反推缩放，再算脸宽占比
            span_px = max(eye_row - head_row, 1)
            scale = span_px / max(eye_y - head_top, 1)
            face_w = fake.width * scale / out.width
            assert 0.50 <= face_w <= 0.80, f"{tw}x{th} 脸宽占比 {face_w:.1%} 偏离 60%~70% 过多"

            print(f"  [OK] {tw}x{th}: 头顶留白={top_ratio:.1%} 眼位={eye_ratio:.1%} "
                  f"脸宽占比={face_w:.1%}")

    finally:
        FD.detect_faces = orig


def test_quality_warnings():
    from app.core.image_engine.pipeline import _quality_warnings
    w = _quality_warnings(295, 413, 300, True)
    assert not any("DPI" in x for x in w), "标准尺寸+300dpi 不应有 DPI 警告"
    w2 = _quality_warnings(0, 0, 72, True)
    assert any("DPI" in x for x in w2) and any("标准尺寸" in x for x in w2)
    w3 = _quality_warnings(231, 268, 300, True)
    assert any("非标准" in x for x in w3) and any("分辨率较低" in x for x in w3)
    print(f"  [OK] quality warnings: std={len(w)} 未指定={len(w2)} 低清={len(w3)}")


if __name__ == "__main__":
    print("=== 证件照出图质量红线烟雾测试 ===")
    for fn in (
        test_alpha_refine_no_jagged,
        test_ambient_blend_removes_cast,
        test_drop_shadow_shape,
        test_compliant_clamp_red_lines,
        test_compose_red_lines,
        test_align_face_composition_measured,
        test_quality_warnings,
    ):
        print(f"[{fn.__name__}]")
        fn()
    print("\n全部通过 ✅")
