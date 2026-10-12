"""二分法压缩模块 — 精确控制输出文件大小."""

from io import BytesIO
from typing import Tuple

from PIL import Image
from loguru import logger


def binary_compress(
    image: Image.Image, min_kb: int, max_kb: int, fmt: str = "JPEG",
) -> Tuple[bytes, int, str]:
    """
    二分法在 JPEG quality [1, 100] 内搜索，使输出文件大小落在 [min_kb, max_kb] 范围内.

    参数:
        image: PIL.Image (RGB)
        min_kb: 最小文件大小 (KB)
        max_kb: 最大文件大小 (KB)
        fmt: 保存格式 (JPEG)

    返回:
        (result_bytes, actual_quality, warnings)
    """
    if fmt.upper() != "JPEG":
        buf = BytesIO()
        image.save(buf, format=fmt)
        data = buf.getvalue()
        size_kb = len(data) / 1024
        return data, 100, f"Non-JPEG format ({fmt}): {size_kb:.1f}KB"

    # min_kb=0 表示不限制最小值，只要不超 max_kb 就给最高质量
    if min_kb <= 0:
        dpi = image.info.get("dpi", (72, 72))
        for q in (100, 98, 95, 92, 90, 88, 85, 80, 75, 70, 60, 50, 40, 30, 20, 10, 5, 1):
            buf = BytesIO()
            image.save(buf, format="JPEG", quality=q, dpi=dpi)
            data = buf.getvalue()
            size_kb = len(data) / 1024
            if size_kb <= max_kb:
                logger.info(f"High quality compress: q={q} => {size_kb:.1f}KB (min_kb=0)")
                return data, q, ""
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=1, dpi=dpi)
        data = buf.getvalue()
        size_kb = len(data) / 1024
        return data, 1, f"Even quality=1 exceeds max {max_kb}KB: {size_kb:.1f}KB"

    # 【策略】质量优先：在不超过 max_kb 的前提下取最大的 quality。
    # 证件照出图质量敏感，同样的体积预算内应给到最高画质（体积只影响
    # 报名系统的上传上限，画质决定了打印效果）。
    best_data = None
    best_quality = None
    best_size_kb = None
    warnings = ""
    dpi = image.info.get("dpi", (72, 72))

    low, high = 1, 100
    while low <= high:
        mid = (low + high) // 2
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=mid, dpi=dpi)
        data = buf.getvalue()
        size_kb = len(data) / 1024

        if size_kb <= max_kb:
            # 记录「不超上限」里的最高质量
            if best_quality is None or mid > best_quality:
                best_data, best_quality, best_size_kb = data, mid, size_kb
            low = mid + 1
        else:
            high = mid - 1

    if best_data is not None:
        # 浮点边界：20.0KB 对 min_kb=20 不应报「低于下限」，
        # 给 0.5KB 的容差避免无意义的警告透传给用户
        if best_size_kb < min_kb - 0.5:
            warnings = f"Quality set to {best_quality}, but size {best_size_kb:.1f}KB is below min {min_kb}KB."
        logger.info(f"Binary compress: quality={best_quality} => {best_size_kb:.1f}KB")
        return best_data, best_quality, warnings

    # 极端情况：quality=1 都超过 max_kb → 返回体积最小的 q=1 结果
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=1, dpi=dpi)
    data = buf.getvalue()
    size_kb = len(data) / 1024
    return data, 1, f"Even quality=1 exceeds max {max_kb}KB: {size_kb:.1f}KB"
