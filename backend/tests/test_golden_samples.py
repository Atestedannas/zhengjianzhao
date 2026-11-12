"""P2 黄金样本门禁：多输入质量下的「美颜确实生效 + 红线不越界」回归.

【为什么要有这个脚本】P0/P1 的全部结论只建立在 1~2 张样张上，且历史上反复
出现「测试全过、用户看不出变化」「某类输入静默降级」的问题。本脚本把验证口径
固定下来：
  * 2 张真人样张（高清户外照 / 231×268 蓝底证件照小图）；
  * 3 个由高清样张派生的劣化变体（低分辨率上传、JPEG 重压缩、暗沉曝光）——
    覆盖真实用户最常遇到的低质输入，且不占用仓库的额外图片；
  * 1 张卡通简笔画负样本（会被人脸模型误检），只保证「不崩、不出诡异色块」，
    不要求它产生美颜收益。

【阈值策略】宁宽勿紧：阈值取实测值的 50~70%，目的是抓住「整条链路被饿死 /
蒙版失效 / spot 被误关 / 身份或肤色档被改」这类回归，而不是卡死小幅波动。

用法（CI 直接跑，退出码非 0 即失败）:
    TEMP=D:/.../.p1_tmp .venv/Scripts/python.exe backend/tests/test_golden_samples.py
    ... --report   额外产出 P0/P1/P2 三代指标表
"""
from __future__ import annotations

import io
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "tests"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import p0_ab_audit as A  # noqa: E402
from PIL import Image, ImageEnhance  # noqa: E402

from app.core.image_engine import beautify as B  # noqa: E402
from app.core.image_engine.face_detect import align_face  # noqa: E402
from app.core.image_engine.pipeline import (  # noqa: E402
    ProcessParams, process_image, render_baseline,
)

OUT = os.path.join(PROJECT, "p2_ab_out")
os.makedirs(OUT, exist_ok=True)

MAIN = os.path.join(PROJECT, "tests", "test_photo.jpg")
ALT = os.path.join(PROJECT, "dsh_p1.png")
CARTOON = os.path.join(PROJECT, "dsh_ab_out", "dsh_p2_in.png")

# 当前生产参数（与 beautify 包 parts/01_config.py 默认值逐项一致）。每次 run 前重置，
# 防止三代对比里 monkeypatch 的旋钮串味。
PROD = {
    "SPOT_GAIN": 1.30, "SPOT_RADIUS_RATIO": 0.60,
    "SPOT_GATE_LO": 0.8, "SPOT_GATE_HI": 6.0,
    "CHROMA_GATE_LO": 0.8, "CHROMA_GATE_HI": 6.0,
    "TONE_GRID_ACTIVE": 6, "LIP_NEED_LO": 14.0, "LIP_NEED_HI": 36.0,
    "EYE_CATCHLIGHT_PCT": 92.0, "TEXTURE_REINJECT_MAX": 0.40,
}
# 三代标定值：P0 无 spot 轴 / P1 单一样张标定 / P2 两张真人样张重标定。
GENERATIONS = {
    "P0": {"SPOT_RADIUS_RATIO": 0.18, "SPOT_GAIN": 0.0,
           "TONE_GRID_ACTIVE": 4, "LIP_NEED_LO": 10.0, "LIP_NEED_HI": 30.0,
           "EYE_CATCHLIGHT_PCT": 97.0},
    "P1": {"SPOT_RADIUS_RATIO": 0.45, "SPOT_GAIN": 1.00},
    "P2": {"SPOT_RADIUS_RATIO": 0.60, "SPOT_GAIN": 1.30},
}

MIN_IDENTITY = 0.96          # 与 _beautify_warnings 的 WARN 线一致
MIN_MEAN_ABS = 2.0           # 美颜确实生效（历史「0.2 级」空转问题的下限）
MIN_ZONE_DROP_SKIN = 0.15    # 皮肤区色差至少要压下去
MIN_SPOT_DROP = -0.01        # 色斑不许被修得更显眼
MAX_P95_ABS = 25.0           # 不过修


def reset():
    for k, v in PROD.items():
        setattr(B, k, v)


def make_params(spot: bool = True) -> ProcessParams:
    return ProcessParams(
        width=295, height=413, dpi=300,
        beautify_level=1, bg_color="keep",
        retouch_strength=1.0, id_photo_align=True,
        beautify_spot=spot, min_kb=0, max_kb=10000,
    )


