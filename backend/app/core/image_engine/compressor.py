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

    # min_kb=0 表示不限制最小值，使用高质量直接输出
    if min_kb <= 0:
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=95, dpi=image.info.get("dpi", (72, 72)))
        data = buf.getvalue()
        size_kb = len(data) / 1024
        if size_kb <= max_kb:
            logger.info(f"High quality compress: q=95 => {size_kb:.1f}KB (min_kb=0)")
            return data, 95, ""
        # 超出 max_kb，降质量直到满足
        for q in range(90, 0, -5):
            buf = BytesIO()
            image.save(buf, format="JPEG", quality=q, dpi=image.info.get("dpi", (72, 72)))
            data = buf.getvalue()
            size_kb = len(data) / 1024
            if size_kb <= max_kb:
                logger.info(f"High quality compress: q={q} => {size_kb:.1f}KB")
                return data, q, ""
        # 极端情况：quality=1 还超
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=1, dpi=image.info.get("dpi", (72, 72)))
        data = buf.getvalue()
        size_kb = len(data) / 1024
        return data, 1, f"Even quality=1 exceeds max {max_kb}KB: {size_kb:.1f}KB"

    low, high = 1, 100
    best_data = None
    best_quality = None
    best_size_kb = None
    warnings = ""

    while low <= high:
        mid = (low + high) // 2
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=mid, dpi=image.info.get("dpi", (72, 72)))
        data = buf.getvalue()
        size_kb = len(data) / 1024

        # 记录最接近目标范围的结果
        if best_size_kb is None or abs(size_kb - (min_kb + max_kb) / 2) < abs(best_size_kb - (min_kb + max_kb) / 2):
            best_data = data
            best_quality = mid
            best_size_kb = size_kb

        if min_kb <= size_kb <= max_kb:
            logger.info(f"Binary compress: quality={mid} => {size_kb:.1f}KB (in range)")
            return data, mid, ""

        if size_kb < min_kb:
            low = mid + 1
        else:
            high = mid - 1

    # 二分法未在范围内找到 → 返回最佳近似
    if best_size_kb is not None:
        if best_size_kb < min_kb:
            warnings = f"Quality set to {best_quality}, but size {best_size_kb:.1f}KB is below min {min_kb}KB."
        else:
            warnings = f"Quality set to {best_quality}, but size {best_size_kb:.1f}KB exceeds max {max_kb}KB."

        logger.warning(f"Binary compress best effort: quality={best_quality} => {best_size_kb:.1f}KB")
        return best_data, best_quality, warnings

    # 极端情况：直接返回 quality=80
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    size_kb = len(data) / 1024
    return data, 80, f"Fallback quality 80: {size_kb:.1f}KB"
