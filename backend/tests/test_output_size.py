"""尺寸 / DPI 出图回归测试（离线，不需要 mediapipe / ONNX）.

覆盖:
  * crop / exact / fit / fill 四种缩放模式的实际出图像素 == 请求像素
  * 「按宽度 / 按高度」只给一个维度时也要生效（此前会被静默跳过）
  * JPEG 里写入的 DPI 元数据 == 请求 DPI
  * width=height=0（通用扫描件）保持原图尺寸

运行:
    py backend/tests/test_output_size.py
"""
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.image_engine.pipeline import ProcessParams, process_image  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name}{(' ' + detail) if detail else ''}")
    if not cond:
        FAILURES.append(name)


def make_portrait(w=900, h=1200):
    arr = np.full((h, w, 3), 235, np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    head = ((xx - w / 2) ** 2 / (w * 0.18) ** 2 + (yy - h * 0.32) ** 2 / (h * 0.16) ** 2) <= 1
    body = (np.abs(xx - w / 2) < w * 0.30) & (yy > h * 0.50)
    arr[head | body] = (150, 170, 205)
    return Image.fromarray(arr)


def run(w, h, mode, dpi):
    p = ProcessParams(
        width=w, height=h, resize_mode=mode, dpi=dpi, bg_color="keep",
        output_format="JPEG", beautify_level=0, min_kb=0, max_kb=5000,
    )
    r = process_image(make_portrait(), p)
    return Image.open(io.BytesIO(r.data)), r


def main():
    print("[test_standard_sizes]")
    for name, w, h in [("一寸", 295, 413), ("二寸", 413, 579), ("征兵", 358, 441),
                       ("教师自定义", 300, 400), ("横向", 400, 300)]:
        im, _ = run(w, h, "crop", 300)
        check(f"{name} {w}x{h} crop", im.size == (w, h), f"-> {im.size}")

    print("[test_resize_modes]")
    for mode in ("exact", "fit", "fill"):
        im, _ = run(300, 400, mode, 300)
        check(f"{mode} 300x400", im.size == (300, 400), f"-> {im.size}")

    print("[test_single_axis]")
    im, _ = run(300, 0, "by_width", 300)
    check("by_width w=300 h=0", im.size == (300, 400), f"-> {im.size}")
    im, _ = run(0, 400, "by_height", 300)
    check("by_height w=0 h=400", im.size == (300, 400), f"-> {im.size}")

    print("[test_zero_zero_keeps_original]")
    im, r = run(0, 0, "crop", 300)
    check("w=h=0 keep original", im.size == (900, 1200), f"-> {im.size}")
    check("w=h=0 warns", any("0" in w for w in r.warnings), f"warnings={len(r.warnings)}")

    print("[test_fill_no_black_edge]")
    from app.core.image_engine.resize import _resize_fill

    src = Image.new("RGB", (12, 23), (255, 0, 0))
    filled = _resize_fill(src, 50, 99)
    check("fill 12x23 -> 50x99 size", filled.size == (50, 99), f"-> {filled.size}")
    check("fill no black edge", filled.getpixel((0, 0)) == (255, 0, 0),
          f"corner={filled.getpixel((0, 0))}")

    print("[test_dpi_metadata]")
    for dpi in (300, 350):
        im, _ = run(295, 413, "crop", dpi)
        meta = im.info.get("dpi")
        check(f"dpi={dpi} in jpeg", meta == (dpi, dpi), f"-> {meta}")

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("全部通过 [OK]")


if __name__ == "__main__":
    main()
