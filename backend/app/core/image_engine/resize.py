"""尺寸调整模块：精确像素缩放 + 保持比例 + 多种缩放策略."""

from enum import Enum
from typing import Tuple, Optional

from PIL import Image
from loguru import logger


class ResizeMode(str, Enum):
    """缩放模式."""
    EXACT = "exact"           # 精确缩放（可能拉伸）
    FIT = "fit"               # 等比缩放，完整容纳在目标尺寸内（留白）
    FILL = "fill"             # 等比缩放，填满目标尺寸（裁剪多余）
    BY_WIDTH = "by_width"     # 按宽度等比缩放
    BY_HEIGHT = "by_height"   # 按高度等比缩放


def _resize_exact(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """精确缩放到目标像素尺寸（不保持比例，可能拉伸）."""
    return image.resize((target_w, target_h), Image.LANCZOS)


def _resize_fit(image: Image.Image, target_w: int, target_h: int, fill_color: Tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """
    等比缩放，完整容纳在目标尺寸内，不足部分填充背景色.
    适用于：需要保持原始比例但尺寸必须精确的场景.
    """
    img_w, img_h = image.size
    ratio = min(target_w / img_w, target_h / img_h)

    new_w = int(img_w * ratio)
    new_h = int(img_h * ratio)

    resized = image.resize((new_w, new_h), Image.LANCZOS)

    # 创建画布并居中粘贴
    canvas = Image.new("RGB", (target_w, target_h), fill_color)
    offset_x = (target_w - new_w) // 2
    offset_y = (target_h - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))

    logger.debug(f"Fit resize: {img_w}x{img_h} -> {new_w}x{new_h} on {target_w}x{target_h}")
    return canvas


def _resize_fill(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    等比缩放，填满目标尺寸，超出部分居中裁剪.
    适用于：需要完整覆盖目标尺寸的场景.
    """
    img_w, img_h = image.size
    ratio = max(target_w / img_w, target_h / img_h)

    new_w = int(img_w * ratio)
    new_h = int(img_h * ratio)

    resized = image.resize((new_w, new_h), Image.LANCZOS)

    # 居中裁剪
    offset_x = (new_w - target_w) // 2
    offset_y = (new_h - target_h) // 2
    result = resized.crop((offset_x, offset_y, offset_x + target_w, offset_y + target_h))

    logger.debug(f"Fill resize: {img_w}x{img_h} -> {new_w}x{new_h} -> crop {target_w}x{target_h}")
    return result


def resize_image(
    image: Image.Image,
    target_w: int,
    target_h: int,
    mode: ResizeMode = ResizeMode.EXACT,
    fill_color: Tuple[int, int, int] = (255, 255, 255),
    upscale: bool = True,
) -> Image.Image:
    """
    图像尺寸调整.

    参数:
        image: PIL.Image 输入图片
        target_w: 目标宽度（像素）
        target_h: 目标高度（像素）
        mode: 缩放模式
            - EXACT: 精确缩放（可能拉伸，不保持比例）
            - FIT: 等比缩放，完整容纳（留白填充）
            - FILL: 等比缩放，填满裁剪
            - BY_WIDTH: 按宽度等比缩放
            - BY_HEIGHT: 按高度等比缩放
        fill_color: FIT 模式下的填充色 RGB 元组
        upscale: 是否允许放大（False 时只缩小不放大）

    返回:
        PIL.Image 调整后的图片
    """
    img_w, img_h = image.size

    # BY_WIDTH / BY_HEIGHT 模式
    if mode == ResizeMode.BY_WIDTH:
        ratio = target_w / img_w
        target_h = int(img_h * ratio)
        if not upscale and ratio > 1.0:
            logger.debug(f"Upscale disabled, keeping original size: {img_w}x{img_h}")
            return image
        result = image.resize((target_w, target_h), Image.LANCZOS)
        logger.info(f"Resize by width: {img_w}x{img_h} -> {target_w}x{target_h}")
        return result

    if mode == ResizeMode.BY_HEIGHT:
        ratio = target_h / img_h
        target_w = int(img_w * ratio)
        if not upscale and ratio > 1.0:
            logger.debug(f"Upscale disabled, keeping original size: {img_w}x{img_h}")
            return image
        result = image.resize((target_w, target_h), Image.LANCZOS)
        logger.info(f"Resize by height: {img_w}x{img_h} -> {target_w}x{target_h}")
        return result

    # EXACT / FIT / FILL 模式
    if not upscale and target_w > img_w and target_h > img_h:
        logger.debug(f"Upscale disabled, target {target_w}x{target_h} larger than source {img_w}x{img_h}")
        return image

    if mode == ResizeMode.EXACT:
        result = _resize_exact(image, target_w, target_h)
    elif mode == ResizeMode.FIT:
        result = _resize_fit(image, target_w, target_h, fill_color)
    elif mode == ResizeMode.FILL:
        result = _resize_fill(image, target_w, target_h)
    else:
        raise ValueError(f"Unknown resize mode: {mode}")

    logger.info(f"Resize ({mode}): {img_w}x{img_h} -> {target_w}x{target_h}")
    return result


# 常见网站/平台图片尺寸预设
SIZE_PRESETS = {
    "微信头像": (360, 360),
    "微信朋友圈封面": (1280, 720),
    "小红书封面": (1080, 1440),
    "小红书配图": (1080, 1080),
    "抖音封面": (1080, 1920),
    "抖音头像": (200, 200),
    "微博配图": (1200, 800),
    "B站封面": (1920, 1080),
    "淘宝主图": (800, 800),
    "淘宝详情图": (790, 1042),
    "闲鱼宝贝图": (800, 800),
    "LinkedIn头像": (400, 400),
    "Facebook封面": (851, 315),
    "Instagram帖子": (1080, 1080),
    "YouTube封面": (1280, 720),
    "Twitter头像": (400, 400),
    "Twitter封面": (1500, 500),
    "1寸证件照": (295, 413),
    "2寸证件照": (413, 579),
    "小2寸证件照": (413, 531),
    "大1寸证件照": (390, 567),
    "护照照片": (390, 567),
    "签证照片(美国)": (600, 600),
    "签证照片(申根)": (413, 531),
    "一寸电子版": (358, 441),
    "二寸电子版": (358, 441),
}


def get_preset(name: str) -> Optional[Tuple[int, int]]:
    """获取预设尺寸，返回 (width, height) 或 None."""
    return SIZE_PRESETS.get(name)