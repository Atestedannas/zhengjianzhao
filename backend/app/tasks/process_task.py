"""照片处理相关的 Celery 任务.

分工：
- `process_photo`：真正跑图像流水线（重活、大模型都在这里，web worker 不碰）；
- `reap_stuck_records`：兜底清理卡死的记录（worker 被 OOM 杀掉 / 容器重启时，
  任务会凭空消失，记录会永远停在 processing，前端就会一直转圈）。
"""

import asyncio

from celery.exceptions import SoftTimeLimitExceeded
from loguru import logger

from app.config import settings
from app.tasks.celery_app import celery_app


@celery_app.task(
    name="app.tasks.process.process_photo",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def process_photo(self, record_id: int):
    """异步执行一条照片处理记录.

    注意：`run_process_record` 内部已经把「处理失败」落库并退还次数了，
    能冒到这里的都是基础设施异常（数据库不可用、worker 被杀前的中断等），
    这类才值得重试。
    """
    from app.core.process_runner import run_process_record_sync

    try:
        return run_process_record_sync(record_id)
    except SoftTimeLimitExceeded:
        # 软超时说明这张图已经跑了 task_soft_time_limit 秒（异常），重试只会再烧一遍 CPU。
        # 此时 run_process_record 已经把它判失败并退还次数，直接放弃。
        logger.error(f"process_photo({record_id}) 软超时，已判失败并退还次数，不再重试")
        raise
    except Exception as e:  # noqa: BLE001
        configured = int(getattr(settings, "PROCESS_TASK_MAX_RETRIES", 1) or 0)
        if self.request.retries < min(configured, int(self.max_retries or 0)):
            logger.warning(f"process_photo({record_id}) 异常，准备第 {self.request.retries + 1} 次重试: {e}")
            raise self.retry(exc=e)
        logger.exception(f"process_photo({record_id}) 最终失败: {e}")
        raise


@celery_app.task(name="app.tasks.process.reap_stuck_records")
def reap_stuck_records() -> dict:
    """把长时间卡在 pending/processing 的记录判为失败并退还次数（定时兜底）."""
    from app.core.process_runner import reap_stuck_records_async

    return asyncio.run(reap_stuck_records_async())
