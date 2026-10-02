"""每日免费次数：发放流水表 + 补齐配置键名

Revision ID: 002_daily_bonus
Revises: 001_initial
Create Date: 2026-10-02

背景：
    后台「价格策略 → 每日免费」的 daily_bonus_enabled / daily_bonus_count
    以前只被读写、没有任何消费者；001 迁移里种的键名也是错的
    （register_gift / daily_gift / daily_gift_enabled）。

本迁移做两件事：
1. 建 daily_bonus_logs —— (user_id, claim_date) 唯一，保证每人每天只发一次；
2. 补齐正确的配置键，让老库无需手工插数据。

注意：应用启动时 Base.metadata.create_all 也会建这张表，
所以这里用 inspect 判存在，重复执行不会报 "table already exists"。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "002_daily_bonus"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建发放流水表 + 补齐配置键."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "daily_bonus_logs" not in inspector.get_table_names():
        op.create_table(
            "daily_bonus_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
            sa.Column("user_id", sa.Integer(), nullable=False, comment="用户ID"),
            sa.Column("claim_date", sa.Date(), nullable=False, comment="发放日期（按业务时区计算）"),
            sa.Column(
                "bonus_count", sa.Integer(), nullable=False, server_default="0",
                comment="本次发放的免费次数",
            ),
            sa.Column(
                "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False,
                comment="发放时间",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "claim_date", name="uk_daily_bonus_user_date"),
        )
        op.create_index("ix_daily_bonus_logs_user_id", "daily_bonus_logs", ["user_id"])

    # 补齐正确键（已有的不动；老库里的 register_gift/daily_gift 留着无害，无人读取）
    op.execute("""
        INSERT INTO free_count_config (config_key, config_value, description, updated_at) VALUES
        ('unit_price', '0.99', '单次处理价格（元）', NOW()),
        ('register_bonus', '3', '新用户注册赠送次数', NOW()),
        ('daily_bonus_enabled', '0', '每日免费次数开关（1=开启）', NOW()),
        ('daily_bonus_count', '1', '每日赠送数量', NOW())
        ON DUPLICATE KEY UPDATE config_key = config_key
    """)


def downgrade() -> None:
    """删除发放流水表.

    只删表，不回滚配置键 —— 键名本身是对的，删掉会让线上配置丢失。
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "daily_bonus_logs" in inspector.get_table_names():
        op.drop_table("daily_bonus_logs")
