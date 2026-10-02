"""DPI 调整模块 — 设置 JPEG 元数据中的 DPI 信息."""

from PIL import Image
from loguru import logger


def adjust_dpi(image: Image.Image, target_dpi: int) -> Image.Image:
    """
    设置图片 DPI 元数据，不改变像素尺寸.
    通过设置 Pillow 的 info['dpi'] 实现在保存 JPEG 时写入.
    返回 image（原位修改）.
    """
    image.info["dpi"] = (target_dpi, target_dpi)
    logger.debug(f"DPI set to {target_dpi}")
    return image
