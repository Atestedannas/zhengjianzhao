"""P1 第二轮扫描：气色预算（唇色 / 眼神光 / 分区网格）× spot 尺度。

第一轮已确认 spot 单独调参不会过修，但增益有限（最终出图色斑降 0.6~6.4%）。
本轮把「缩图后仍能存活」的低频气色项纳入，找出整体可见度最高的组合，
并用第二张真实证件照 dsh_p1.png 做过修验证（皮肤更粗糙、更暗）。

用法：TEMP=D:/.../.p1_tmp .venv/Scripts/python.exe backend/tests/p1_sweep2.py
"""
import io
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "tests"))

import p0_ab_audit as A  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402
from app.core.image_engine import beautify as B  # noqa: E402
from app.core.image_engine.pipeline import (  # noqa: E402
    ProcessParams, process_image, render_baseline,
)

OUT = os.path.join(PROJECT, "p1_ab_out")
os.makedirs(OUT, exist_ok=True)
SAMPLES = {
    "main": os.path.join(PROJECT, "tests", "test_photo.jpg"),
    "alt": os.path.join(PROJECT, "dsh_p1.png"),
}


def make_params():
    return ProcessParams(
        width=295, height=413, dpi=300,
        beautify_level=1, bg_color="keep",
        retouch_strength=1.0, id_photo_align=True,
        min_kb=0, max_kb=10000,
    )


def reset():
    B.SPOT_GAIN = 0.55
    B.SPOT_RADIUS_RATIO = 0.18
    B.SPOT_GATE_LO, B.SPOT_GATE_HI = 0.8, 6.0
    B.CHROMA_GATE_LO, B.CHROMA_GATE_HI = 0.8, 6.0
    B.LIP_NEED_LO, B.LIP_NEED_HI = 10.0, 30.0
    B.EYE_CATCHLIGHT_PCT = 97.0
    B.TONE_GRID_ACTIVE = 4


def run(src, params, base, knobs):
    for k, v in knobs.items():
        setattr(B, k, v)
    out = Image.open(io.BytesIO(process_image(src, params).data)).convert("RGB")
    m = A._metrics(base, out)
    m.update(A._skin_zone_metrics(base, out))
    return m, out


# 固定 spot 用第一轮的「中」档（r=0.30 / g=0.85）：目视不过修、指标明显好于保守档
SPOT_BASE = {"SPOT_RADIUS_RATIO": 0.30, "SPOT_GAIN": 0.85}

CASES = [
    ("A P0+spot基线", {}),
    ("B 唇色吃满", {"LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0}),
    ("C 眼神光", {"EYE_CATCHLIGHT_PCT": 92.0}),
    ("D 分区网格6", {"TONE_GRID_ACTIVE": 6}),
    ("E 唇+眼", {"LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
                 "EYE_CATCHLIGHT_PCT": 92.0}),
    ("F 唇+眼+网格", {"LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
                      "EYE_CATCHLIGHT_PCT": 92.0, "TONE_GRID_ACTIVE": 6}),
    ("G F+chroma放宽", {"LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
                        "EYE_CATCHLIGHT_PCT": 92.0, "TONE_GRID_ACTIVE": 6,
                        "CHROMA_GATE_LO": 0.5, "CHROMA_GATE_HI": 4.0}),
    ("H F+spot更强", {"LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
                       "EYE_CATCHLIGHT_PCT": 92.0, "TONE_GRID_ACTIVE": 6,
                       "SPOT_RADIUS_RATIO": 0.45, "SPOT_GAIN": 1.0}),
]

results = []
print(f"=== P1 第二轮：{len(CASES)} 组 × {len(SAMPLES)} 张样张 ===")
for label, extra in CASES:
    reset()
    knobs = {**SPOT_BASE, **extra}
    row = {"label": label, "knobs": knobs}
    for sname, spath in SAMPLES.items():
        src = Image.open(spath).convert("RGB")
        params = make_params()
        base = Image.open(io.BytesIO(render_baseline(src, params).data)).convert("RGB")
        t = time.time()
        m, out = run(src, params, base, knobs)
        row[sname] = {
            "spot_drop": m["spot_drop"], "strong_spot_out": m["strong_spot_out"],
            "zone_drop_skin": m["zone_drop_skin"],
            "zone_spread_drop": m["zone_spread_drop"],
            "dL": m["dL"], "db": m["db"], "da": m["da"],
            "mean_abs": m["mean_abs"], "hf_skin": m["hf_skin"],
            "elapsed": round(time.time() - t, 2),
        }
        tag = label.split()[0]
        out.save(os.path.join(OUT, f"s2_{tag}_{sname}.jpg"), quality=95)
        z = row[sname]
        print(f"{label:16s} [{sname:4s}] 色斑降{z['spot_drop']:+.2%} "
              f"肤色区色差降{z['zone_drop_skin']:+.1%} 区域色差降{z['zone_spread_drop']:+.1%} "
              f"dL={z['dL']:+.2f} db={z['db']:+.2f} da={z['da']:+.2f} "
              f"meanAbs={z['mean_abs']:.2f} HFskin={z['hf_skin']:.3f} {z['elapsed']}s")
    results.append(row)

with open(os.path.join(OUT, "sweep2_results.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("saved", os.path.join(OUT, "sweep2_results.json"))
