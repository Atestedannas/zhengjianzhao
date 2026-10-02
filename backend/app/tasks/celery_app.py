"""Celery 应用配置."""

from celery import Celery
from app.config import settings

celery_app = Celery(
    "photo_service",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.cleanup"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    beat_schedule={
        "cleanup-temp-files": {
            "task": "app.tasks.cleanup.cleanup_expired_files",
            "schedule": 300.0,  # 每 5 分钟
        },
    },
)
