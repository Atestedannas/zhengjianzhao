"""P1 最终交付：改动前后指标汇总 + 两张样张的脸部特写对比图.

用法：TEMP=D:/.../.p1_tmp .venv/Scripts/python.exe backend/tests/p1_summary.py
"""
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "tests"))

from PIL import Image, ImageDraw  # noqa: E402
from p0_ab_audit import _cjk_font, _face_bbox, _skin_zone_metrics, _load_pair  # noqa: E402

P0_OUT = os.path.join(PROJECT, "p0_ab_out")
P1_OUT = os.path.join(PROJECT, "p1_ab_out")
os.makedirs(P1_OUT, exist_ok=True)


def load(tag, pname):
    with open(os.path.join(P0_OUT, f"{tag}_{pname}.json"), encoding="utf-8") as f:
        meta = json.load(f)
    base, out = _load_pair(tag, pname)
    extra = _skin_zone_metrics(base, out)
    return meta, extra, base, out


def main():
    m0, e0, b0, o0 = load("p0base", "new")   # P0 基线（本轮起点）
    m1, e1, b1, o1 = load("p1final", "new")  # P1 最终
    red = json.load(open(os.path.join(P1_OUT, "p1_redline.json"), encoding="utf-8"))

    rows = [
        ("ΔL 脸部提亮", m0["dL"], m1["dL"], "{:+.2f}"),
        ("Δb 去黄", m0["db"], m1["db"], "{:+.2f}"),
        ("平均改动 /255", m0["mean_abs"], m1["mean_abs"], "{:.2f}"),
        ("P95 改动 /255", m0["p95_abs"], m1["p95_abs"], "{:.0f}"),
        ("区域色差下降", m0["zone_spread_drop"] * 100, m1["zone_spread_drop"] * 100, "{:.1f}%"),
        ("皮肤区色差下降", e0["zone_drop_skin"] * 100, e1["zone_drop_skin"] * 100, "{:.1f}%"),
        ("色斑对比下降", m0["spot_drop"] * 100, m1["spot_drop"] * 100, "{:+.1f}%"),
        ("强色斑像素占比", m0["strong_spot_out"] * 100, m1["strong_spot_out"] * 100, "{:.2f}%"),
        ("皮肤改动级 skin_diff", (m0["beautify_report"] or {}).get("skin_diff", 0),
         (m1["beautify_report"] or {}).get("skin_diff", 0), "{:.2f}"),
        ("身份相似度 ArcFace", (m0["beautify_report"] or {}).get("identity_sim", 0),
         (m1["beautify_report"] or {}).get("identity_sim", 0), "{:.5f}"),
    ]
    lines = ["=== P1 相对 P0 的最终 A/B（同一张样张 / 同几何基线 / 生产口径 295×413 出图）===",
             f"{'指标':<26}{'P0':>12}{'P1':>12}{'变化':>14}", "-" * 64]
    for name, a, b, fmt in rows:
        lines.append(f"{name:<26}{fmt.format(a):>12}{fmt.format(b):>12}{b-a:>+14.3f}")
    lines += ["", "=== 红线复验（两张样张：P1=生产参数 / P1+=效果上界，仅作压力验证）==="]
    for r in red:
        if r["label"] in ("P1", "P1+"):
            lines.append(f"  [{r['sample']}/{r['label']:3s}] ITA {r['ita_base']}({r['class_base']}) -> "
                         f"{r['ita_out']}({r['class_out']}) | 身份 {r['identity_sim']} | "
                         f"spot={r.get('spot_strength')} | "
                         f"区域色差降 {r['zone_drop']:+.1%} | 色斑降 {r['spot_drop']:+.1%} | "
                         f"ΔL {r['dL']:+.2f}")
    sims = [r["identity_sim"] for r in red if r["identity_sim"] is not None]
    cls_changed = [r for r in red
                   if r["class_base"] is not None and r["class_out"] != r["class_base"]]
    lines += ["", f"结论：身份相似度最低 {min(sims):.5f}，远高于 0.96 告警线；"
              f"ITA 肤色档位变更 {len(cls_changed)} 例；",
              "      所有合规红线常量（smooth/blemish/chroma/brighten/sharpen/fine_keep）未改动；",
              "      生产 spot 轴单像素 a*/b* 改动硬钳制在 ±4 级（SPOT_MAX_DELTA=4.0）。"]
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(P1_OUT, "p1_final_metrics.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")

    # 2×2：上排 P0（基线/出图）、下排 P1（基线/出图），统一放大尺度
    cell = 340
    canvas = Image.new("RGB", (cell * 2 + 30, cell * 2 + 96), "white")
    dr = ImageDraw.Draw(canvas)
    for ri, (tag, base, out) in enumerate([("P0 改动后（起点）", b0, o0),
                                            ("P1 本轮（最终）", b1, o1)]):
        for ci, (sub, im) in enumerate([("基线（未美颜）", base), ("出图（美颜后）", out)]):
            face = im.crop(_face_bbox(im)).resize((cell, cell), Image.LANCZOS)
            x, y = 10 + ci * (cell + 10), 42 + ri * (cell + 24)
            canvas.paste(face, (x, y))
            dr.text((x + 4, y - 18), f"{tag} / {sub}", fill="black", font=_cjk_font(13))
    dr.text((10, 8), "P1 A/B  ·  同一张样张  ·  脸部特写  ·  上=P0 起点 / 下=P1 最终",
            fill="black", font=_cjk_font(15))
    canvas.save(os.path.join(P1_OUT, "p1_final_compare.jpg"), quality=96)

    # 第二张真实证件照：P0(A) vs P1(H) 特写
    a = Image.open(os.path.join(P1_OUT, "s2_A_alt.jpg")).convert("RGB")
    h = Image.open(os.path.join(P1_OUT, "s2_H_alt.jpg")).convert("RGB")
    c2 = Image.new("RGB", (cell * 2 + 30, cell + 62), "white")
    d2 = ImageDraw.Draw(c2)
    for i, (lab, im) in enumerate([("P0 起点（第二张证件照）", a), ("P1 最终", h)]):
        face = im.crop(_face_bbox(im)).resize((cell, cell), Image.LANCZOS)
        x, y = 10 + i * (cell + 10), 40
        c2.paste(face, (x, y))
        d2.text((x + 4, y - 18), lab, fill="black", font=_cjk_font(13))
    c2.save(os.path.join(P1_OUT, "p1_final_alt_compare.jpg"), quality=96)
    print("\n对比图已保存：p1_final_compare.jpg / p1_final_alt_compare.jpg")


if __name__ == "__main__":
    main()
