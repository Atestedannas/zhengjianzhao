"""格式转换模块：支持 JPEG / PNG / PDF / BMP / WEBP 多格式输出."""

from io import BytesIO
from typing import Tuple

from PIL import Image
from loguru import logger


# 支持的输出格式
SUPPORTED_OUTPUT_FORMATS = {
    "JPEG": {"ext": "jpg", "mime": "image/jpeg", "description": "JPEG 图片（最常用）"},
    "PNG":  {"ext": "png", "mime": "image/png", "description": "PNG 图片（无损压缩）"},
    "PDF":  {"ext": "pdf", "mime": "application/pdf", "description": "PDF 文档"},
    "BMP":  {"ext": "bmp", "mime": "image/bmp", "description": "BMP 位图"},
    "WEBP": {"ext": "webp", "mime": "image/webp", "description": "WebP 图片（体积小）"},
    "TIFF": {"ext": "tiff", "mime": "image/tiff", "description": "TIFF 图片（高质量）"},
}


def _image_to_pdf_bytes(image: Image.Image) -> bytes:
    """
    将 PIL Image 转换为 PDF 字节流.
    使用 Pillow 内置的 PDF 写入功能.
    """
    # 确保 RGB 模式
    if image.mode != "RGB":
        image = image.convert("RGB")

    buf = BytesIO()
    image.save(buf, format="PDF", resolution=image.info.get("dpi", (300, 300))[0])
    return buf.getvalue()


def _image_to_jpeg_bytes(image: Image.Image, quality: int = 95) -> bytes:
    """将图片转为 JPEG 字节流."""
    if image.mode != "RGB":
        image = image.convert("RGB")
    buf = BytesIO()
    dpi = image.info.get("dpi", (300, 300))
    image.save(buf, format="JPEG", quality=quality, dpi=dpi)
    return buf.getvalue()


def _image_to_png_bytes(image: Image.Image) -> bytes:
    """将图片转为 PNG 字节流."""
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def convert_format(
    image: Image.Image,
    output_format: str,
    quality: int = 95,
) -> Tuple[bytes, str]:
    """
    将图片转换为指定格式.

    参数:
        image: PIL.Image 输入图片
        output_format: 目标格式（JPEG / PNG / PDF / BMP / WEBP / TIFF）
        quality: JPEG 质量（1-100），仅对 JPEG 和 WEBP 有效

    返回:
        (bytes_data, mime_type)
    """
    fmt = output_format.upper()

    if fmt not in SUPPORTED_OUTPUT_FORMATS:
        logger.warning(f"Unsupported format '{fmt}', falling back to JPEG.")
        fmt = "JPEG"

    if fmt == "JPEG":
        data = _image_to_jpeg_bytes(image, quality)
    elif fmt == "PNG":
        data = _image_to_png_bytes(image)
    elif fmt == "PDF":
        data = _image_to_pdf_bytes(image)
    elif fmt == "WEBP":
        buf = BytesIO()
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(buf, format="WEBP", quality=quality)
        data = buf.getvalue()
    elif fmt == "BMP":
        buf = BytesIO()
        image.save(buf, format="BMP")
        data = buf.getvalue()
    elif fmt == "TIFF":
        buf = BytesIO()
        image.save(buf, format="TIFF")
        data = buf.getvalue()
    else:
        data = _image_to_jpeg_bytes(image, quality)

    mime = SUPPORTED_OUTPUT_FORMATS[fmt]["mime"]
    size_kb = len(data) / 1024
    logger.info(f"Format conversion: {image.size} -> {fmt} ({size_kb:.1f}KB)")

    return data, mime


def get_format_info(output_format: str) -> dict:
    """获取格式信息."""
    return SUPPORTED_OUTPUT_FORMATS.get(
        output_format.upper(),
        {"ext": "jpg", "mime": "image/jpeg", "description": "Unknown format"},
    )