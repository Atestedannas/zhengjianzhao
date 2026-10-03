"""Celery 应用配置."""

from celery import Celery
from app.config import settings

celery_app = Celery(
    "photo_service",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.cleanup", "app.tasks.process_task"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 一个任务被取走后 worker 若被内核 OOM 杀掉 / 容器重启，消息会留在 unacked 里，
    # 靠 visibility_timeout 决定多久后重投。默认 1 小时太长，压到 15 分钟
    # （与 PROCESS_STUCK_MINUTES 同量级：兜底任务会先把记录判失败并退还次数，
    #  重投回来的任务看到终态会直接跳过，不会形成毒消息死循环）。
    broker_transport_options={"visibility_timeout": 900},
    # worker 丢消息时不立即重投（避免 OOM 任务被无限重投）
    task_reject_on_worker_lost=False,
    # 单并发 + 长任务：不要预取任务，避免一个 worker 攥着一堆任务不放
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # 单张图正常几十秒。软超时会先抛异常（被 run_process_record 捕获 → 判失败 + 退次数），
    # 硬超时直接杀子进程，避免一个卡死的任务堵死整个队列。
    task_soft_time_limit=240,
    task_time_limit=300,
    beat_schedule={
        "cleanup-temp-files": {
            "task": "app.tasks.cleanup.cleanup_expired_files",
            "schedule": 300.0,  # 每 5 分钟
        },
        # 兜底：worker 被杀时任务会凭空消失，记录会永远停在 processing，
        # 这里定时把超时未完成的记录判失败并退还免费次数。
        "reap-stuck-process-records": {
            "task": "app.tasks.process.reap_stuck_records",
            "schedule": 300.0,  # 每 5 分钟
        },
    },
)
