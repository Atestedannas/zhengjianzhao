"""照片处理执行体 —— 由 Celery worker 调用（web worker 不再加载大模型）.

设计要点：
1. API 只做「校验 + 扣费 + 建记录 + 入队」，重活全部在 worker 里跑，
   web worker 常驻内存里不会出现几百 MB~3GB 的模型，内存问题根除；
2. worker 是同步上下文，用 `asyncio.run` 包一层；
3. **每个任务创建独立 engine，用完 dispose** —— celery prefork 下每个任务的
   `asyncio.run` 都是新的事件循环，复用全局异步 engine 的连接池会报
   「连接绑定在其它事件循环」；
4. 失败要**退还扣掉的免费次数**（扣费发生在接口里，不退还就是白扣）。
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core.billing import refund_free_count
from app.core.image_engine.pipeline import ProcessParams, process_image
from app.core.image_engine.validator import validate_image
from app.models.process_record import ProcessRecord
from app.utils.file_utils import generate_unique_filename, safe_delete_temp_file

# 只从 JSON 里恢复 ProcessParams 认识的字段，避免历史脏数据带进未知 key
_PARAM_FIELDS = set(ProcessParams.__dataclass_fields__.keys())


def _create_engine():
    """任务专用的短生命周期 engine.

    不开 pool_pre_ping：engine 只在单次任务内存活，有 pool_recycle 就够；
    而且 aiomysql 方言下 pool_pre_ping 会踩到
    「AsyncAdapt_aiomysql_connection.ping() missing 1 required positional argument」
    这个已知不兼容（asyncmy 没这个问题，但没必要冒风险）。
    """
    return create_async_engine(
        settings.DATABASE_URL_FINAL,
        echo=False,
        pool_size=2,
        max_overflow=1,
        pool_recycle=1800,
    )


def load_params(raw) -> ProcessParams:
    """从记录的 request_params 快照恢复处理参数."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("request_params 不是合法 JSON，按默认参数处理")
            raw = {}
    data = {k: v for k, v in (raw or {}).items() if k in _PARAM_FIELDS}
    if data.get("gender") in ("None", "", None):
        data["gender"] = None
    return ProcessParams(**data)


def _save_result(result) -> str:
    """把处理结果写到共享的临时目录，返回绝对路径."""
    ext = (result.output_format or "JPEG").lower()
    if ext == "jpeg":
        ext = "jpg"
    os.makedirs(settings.TEMP_FILE_DIR, exist_ok=True)
    path = os.path.join(settings.TEMP_FILE_DIR, generate_unique_filename(ext))
    with open(path, "wb") as f:
        f.write(result.data)
    return path


