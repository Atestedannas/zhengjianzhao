"""照片处理异步化：process_records.status 扩容 + original_path 列

Revision ID: 003_process_async
Revises: 002_daily_bonus
Create Date: 2026-10-03

背景：
    /api/v1/process/ 从「同步跑完整个图像流水线」改成「立即返回 record_id +
    Celery worker 异步处理」。这带来两处结构变更：
      1. process_records.status 原本是 ENUM('success','failed')，需要
         pending / processing 两个中间态；
      2. 原图要先落在 web 与 worker 共享的目录里，记录里需要保存它的路径
         （original_path），worker 处理完即删除。

    线上是既有库，SQLAlchemy 的 create_all 只建新表、不会改已有列，
    所以这里显式 ALTER。应用启动时的 app/core/db_schema.py 有一份**等价的幂等实现**
    （线上部署不跑 alembic，靠它自愈），两边保持一致。

注意：ENUM 扩容会重建列定义，但不会丢数据（旧值 success/failed 都保留）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "003_process_async"
down_revision: Union[str, None] = "002_daily_bonus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_STATUS = "ENUM('pending','processing','success','failed')"
_OLD_STATUS = "ENUM('success','failed')"


def _column_type(table: str, column: str) -> Union[str, None]:
    result = op.get_bind().execute(
        sa.text(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return result


def upgrade() -> None:
    """status 扩出 pending/processing，并补 original_path."""
    status_type = _column_type("process_records", "status")
    if status_type is None:
        # 表还不存在：由 create_all 按最新 ORM 定义建，无需 ALTER
        return

    if "pending" not in str(status_type):
        op.execute(
            f"ALTER TABLE process_records MODIFY COLUMN status "
            f"{_NEW_STATUS} NOT NULL DEFAULT 'pending'"
        )

    if _column_type("process_records", "original_path") is None:
        op.execute(
            "ALTER TABLE process_records ADD COLUMN original_path VARCHAR(512) NULL "
            "COMMENT '原图暂存路径（celery 任务读取，处理完删除）' AFTER thumb_path"
        )


def downgrade() -> None:
    """删掉 original_path，并把 status 收回 success/failed.

    回收 ENUM 前先把中间态归一到「失败」，避免取值落在旧 ENUM 之外报错。
    """
    if _column_type("process_records", "original_path") is not None:
        op.execute("ALTER TABLE process_records DROP COLUMN original_path")

    status_type = _column_type("process_records", "status")
    if status_type and "pending" in str(status_type):
        op.execute(
            "UPDATE process_records SET status = 'failed' "
            "WHERE status IN ('pending', 'processing')"
        )
        op.execute(
            f"ALTER TABLE process_records MODIFY COLUMN status "
            f"{_OLD_STATUS} NOT NULL DEFAULT 'success'"
        )
