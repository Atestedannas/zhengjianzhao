"""文件工具函数."""

import os
import uuid
import time
from typing import Optional

from loguru import logger


def generate_unique_filename(extension: str = ".jpg") -> str:
    """生成唯一文件名: {timestamp}_{uuid}.{ext}."""
    ts = int(time.time() * 1000)
    uid = uuid.uuid4().hex[:12]
    ext = extension.lstrip(".").lower()
    return f"{ts}_{uid}.{ext}"


def safe_delete_temp_file(filepath: str) -> bool:
    """安全删除临时文件，忽略不存在或权限错误."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.debug(f"Deleted temp file: {filepath}")
            return True
    except OSError as e:
        logger.warning(f"Failed to delete {filepath}: {e}")
    return False


def format_file_size(bytes_size: int) -> str:
    """格式化文件大小为人类可读字符串."""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    else:
        return f"{bytes_size / (1024 * 1024):.1f} MB"


def ensure_dir(dir_path: str) -> None:
    """确保目录存在."""
    os.makedirs(dir_path, exist_ok=True)


def get_temp_path(filename: str) -> str:
    """获取临时文件完整路径."""
    from app.config import settings

    ensure_dir(settings.TEMP_FILE_DIR)
    return os.path.join(settings.TEMP_FILE_DIR, filename)
