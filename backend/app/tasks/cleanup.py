"""定时清理过期临时文件."""

import os
import glob
from datetime import datetime, timedelta

from loguru import logger

from app.tasks.celery_app import celery_app
from app.config import settings


@celery_app.task(name="app.tasks.cleanup.cleanup_expired_files")
def cleanup_expired_files():
    """清理超过 TTL 的临时图片文件."""
    temp_dir = settings.TEMP_FILE_DIR
    ttl_minutes = settings.TEMP_FILE_TTL_MINUTES
    cutoff = datetime.now() - timedelta(minutes=ttl_minutes)

    if not os.path.isdir(temp_dir):
        logger.warning(f"临时目录不存在: {temp_dir}")
        return {"removed": 0}

    patterns = ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp"]
    count = 0

    for pattern in patterns:
        for filepath in glob.glob(os.path.join(temp_dir, pattern)):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                if mtime < cutoff:
                    os.remove(filepath)
                    count += 1
            except OSError as e:
                logger.warning(f"无法删除 {filepath}: {e}")

    logger.info(f"Cleanup: removed {count} expired temp files from {temp_dir}")
    return {"removed": count}
