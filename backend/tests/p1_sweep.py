"""P1 参数扫描：在**生产口径最终出图**（295×413 + JPEG）上选 spot 参数。

为什么不能只在工作分辨率上量：出图会缩 3.5 倍并经过 JPEG 4:2:0 色度抽样，
痘印尺度的 a/b 改动会被抹掉一大半。工作图上"色斑降 17%"到了最终出图可能
只剩 0.6%。本脚本固定同一张样张 + 同几何基线，只改模块级旋钮，量用户真正
看到的图，避免拍脑袋调参。

用法（photo-zhijianzhao/ 根目录）：
    .venv/Scripts/python.exe backend/tests/p1_sweep.py
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

import p0_ab_audit as A  # noqa: E402  —— 复用其 Haar/ASCII 路径处理与指标函数

from PIL import Image  # noqa: E402
from app.core.image_engine import beautify as B  # noqa: E402
from app.core.image_engine.pipeline import (  # noqa: E402
    ProcessParams, process_image, render_baseline,
)

OUT = os.path.join(PROJECT, "p1_ab_out")
os.makedirs(OUT, exist_ok=True)

src = Image.open(A.SAMPLE).convert("RGB")
params = ProcessParams(
    width=295, height=413, dpi=300,
    beautify_level=1, bg_color="keep",
    retouch_strength=1.0, id_photo_align=True,
    min_kb=0, max_kb=10000,
)
base = Image.open(io.BytesIO(render_baseline(src, params).data)).convert("RGB")


def run(knobs: dict) -> dict:
    for k, v in knobs.items():
        setattr(B, k, v)
    out = Image.open(io.BytesIO(process_image(src, params).data)).convert("RGB")
    m = A._metrics(base, out)
    m.update(A._skin_zone_metrics(base, out))
    return m, out


def reset():
    B.SPOT_GAIN = 0.55
    B.SPOT_RADIUS_RATIO = 0.18
    B.SPOT_GATE_LO, B.SPOT_GATE_HI = 0.8, 6.0
    B.CHROMA_GATE_LO, B.CHROMA_GATE_HI = 0.8, 6.0
    B._PARAMS[B.BeautyLevel.COMPLIANT] = B._PARAMS[B.BeautyLevel.COMPLIANT]


# 第一轮：半径 × 增益（决定"改动能否活过缩图"）
GRID = []
for radius in (0.18, 0.30, 0.45):
    for gain in (0.55, 0.85, 1.15):
        GRID.append({
            "SPOT_RADIUS_RATIO": radius,
            "SPOT_GAIN": gain,
            "SPOT_GATE_LO": 0.8, "SPOT_GATE_HI": 6.0,
            "CHROMA_GATE_LO": 0.8, "CHROMA_GATE_HI": 6.0,
        })

results = []
print(f"=== P1 扫描：{len(GRID)} 组，生产口径最终出图 ===")
for i, knobs in enumerate(GRID):
    reset()
    t = time.time()
    m, out = run(knobs)
    row = {
        "idx": i, **knobs,
        "spot_drop": m["spot_drop"],
        "strong_spot_out": m["strong_spot_out"],
        "zone_drop_skin": m["zone_drop_skin"],
        "zone_spread_drop": m["zone_spread_drop"],
        "dL": m["dL"], "db": m["db"], "mean_abs": m["mean_abs"],
        "hf_skin": m["hf_skin"],
        "elapsed": round(time.time() - t, 2),
    }
    results.append(row)
    print(f"[{i:2d}] r={knobs['SPOT_RADIUS_RATIO']:.2f} g={knobs['SPOT_GAIN']:.2f} "
          f"→ 色斑降 {m['spot_drop']:+.2%} 强斑 {m['strong_spot_out']:.2%} "
          f"肤色区色差降 {m['zone_drop_skin']:+.1%} 区域色差降 {m['zone_spread_drop']:+.1%} "
          f"dL={m['dL']:+.2f} meanAbs={m['mean_abs']:.2f} {row['elapsed']}s")
    out.save(os.path.join(OUT, f"sweep_{i:02d}.jpg"), quality=95)

with open(os.path.join(OUT, "sweep_results.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("saved", os.path.join(OUT, "sweep_results.json"))
