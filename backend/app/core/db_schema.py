"""启动时的幂等结构补齐（老库升级用）.

背景：生产库是**既有库**，SQLAlchemy 的 `create_all` 只会创建缺失的表，
**不会修改已有列**。而照片处理改成 Celery 异步后：
  1. `process_records.status` 是 ENUM('success','failed')，必须扩出 pending/processing；
  2. 需要新增 `original_path` 保存原图暂存路径，供 celery worker 读取。
所以这里用 information_schema 先判断、再 ALTER，重复执行安全（幂等）。

正式的结构变更仍然走 alembic（见 backend/alembic/versions/003_*.py），
这里是保证「用 docker compose 直接部署、不跑 alembic」的线上也能自愈。
"""

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_PROCESS_STATUS_VALUES = ("pending", "processing", "success", "failed")

_COLUMN_QUERY = text(
    "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
)
_COLUMN_COUNT_QUERY = text(
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
)


async def ensure_process_record_schema(conn: AsyncConnection) -> None:
    """补齐 process_records 的异步任务所需结构（仅 MySQL，幂等）."""
    if conn.dialect.name != "mysql":
        return

    status_type = (
        await conn.execute(_COLUMN_QUERY, {"t": "process_records", "c": "status"})
    ).scalar()

    # 表还不存在（全新库由 create_all 建，此处直接跳过）
    if status_type is None:
        return

    if "pending" not in str(status_type):
        enum_values = ",".join(f"'{v}'" for v in _PROCESS_STATUS_VALUES)
        await conn.execute(
            text(
                "ALTER TABLE process_records MODIFY COLUMN status "
                f"ENUM({enum_values}) NOT NULL DEFAULT 'pending'"
            )
        )
        logger.info(
            f"process_records.status 已扩展为 {list(_PROCESS_STATUS_VALUES)}（原类型 {status_type}）"
        )

    has_original_path = (
        await conn.execute(
            _COLUMN_COUNT_QUERY, {"t": "process_records", "c": "original_path"}
        )
    ).scalar()

    if not has_original_path:
        await conn.execute(
            text(
                "ALTER TABLE process_records ADD COLUMN original_path VARCHAR(512) NULL "
                "COMMENT '原图暂存路径（celery 任务读取，处理完删除）' AFTER thumb_path"
            )
        )
        logger.info("process_records.original_path 列已补齐")
