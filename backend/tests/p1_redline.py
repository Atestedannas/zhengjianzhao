"""P1 红线验证：候选组合在两张样张上的身份相似度 / ITA 肤色分档 / 生效报告.

用户选定主场景为报名·考试证件照，红线不动。本脚本确认「效果更明显」没有
以改变识别特征或肤色类型为代价。

用法：TEMP=D:/.../.p1_tmp .venv/Scripts/python.exe backend/tests/p1_redline.py
"""
import io
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "tests"))

import p0_ab_audit as A  # noqa: E402
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from app.core.image_engine import beautify as B  # noqa: E402
from app.core.image_engine.pipeline import (  # noqa: E402
    ProcessParams, process_image, render_baseline,
)
from app.core.image_engine.face_detect import align_face  # noqa: E402

OUT = os.path.join(PROJECT, "p1_ab_out")
SAMPLES = {
    "main": os.path.join(PROJECT, "tests", "test_photo.jpg"),
    "alt": os.path.join(PROJECT, "dsh_p1.png"),
}

KNOBS = {
    "P0": {"SPOT_RADIUS_RATIO": 0.18, "SPOT_GAIN": 0.0,
           "TONE_GRID_ACTIVE": 4, "LIP_NEED_LO": 10.0, "LIP_NEED_HI": 30.0,
           "EYE_CATCHLIGHT_PCT": 97.0, "CHROMA_GATE_LO": 0.8, "CHROMA_GATE_HI": 6.0},
    "F": {"SPOT_RADIUS_RATIO": 0.30, "SPOT_GAIN": 0.85,
          "TONE_GRID_ACTIVE": 6, "LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
          "EYE_CATCHLIGHT_PCT": 92.0, "CHROMA_GATE_LO": 0.8, "CHROMA_GATE_HI": 6.0},
    # P1 = P1 时代的生产参数（历史留档，用于三代对比；不是当前生产值）。
    # 当前 P2 生产值为 (SPOT_RADIUS_RATIO=0.60, SPOT_GAIN=1.30)，
    # 其红线复验由 backend/tests/test_golden_samples.py 承担。
    "P1": {"SPOT_RADIUS_RATIO": 0.45, "SPOT_GAIN": 1.00,
           "TONE_GRID_ACTIVE": 6, "LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
           "EYE_CATCHLIGHT_PCT": 92.0, "CHROMA_GATE_LO": 0.8, "CHROMA_GATE_HI": 6.0},
    # P1+ = P1 时代的效果上界（历史留档，仅作压力验证）。
    "P1+": {"SPOT_RADIUS_RATIO": 0.45, "SPOT_GAIN": 1.15,
            "TONE_GRID_ACTIVE": 6, "LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
            "EYE_CATCHLIGHT_PCT": 92.0, "CHROMA_GATE_LO": 0.8, "CHROMA_GATE_HI": 6.0},
}


def apply_knobs(k: dict):
    for name, val in k.items():
        setattr(B, name, val)


def face_ita(pil: Image.Image) -> tuple:
    """在工作分辨率的人脸上算 ITA° 与肤色档（与算法同一口径）."""
    bgr = B._pil_to_cv2(pil)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lms = B._detect_all_landmarks(bgr)
    if not lms:
        return None, None, None
    boxes = [B._box_from_landmarks(lm, bgr.shape[1], bgr.shape[0]) for lm in lms]
    parse = B._parse_skin_mask(bgr, boxes or None)
    mask, geo = B._landmark_region_mask(bgr, lms, parse)
    if mask is None:
        return None, None, None
    ita = B.skin_ita(lab, mask)
    return (round(float(ita), 2) if ita is not None else None,
            B._ita_skin_class(ita) if ita is not None else None,
            round(float(geo.iod), 1))


def main():
    results = []
    for sname, spath in SAMPLES.items():
        src = Image.open(spath).convert("RGB")
        params = ProcessParams(width=295, height=413, dpi=300,
                              beautify_level=1, bg_color="keep",
                              retouch_strength=1.0, id_photo_align=True,
                              min_kb=0, max_kb=10000)
        base_img = Image.open(io.BytesIO(render_baseline(src, params).data)).convert("RGB")
        work = align_face(src, 1024, 1434)
        ita0, cls0, iod0 = face_ita(work)
        for label, knobs in KNOBS.items():
            apply_knobs(knobs)
            r = process_image(src, params)
            out = Image.open(io.BytesIO(r.data)).convert("RGB")
            rep = dict(r.beautify_report or {})
            m = A._metrics(base_img, out)
            ita_o, cls_o, _ = face_ita(out.resize((1024, 1434), Image.LANCZOS))
            row = {
                "sample": sname, "label": label,
                "ita_base": ita0, "class_base": cls0,
                "ita_out": ita_o, "class_out": cls_o,
                "iod_px": rep.get("iod_px"),
                "identity_sim": rep.get("identity_sim"),
                "skin_diff": rep.get("skin_diff"),
                "spot_strength": rep.get("spot_strength"),
                "applied": rep.get("applied"),
                "mask_coverage": rep.get("mask_coverage"),
                "dL": m["dL"], "db": m["db"],
                "mean_abs": m["mean_abs"], "p95_abs": m["p95_abs"],
                "zone_drop": m["zone_spread_drop"],
                "spot_drop": m["spot_drop"],
                "strong_spot": [m["strong_spot_base"], m["strong_spot_out"]],
                "warnings": list(r.warnings),
            }
            results.append(row)
            print(f"[{sname:4s}/{label:3s}] "
                  f"ITA {ita0}({cls0}) -> {ita_o}({cls_o}) | "
                  f"身份={row['identity_sim']} 皮肤改动={row['skin_diff']} "
                  f"色斑降={row['spot_drop']:+.1%} 强色斑={row['strong_spot'][0]:.1%}->{row['strong_spot'][1]:.1%} "
                  f"ΔL={row['dL']:+.2f} Δb={row['db']:+.2f} "
                  f"区域色差降={row['zone_drop']:+.1%} "
                  f"warnings={row['warnings']}")
    with open(os.path.join(OUT, "p1_redline.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n红线判定：身份相似度需 >=0.96（WARN 线），ITA 肤色档需不变。")
    bad = [r for r in results
           if (r["identity_sim"] is not None and r["identity_sim"] < 0.96)
           or (r["class_base"] is not None and r["class_out"] != r["class_base"])]
    print("越线组合数：", len(bad))
    for r in bad:
        print("  ", r["sample"], r["label"], r["identity_sim"],
              r["class_base"], "->", r["class_out"])
    print("结论：", "全部守住红线" if not bad else "存在越线，需回调")


if __name__ == "__main__":
    main()
