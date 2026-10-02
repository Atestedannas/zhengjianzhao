"""图片格式校验模块 — 校验文件魔数（magic bytes）."""

from io import BytesIO
from typing import Tuple

from PIL import Image
from loguru import logger

from app.config import settings
from app.utils.error_codes import ErrorCode

# 允许的图片格式魔数（前几个字节）
MAGIC_SIGNATURES = {
    b"\xFF\xD8\xFF": "JPEG",        # JPEG
    b"\x89PNG\r\n\x1A\n": "PNG",    # PNG
    b"BM": "BMP",                   # BMP (备用)
    b"RIFF": "WEBP",                # WebP
    b"ftyp": "HEIC",                # HEIC (ftyp 在偏移 4)
    b"\x00\x00\x00\x0Cftyp": "HEIC",  # HEIC 大端
}

# HEIC 转 PNG 的尝试
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORT = True
except ImportError:
    HEIC_SUPPORT = False
    logger.warning("pillow_heif not installed, HEIC support limited.")


def _check_magic_bytes(data: bytes) -> str:
    """检测文件魔数, 返回格式字符串."""
    for magic, fmt in MAGIC_SIGNATURES.items():
        if data[: len(magic)] == magic:
            return fmt
    # HEIC 特殊检测 (ftyp at offset 4)
    if len(data) > 8 and data[4:8] == b"ftyp":
        return "HEIC"
    raise ValueError("Unsupported image format")


def validate_image(file_bytes: bytes, max_size_mb: float = None) -> Tuple[Image.Image, str]:
    """
    校验上传图片:
    1. 文件大小检查
    2. 魔数校验（仅允许 JPEG/PNG/HEIC/BMP/WEBP）
    3. 用 PIL 打开验证
    返回 (PIL.Image, format_str)
    """
    if max_size_mb is None:
        max_size_mb = settings.UPLOAD_MAX_MB

    max_size_bytes = max_size_mb * 1024 * 1024

    if len(file_bytes) > max_size_bytes:
        raise ValueError(f"File size {len(file_bytes) / 1024 / 1024:.1f}MB exceeds limit {max_size_mb}MB")

    try:
        fmt = _check_magic_bytes(file_bytes)
    except ValueError as e:
        raise ValueError(f"Unsupported image format: {e}")

    try:
        image = Image.open(BytesIO(file_bytes))
        image.load()
    except Exception as e:
        raise ValueError(f"Invalid or corrupted image file: {e}")

    # 统一转 RGB 模式（去掉 RGBA / P / CMYK 等）
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")

    logger.debug(f"Image validated: {image.size} {image.mode} fmt={fmt} size={len(file_bytes)}B")
    return image, fmt
