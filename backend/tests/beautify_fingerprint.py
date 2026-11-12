"""beautify 模块重构安全网：记录/比对生产链路的逐字节出图指纹.

拆分 3633 行的 beautify.py 时，最大的风险是「代码还能跑，但某个私有符号
丢了、动态 setattr 失效、模型路径少了一层目录」。本脚本在重构前后各跑一次：

    拆分前: python backend/tests/beautify_fingerprint.py --out p3_ab_out/pre_split.json
    拆分后: python backend/tests/beautify_fingerprint.py --check p3_ab_out/pre_split.json

覆盖：2 张真人样张 × 3 个档位 × 多个开关组合、动态旋钮 setattr、
直接调用的私有算子符号、以及模型路径解析。任何一项哈希不一致即失败。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from app.core.image_engine import beautify as B  # noqa: E402
from app.core.image_engine.pipeline import ProcessParams, process_image  # noqa: E402

MAIN = ROOT / "tests" / "test_photo.jpg"
ALT = ROOT / "dsh_p1.png"

# 重构后必须仍能通过 `B.<name>` 访问到的符号（历史扫描/回归脚本在用）。
REQUIRED_SYMBOLS = [
    "BeautyLevel", "apply_beautify", "apply_studio_beautify", "skin_ita",
    "identity_similarity", "perceptual_distance", "_PARAMS", "_Params",
    "_clamp_compliant", "_adaptive_params", "_retouch", "_tone_shift_map",
    "_texture_reinject", "_feature_band_mask", "_ramp", "_scale", "_fast_blur",
    "_surface_blur", "_guided_1", "_guided_1_fast", "_rolling_guidance",
    "_l0_smooth", "_retinex_normalize", "_parse_skin_mask", "_detect_all_landmarks",
    "_landmark_region_mask", "_fallback_region", "_blemish_patch_repair",
    "_patchmatch_fill", "_detect_blemish_blobs", "_pil_to_cv2", "_ita_scales",
    "SPOT_GAIN", "SPOT_RADIUS_RATIO", "SPOT_MAX_DELTA", "TONE_GRID_ACTIVE",
    "CHROMA_GATE_LO", "LIP_NEED_LO", "EYE_CATCHLIGHT_PCT", "TEXTURE_REINJECT_MAX",
    "COMPLIANT_MAX_SMOOTH", "IDENTITY_SIM_WARN", "RETINEX_MAX_LIFT",
]


def _samples():
    imgs = {"main": Image.open(MAIN).convert("RGB"),
            "alt": Image.open(ALT).convert("RGB")}
    m = imgs["main"]
    small = m.copy()
    small.thumbnail((420, 420), Image.LANCZOS)
    imgs["downscaled"] = small
    buf = io.BytesIO()
    m.save(buf, format="JPEG", quality=60)
    imgs["jpeg60"] = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    imgs["dark"] = ImageEnhance.Brightness(m).enhance(0.82)
    return imgs


def _base(**kw):
    d = dict(width=295, height=413, dpi=300, beautify_level=1, bg_color="keep",
             retouch_strength=1.0, id_photo_align=True, min_kb=0, max_kb=10000)
    d.update(kw)
    return ProcessParams(**d)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect() -> dict:
    imgs = _samples()
    out: dict[str, str] = {}

    cases = [
        ("default", {}),
        ("spot_off", {"beautify_spot": False}),
        ("tone_off", {"beautify_tone": False}),
        ("texture_off", {"beautify_texture": False}),
        ("feature_off", {"beautify_feature": False}),
        ("adaptive_off", {"beautify_adaptive": False}),
        ("strength_0", {"retouch_strength": 0.0}),
        ("strength_05", {"retouch_strength": 0.5}),
        ("level_artistic", {"beautify_level": 4}),
        ("level_studio", {"beautify_level": 5}),
        ("no_align", {"id_photo_align": False}),
        ("bg_blue", {"bg_color": "blue"}),
    ]
    for sname in ("main", "alt", "downscaled", "jpeg60", "dark"):
        src = imgs[sname]
        for cname, kw in cases:
            r = process_image(src, _base(**kw))
            out[f"out/{sname}/{cname}"] = _sha(r.data)
            rep = dict(getattr(r, "beautify_report", None) or {})
            out[f"report/{sname}/{cname}"] = _sha(
                json.dumps(rep, sort_keys=True, default=str).encode("utf-8")
            )

    # 动态旋钮：历史扫描脚本靠 setattr(B, CONST, v) 改参数，重构后必须仍生效。
    orig = (B.SPOT_GAIN, B.SPOT_RADIUS_RATIO)
    for gain, radius in [(1.0, 0.45), (1.3, 0.60), (0.0, 0.18)]:
        B.SPOT_GAIN, B.SPOT_RADIUS_RATIO = gain, radius
        r = process_image(imgs["main"], _base())
        out[f"knob/gain{gain}_r{radius}"] = _sha(r.data)
    B.SPOT_GAIN, B.SPOT_RADIUS_RATIO = orig

    # 私有算子直接调用（验证符号导出 + 基础数值一致）
    out["sym/missing"] = _sha(
        json.dumps([s for s in REQUIRED_SYMBOLS if not hasattr(B, s)]).encode()
    )
    arr = np.linspace(-2.0, 9.0, 64, dtype=np.float32)
    vals = [
        float(B._ramp(arr, 0.8, 6.0).sum()),
        float(B._scale(80.0, 0.45, 3)),
        float(np.asarray(B._pil_to_cv2(imgs["main"])).shape[0]),
        float(B._clamp_compliant(B._PARAMS[B.BeautyLevel.HIGH]).smooth),
        float(B._ita_scales(35.0)[0]),
        float(B.COMPLIANT_MAX_SMOOTH),
        float(B.SPOT_GAIN),
    ]
    out["sym/ops"] = _sha(json.dumps([round(v, 6) for v in vals]).encode())

    # 模型路径解析：拆分后目录多一层，路径错会静默降级
    out["sym/model_paths"] = _sha(
        json.dumps([str(B._parse_model_path()), str(B._model_path()),
                    str(B._arc_model_path())], sort_keys=True).encode()
    )
    out["meta/module_file"] = str(getattr(B, "__file__", ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="写出指纹 JSON")
    ap.add_argument("--check", help="与该指纹 JSON 比对")
    args = ap.parse_args()
    cur = collect()
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cur, indent=2, ensure_ascii=False, sort_keys=True),
                     encoding="utf-8")
        print(f"指纹已写入 {p} ({len(cur)} 项)")
    if args.check:
        old = json.loads(Path(args.check).read_text(encoding="utf-8"))
        diff = [k for k in sorted(set(old) | set(cur)) if old.get(k) != cur.get(k)
                and k != "meta/module_file"]
        # 模块路径本身允许变化，但内容必须可定位
        print(f"比对 {len(old)} 项，差异 {len(diff)} 项")
        for k in diff[:25]:
            print(f"  [DIFF] {k}: {str(old.get(k))[:16]} -> {str(cur.get(k))[:16]}")
        print("模块路径(允许变化):", cur.get("meta/module_file"))
        if diff:
            print("指纹不一致 [FAIL]")
            return 1
        print("逐字节一致 [OK]")
    if not args.out and not args.check:
        print(json.dumps({k: v[:12] for k, v in list(cur.items())[:8]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
