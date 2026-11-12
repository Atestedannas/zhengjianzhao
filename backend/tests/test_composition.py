"""证件照构图门禁（2026-11 眼位构图专项）.

与 test_edge_quality.py 的合成图黑盒测试互补：这里在**真实样张**上量
「距上边沿」口径的头顶留白 / 眼位 / Haar 框宽，并把三条结论固化成断言：

1. 眼位必须落在成人国家标准 0.30H ~ 0.50H（国家移民局《出入境证件相片
   照相指引》/ ICAO DOC 9303）。旧口径 0.50~0.60 已废止。
2. 源图不足导致头顶留白归零时，必须打 Composition warning，不许静默出图。
3. 口径常量从旧值 (0.56 / [0.50,0.60]) 修正到 (0.3633 / [0.30,0.50])
   **不得改变任何出图像素** —— 保证 P0/P1/P2 三代 A/B 基线继续可比。
   备份在 p3_backup_20261111/face_detect.py，用独立模块加载后逐像素对比。

用法：
    python backend/tests/test_composition.py            # 跑门禁
    python backend/tests/test_composition.py --report   # 另存文本报告
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import re
import sys
from pathlib import Path

import numpy as np
from loguru import logger
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.image_engine import face_detect as FD  # noqa: E402

OUT_DIR = ROOT / "p3_ab_out"
LEGACY = ROOT / "p3_backup_20261111" / "face_detect.py"

# (标签, 路径, 是否要求 5%~10% 头顶留白)
# alt 是 231×268 已裁好的小图，源图本身没有足够留白，归入「允许例外但必须告警」。
SAMPLES = [
    ("main", ROOT / "tests" / "test_photo.jpg", True),
    ("alt", ROOT / "dsh_p1.png", False),
]
SIZES = [(295, 413), (413, 579), (260, 378)]

# 备份版本日志写「脸宽占比」，当前版本写「Haar框宽占比」，两种都要能解析。
_PAT = re.compile(
    r"头顶留白=([\d.]+)%.*?眼位=([\d.]+)%.*?(?:Haar框宽占比|脸宽占比)=([\d.]+)%"
)


def _capture():
    """挂一个 loguru sink，返回 (清空函数, 取日志函数)."""
    buf: list[str] = []
    sid = logger.add(lambda m: buf.append(str(m)), level="INFO")

    def clear():
        buf.clear()

    def read():
        return list(buf)

    def close():
        logger.remove(sid)

    return clear, read, close


def _measure(align_face, img, tw, th, clear, read):
    clear()
    out = align_face(img, tw, th)
    lines = read()
    comp = None
    for m in lines:
        if "Face aligned:" in m:
            hit = _PAT.search(m)
            if hit:
                comp = tuple(float(x) / 100.0 for x in hit.groups())
    warned = any("Composition warning" in m for m in lines)
    if comp is None:
        raise AssertionError(f"{tw}x{th}: 未捕获到构图日志（检测不到人脸？）")
    return np.array(out), comp, warned


def _load_legacy():
    """把改动前的 face_detect.py 作为独立模块加载，用于像素级对比."""
    if not LEGACY.exists():
        return None
    spec = importlib.util.spec_from_file_location("_legacy_face_detect", LEGACY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="写出文本报告")
    args = ap.parse_args()

    clear, read, close = _capture()
    legacy = _load_legacy()
    rows, errors = [], []
    try:
        for name, path, require_margin in SAMPLES:
            if not path.exists():
                errors.append(f"缺少样张 {path}")
                continue
            img = Image.open(path).convert("RGB")
            for tw, th in SIZES:
                arr, (top, eye, face_w), warned = _measure(
                    FD.align_face, img, tw, th, clear, read
                )

                # ① 眼位合规带
                if not (FD.COMPOSE_EYE_LINE_MIN - 0.02 <= eye <= FD.COMPOSE_EYE_LINE_MAX + 0.02):
                    errors.append(
                        f"{name} {tw}x{th} 眼位 {eye:.1%} 越出 "
                        f"{FD.COMPOSE_EYE_LINE_MIN:.0%}~{FD.COMPOSE_EYE_LINE_MAX:.0%}"
                    )
                # ② 留白：高清样本强约束；小图例外但必须告警
                if require_margin and not (
                    FD.COMPOSE_TOP_MARGIN_MIN - 0.02 <= top <= FD.COMPOSE_TOP_MARGIN_MAX + 0.02
                ):
                    errors.append(f"{name} {tw}x{th} 头顶留白 {top:.1%} 越出 5%~10%")
                if top < FD.COMPOSE_TOP_MARGIN_MIN - 0.005 and not warned:
                    errors.append(f"{name} {tw}x{th} 留白 {top:.1%} 不足却未打 warning（静默出图）")

                # ③ 口径修正不得改变出图
                md = None
                if legacy is not None:
                    old_arr, _, _ = _measure(
                        legacy.align_face, img, tw, th, clear, read
                    )
                    md = int(np.abs(arr.astype(np.int16) - old_arr.astype(np.int16)).max())
                    if md != 0:
                        errors.append(f"{name} {tw}x{th} 口径修正改变了出图 maxdiff={md}")

                note = "源图不足(已告警)" if top < 0.05 else "正常"
                rows.append(
                    f"  {name:5s} {tw}x{th:<4d} 留白={top:6.1%} 眼位={eye:6.1%} "
                    f"Haar框宽={face_w:6.1%} 新旧口径maxdiff={md} {note}"
                )
    finally:
        close()

    lines = [
        "=== 证件照构图门禁（距上边沿口径）===",
        f"合规带: 眼位 {FD.COMPOSE_EYE_LINE_MIN:.0%}~{FD.COMPOSE_EYE_LINE_MAX:.0%} "
        f"(目标 {FD.COMPOSE_EYE_LINE:.2%}) | "
        f"头顶留白 {FD.COMPOSE_TOP_MARGIN_MIN:.0%}~{FD.COMPOSE_TOP_MARGIN_MAX:.0%}",
        f"依据: 《出入境证件相片照相指引》成人 0.3H~0.5H；居民身份证 眼距下边沿 ≥207px/441px",
        f"备份对照: {LEGACY.relative_to(ROOT) if LEGACY.exists() else '缺失(跳过像素对比)'}",
        "",
        *rows,
        "",
    ]
    if errors:
        lines.append(f"门禁失败 {len(errors)} 项:")
        lines += [f"  [FAIL] {e}" for e in errors]
    else:
        lines += [
            "全部通过 [OK]",
            "说明: alt 为 231×268 已裁小图，越界收缩优先保证不拉伸像素，",
            "      头顶留白归零属已知例外，必须带 Composition warning 出图。",
        ]
    text = "\n".join(lines)
    print(text)

    if args.report:
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "composition_report.txt").write_text(text + "\n", encoding="utf-8")
        print(f"\n报告已写入 {(OUT_DIR / 'composition_report.txt').relative_to(ROOT)}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