def run_pair(src: Image.Image, knobs: dict | None = None, spot: bool = True):
    reset()
    for k, v in (knobs or {}).items():
        setattr(B, k, v)
    p = make_params(spot=spot)
    base = Image.open(io.BytesIO(render_baseline(src, p).data)).convert("RGB")
    r = process_image(src, p)
    out = Image.open(io.BytesIO(r.data)).convert("RGB")
    m = A._metrics(base, out)
    m.update(A._skin_zone_metrics(base, out))
    return m, out, base, dict(r.beautify_report or {}), list(r.warnings)


def ita_class(img: Image.Image):
    """与 p1_redline.py 同口径：工作分辨率上算 ITA° 并归入肤色档."""
    work = (align_face(img, 1024, 1434) if img.size != (295, 413)
            else img.resize((1024, 1434), Image.LANCZOS))
    bgr = B._pil_to_cv2(work)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lms = B._detect_all_landmarks(bgr)
    if not lms:
        return None
    boxes = [B._box_from_landmarks(lm, bgr.shape[1], bgr.shape[0]) for lm in lms]
    parse = B._parse_skin_mask(bgr, boxes or None)
    mask, _geo = B._landmark_region_mask(bgr, lms, parse)
    if mask is None:
        return None
    ita = B.skin_ita(lab, mask)
    return B._ita_skin_class(ita) if ita is not None else None


def golden_images():
    src = Image.open(MAIN).convert("RGB")
    low = src.resize((600, 800), Image.LANCZOS)
    buf = io.BytesIO()
    src.resize((840, 1120), Image.LANCZOS).save(buf, format="JPEG", quality=60)
    buf.seek(0)
    jpg = Image.open(buf).convert("RGB")
    dark = ImageEnhance.Brightness(src).enhance(0.82)
    return [
        ("main", src), ("alt", Image.open(ALT).convert("RGB")),
        ("main_lowres", low), ("main_jpeg", jpg), ("main_dark", dark),
    ]


def check_real(name, m, out, base, rep, warns, ita0, failures):
    def require(cond, msg):
        if not cond:
            failures.append(f"[{name}] {msg}")

    require(list(out.size) == [295, 413], f"出图尺寸异常 {out.size}")
    require(rep.get("applied") is True, "美颜未生效 (applied != True)")
    require(float(rep.get("identity_sim") or 0) >= MIN_IDENTITY,
            f"身份相似度 {rep.get('identity_sim')} < {MIN_IDENTITY}")
    require(not warns, f"出现告警 {warns}")
    require(m["mean_abs"] >= MIN_MEAN_ABS,
            f"整体改动 {m['mean_abs']:.2f} < {MIN_MEAN_ABS}（美颜疑似空转）")
    require(m["zone_drop_skin"] >= MIN_ZONE_DROP_SKIN,
            f"皮肤区色差降 {m['zone_drop_skin']:+.1%} < {MIN_ZONE_DROP_SKIN:.0%}")
    require(m["spot_drop"] >= MIN_SPOT_DROP,
            f"色斑降 {m['spot_drop']:+.1%} < {MIN_SPOT_DROP:.0%}（痘印被修得更显眼）")
    require(m["p95_abs"] <= MAX_P95_ABS,
            f"P95 改动 {m['p95_abs']} > {MAX_P95_ABS}（疑似过修）")
    ita1 = ita_class(out)
    require(ita0 is not None and ita1 == ita0,
            f"ITA 肤色档变更 {ita0} -> {ita1}")
    return ita1