async def run_process_record(record_id: int) -> dict:
    """执行一条处理记录：读原图 → 跑流水线 → 写回结果（失败退次数并记录原因）."""
    engine = _create_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    original_path = None
    try:
        # ---------- 第一阶段：标记 processing 并把要用的数据取出来 ----------
        async with session_factory() as db:
            record = await db.get(ProcessRecord, record_id)
            if record is None:
                logger.warning(f"ProcessRecord {record_id} 不存在，跳过")
                return {"record_id": record_id, "status": "missing"}

            # 幂等：重复投递（broker 重投 / 兜底任务已判失败）时不再执行。
            # success=已经出过图；failed=兜底任务已判失败并退了次数，
            # 再跑一次会出现「又退次数又出图」，所以两种终态都跳过。
            if record.status in ("success", "failed"):
                logger.info(f"ProcessRecord {record_id} 已是终态 {record.status}，跳过重复执行")
                return {"record_id": record_id, "status": record.status}

            original_path = record.original_path
            params = load_params(record.request_params)
            user_id = record.user_id
            is_paid = bool(record.is_paid)

            record.status = "processing"
            record.error_message = None
            await db.commit()

        # ---------- 第二阶段：跑流水线（不占着数据库连接） ----------
        result = None
        temp_path = None
        result_meta = None
        error = None
        try:
            if not original_path or not os.path.exists(original_path):
                raise FileNotFoundError(f"原图暂存文件不存在: {original_path}")

            with open(original_path, "rb") as f:
                file_bytes = f.read()

            image, _fmt = validate_image(file_bytes)
            # 同步 CPU 重活丢到线程里跑，别卡住事件循环（worker 也需要能响应信号）
            result = await asyncio.to_thread(process_image, image, params)
            temp_path = _save_result(result)
            # 出图质量提示（warnings / 人脸数 / 实际格式）没有独立的列，
            # 统一塞进 request_params 快照的 _result 键，供 /status 接口回给前端
            result_meta = {
                "warnings": list(result.warnings or []),
                "faces_detected": result.faces_detected,
                "output_format": result.output_format,
                "mime_type": result.mime_type,
                "file_size_kb": round(result.result_size_kb, 1),
            }
            logger.info(
                f"ProcessRecord {record_id} 处理完成: {result.result_pixels} "
                f"{result.result_size_kb:.1f}KB in {result.processing_time_ms}ms"
            )
        except Exception as e:  # noqa: BLE001 —— 任何失败都要落库 + 退次数
            error = e
            logger.exception(f"ProcessRecord {record_id} 处理失败: {e}")

        # ---------- 第三阶段：写回结果 / 标记失败并退次数 ----------
        async with session_factory() as db:
            record = await db.get(ProcessRecord, record_id)
            if record is None:
                return {"record_id": record_id, "status": "missing"}

            if error is None:
                if record.status not in ("pending", "processing"):
                    # 已被兜底任务（reap_stuck_records）判为失败并退还了次数：
                    # 这里不再把结果写回去，避免「既退次数又白送一张图」。
                    logger.warning(
                        f"ProcessRecord {record_id} 当前状态={record.status}（已被兜底任务处理），"
                        f"丢弃本次结果 {temp_path}"
                    )
                    if temp_path:
                        safe_delete_temp_file(temp_path)
                    status = record.status
                else:
                    record.status = "success"
                    record.result_size = len(result.data)
                    record.result_pixels = result.result_pixels
                    record.result_dpi = result.result_dpi
                    record.processing_time_ms = result.processing_time_ms
                    record.thumb_path = temp_path
                    record.bg_color = params.bg_color
                    record.error_message = None
                    if result_meta:
                        raw = record.request_params if isinstance(record.request_params, dict) else {}
                        # JSON 列必须整体重新赋值才会被 SQLAlchemy 标记为变更
                        record.request_params = {**raw, "_result": result_meta}
                    await db.commit()
                    status = "success"
            else:
                record.status = "failed"
                record.result_size = 0
                record.processing_time_ms = 0
                record.error_message = str(error)[:500]
                await db.commit()

                if user_id and not is_paid:
                    # 处理失败不能白扣用户次数
                    remaining = await refund_free_count(db, user_id)
                    await db.commit()
                    logger.info(f"ProcessRecord {record_id} 失败，已退还 1 次，剩余={remaining}")
                status = "failed"

        # 原图暂存文件用完即删（成功/失败都删，避免磁盘堆积）
        if original_path:
            safe_delete_temp_file(original_path)

        return {
            "record_id": record_id,
            "status": status,
            "error": None if error is None else str(error)[:200],
        }
    finally:
        await engine.dispose()


def run_process_record_sync(record_id: int) -> dict:
    """同步入口（celery 任务是同步函数）."""
    return asyncio.run(run_process_record(record_id))


async def reap_stuck_records_async() -> dict:
    """把卡在 pending/processing 太久（worker 被杀 / 容器重启）的记录判失败并退还次数.

    这是 acks_late 重投之外的**确定性**兜底：worker 被内核 OOM 杀掉时
    任务会凭空消失，若没有这一步，记录会永远停在 processing，前端一直转圈。
    """
    minutes = int(getattr(settings, "PROCESS_STUCK_MINUTES", 15) or 15)
    # 库里 created_at 是 naive UTC（模型默认 datetime.utcnow），这里保持一致
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)

    engine = _create_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    reaped = 0
    try:
        async with session_factory() as db:
            rows = (
                await db.execute(
                    select(ProcessRecord)
                    .where(
                        ProcessRecord.status.in_(("pending", "processing")),
                        ProcessRecord.created_at < cutoff,
                    )
                    .limit(200)
                )
            ).scalars().all()

            for record in rows:
                record.status = "failed"
                record.result_size = 0
                record.processing_time_ms = 0
                record.error_message = (
                    f"任务超过 {minutes} 分钟未完成（worker 可能被重启或内存不足杀掉）"
                )[:500]
                await db.flush()
                if record.user_id and not bool(record.is_paid):
                    await refund_free_count(db, record.user_id)
                reaped += 1

            if reaped:
                await db.commit()
                logger.warning(f"兜底清理：{reaped} 条卡住的处理记录已判失败并退还次数")
        return {"reaped": reaped}
    finally:
        await engine.dispose()