def main():
    do_report = "--report" in sys.argv
    failures = []
    rows = []

    # ---- 红线常量锁定：这些值一旦被改动，必须重新做完整合规评审 ----
    if B.SPOT_MAX_DELTA != 4.0:
        failures.append(f"[红线] SPOT_MAX_DELTA 被改为 {B.SPOT_MAX_DELTA}（应为 4.0）")
    if B.BeautyLevel.COMPLIANT != 1:
        failures.append("[红线] COMPLIANT 档位枚举值被改动")

    print("=== P2 黄金样本门禁 ===")
    for name, src in golden_images():
        m, out, base, rep, warns = run_pair(src)
        ita0 = ita_class(base)
        ita1 = check_real(name, m, out, base, rep, warns, ita0, failures)
        rows.append({"sample": name, "src_size": list(src.size), "ita_base": ita0,
                     "ita_out": ita1, "identity": rep.get("identity_sim"),
                     "skin_diff": rep.get("skin_diff"), "applied": rep.get("applied"),
                     "warnings": warns, **m})
        print(f"  {name:14s} 肤色区色差降 {m['zone_drop_skin']:+7.1%} | "
              f"色斑降 {m['spot_drop']:+6.1%} | 身份 {rep.get('identity_sim', 0):.5f} | "
              f"ITA {ita0}->{ita1} | meanAbs {m['mean_abs']:.2f} | "
              f"P95 {m['p95_abs']:.0f} | {out.size}")

    # ---- 卡通负样本：被误检成人脸也不能崩、不能产生诡异色块 ----
    if os.path.exists(CARTOON):
        cart = Image.open(CARTOON).convert("RGB")
        m, out, _base, rep, warns = run_pair(cart)
        rows.append({"sample": "cartoon_negative", "src_size": list(cart.size), **m})
        if list(out.size) != [295, 413]:
            failures.append(f"[cartoon] 出图尺寸异常 {out.size}")
        if m["mean_abs"] > 20.0:
            failures.append(f"[cartoon] 异常大改动 meanAbs={m['mean_abs']:.2f}")
        print(f"  {'cartoon_neg':14s} 负样本不崩 | meanAbs {m['mean_abs']:.2f} | {out.size}")

    # ---- 一键回滚开关：关掉 spot 后报告必须为 0，且出图确实不同 ----
    src = Image.open(MAIN).convert("RGB")
    m_on, out_on, _b1, rep_on, _w1 = run_pair(src, spot=True)
    m_off, out_off, _b2, rep_off, _w2 = run_pair(src, spot=False)
    if rep_off.get("spot_strength", 0.0) != 0.0:
        failures.append(f"[回滚] beautify_spot=False 但 spot_strength={rep_off.get('spot_strength')}")
    diff = float(np.abs(np.array(out_on, dtype=np.float32)
                        - np.array(out_off, dtype=np.float32)).mean())
    if diff <= 0.02:
        failures.append(f"[回滚] 开关无效，两次出图平均差 {diff:.4f}")
    print(f"  {'spot 开关':14s} on={rep_on.get('spot_strength')} "
          f"off={rep_off.get('spot_strength')} 出图平均差={diff:.3f}")

    # ---- 三代对比（--report）----
    if do_report:
        lines = ["=== P0 / P1 / P2 三代对比（两张真人样张，生产口径 295×413 JPEG 出图）===",
                 f"{'样本':6s}{'代':5s}{'肤色区色差降':>14s}{'区域色差降':>13s}"
                 f"{'色斑降':>10s}{'dL':>8s}{'meanAbs':>10s}{'身份':>10s}", "-" * 76]
        gen_rows = []
        for sname, spath in (("main", MAIN), ("alt", ALT)):
            simg = Image.open(spath).convert("RGB")
            for gen, knobs in GENERATIONS.items():
                m, _o, _b, rep, _w = run_pair(simg, knobs=knobs)
                gen_rows.append({"sample": sname, "gen": gen,
                                 "zone_drop_skin": m["zone_drop_skin"],
                                 "zone_spread_drop": m["zone_spread_drop"],
                                 "spot_drop": m["spot_drop"], "dL": m["dL"],
                                 "mean_abs": m["mean_abs"],
                                 "identity": rep.get("identity_sim")})
                lines.append(
                    f"{sname:6s}{gen:5s}{m['zone_drop_skin']:+13.1%}"
                    f"{m['zone_spread_drop']:+12.1%}{m['spot_drop']:+10.1%}"
                    f"{m['dL']:+8.2f}{m['mean_abs']:10.2f}"
                    f"{rep.get('identity_sim', 0):10.5f}")
        lines += ["", "说明：P0 为无 spot 轴的起点；P1 为单一样张标定；"
                  "P2 为两张真人样张重标定的当前生产值。",
                  "阈值下限：身份 >=0.96 / ITA 肤色档不变 / "
                  "皮肤区色差降 >=15% / 色斑降 >=-1% / P95 改动 <=25。"]
        text = "\n".join(lines) + "\n"
        with open(os.path.join(OUT, "p2_final_metrics.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        json.dump(gen_rows, open(os.path.join(OUT, "p2_generations.json"), "w",
                                 encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\n" + text)

    with open(os.path.join(OUT, "golden_results.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    if failures:
        print("\n门禁失败：")
        for x in failures:
            print("  [FAIL]", x)
        sys.exit(1)
    print(f"\n全部通过 [OK]  样本 {len(rows)} 个，红线 0 越界")
    reset()


if __name__ == "__main__":
    main()
