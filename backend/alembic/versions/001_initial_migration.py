"""Initial migration: create all tables

Revision ID: 001_initial
Revises: None
Create Date: 2026-08-15

该迁移脚本创建项目的所有基础表：
- users（用户表）
- spec_templates（规格模板表）
- process_records（处理记录表）
- orders（订单表）
- admin_users（管理员表）
- free_count_config（免费次数配置表）
- system_config（系统配置表）
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建所有表."""

    # ======================== users ========================
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='用户ID'),
        sa.Column('openid', sa.String(128), nullable=False, comment='微信/支付宝 openid'),
        sa.Column('unionid', sa.String(64), nullable=True, comment='微信 unionid（跨应用）'),
        sa.Column('platform', sa.Enum('wechat_mini', 'wechat_web', 'alipay_web', name='user_platform_enum'), nullable=False, comment='注册来源平台'),
        sa.Column('nickname', sa.String(128), nullable=False, server_default='', comment='昵称'),
        sa.Column('avatar_url', sa.String(512), nullable=False, server_default='', comment='头像URL'),
        sa.Column('free_count', sa.Integer(), nullable=False, server_default='0', comment='剩余免费次数'),
        sa.Column('free_count_total', sa.Integer(), nullable=False, server_default='0', comment='累计获得免费次数'),
        sa.Column('balance', sa.DECIMAL(10, 2), nullable=False, server_default='0.00', comment='账户余额'),
        sa.Column('total_spent', sa.DECIMAL(10, 2), nullable=False, server_default='0.00', comment='累计消费金额'),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1', comment='是否启用（0=禁用）'),
        sa.Column('last_login_at', sa.DateTime(), nullable=True, comment='最近登录时间'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('openid', 'platform', name='uk_openid_platform'),
    )
    op.create_index('ix_users_openid', 'users', ['openid'])
    op.create_index('ix_users_platform', 'users', ['platform'])
    op.create_index('ix_users_created_at', 'users', ['created_at'])

    # ======================== spec_templates ========================
    op.create_table(
        'spec_templates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='模板ID'),
        sa.Column('name', sa.String(128), nullable=False, comment='模板名称'),
        sa.Column('width_px', sa.Integer(), nullable=False, comment='目标宽度（像素）'),
        sa.Column('height_px', sa.Integer(), nullable=False, comment='目标高度（像素）'),
        sa.Column('dpi', sa.Integer(), nullable=False, server_default='350', comment='目标DPI'),
        sa.Column('min_kb', sa.Integer(), nullable=False, comment='最小文件大小（KB）'),
        sa.Column('max_kb', sa.Integer(), nullable=False, comment='最大文件大小（KB）'),
        sa.Column('allowed_bg_colors', sa.JSON(), nullable=False, comment='可选背景色列表'),
        sa.Column('output_format', sa.String(8), nullable=False, server_default='JPEG', comment='输出格式'),
        sa.Column('physical_size_mm', sa.String(32), nullable=True, comment='物理尺寸描述'),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1', comment='是否启用'),
        sa.Column('remark', sa.Text(), nullable=True, comment='备注/公告原文链接'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_spec_templates_is_active', 'spec_templates', ['is_active'])

    # ======================== process_records ========================
    op.create_table(
        'process_records',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='记录ID'),
        sa.Column('user_id', sa.BigInteger(), nullable=True, comment='用户ID'),
        sa.Column('template_id', sa.Integer(), nullable=True, comment='模板ID'),
        sa.Column('request_params', sa.JSON(), nullable=False, comment='处理参数快照'),
        sa.Column('is_paid', sa.Integer(), nullable=False, server_default='0', comment='是否付费'),
        sa.Column('paid_amount', sa.DECIMAL(10, 2), nullable=True, comment='实付金额'),
        sa.Column('payment_trade_no', sa.String(64), nullable=True, comment='支付单号'),
        sa.Column('original_size', sa.Integer(), nullable=False, comment='原始文件大小（Byte）'),
        sa.Column('result_size', sa.Integer(), nullable=False, comment='处理后文件大小（Byte）'),
        sa.Column('result_pixels', sa.String(16), nullable=True, comment='输出像素'),
        sa.Column('result_dpi', sa.Integer(), nullable=True, comment='输出DPI'),
        sa.Column('bg_color', sa.String(16), nullable=True, comment='使用的背景色'),
        sa.Column('processing_time_ms', sa.Integer(), nullable=False, comment='处理耗时（毫秒）'),
        sa.Column('status', sa.Enum('success', 'failed', name='process_status_enum'), nullable=False, server_default='success', comment='处理状态'),
        sa.Column('error_message', sa.String(512), nullable=True, comment='错误信息'),
        sa.Column('thumb_path', sa.String(512), nullable=True, comment='缩略图路径'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['template_id'], ['spec_templates.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_process_records_user_id', 'process_records', ['user_id'])
    op.create_index('ix_process_records_template_id', 'process_records', ['template_id'])
    op.create_index('ix_process_records_created_at', 'process_records', ['created_at'])
    op.create_index('ix_process_records_status', 'process_records', ['status'])

    # ======================== orders ========================
    op.create_table(
        'orders',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='订单ID'),
        sa.Column('order_no', sa.String(32), nullable=False, comment='内部订单号'),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='用户ID'),
        sa.Column('amount', sa.DECIMAL(10, 2), nullable=False, comment='订单金额（元）'),
        sa.Column('pay_method', sa.Enum('wechat_jsapi', 'wechat_native', 'alipay', name='pay_method_enum'), nullable=True, comment='支付方式'),
        sa.Column('trade_no', sa.String(64), nullable=True, comment='支付平台交易号'),
        sa.Column('status', sa.Enum('pending', 'paid', 'refunded', 'closed', name='order_status_enum'), nullable=False, server_default='pending', comment='订单状态'),
        sa.Column('paid_at', sa.DateTime(), nullable=True, comment='支付成功时间'),
        sa.Column('refund_amount', sa.DECIMAL(10, 2), nullable=True, comment='退款金额'),
        sa.Column('refund_at', sa.DateTime(), nullable=True, comment='退款时间'),
        sa.Column('refund_trade_no', sa.String(64), nullable=True, comment='退款交易号'),
        sa.Column('expire_at', sa.DateTime(), nullable=False, comment='订单过期时间'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_no', name='uk_order_no'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )
    op.create_index('ix_orders_user_id', 'orders', ['user_id'])
    op.create_index('ix_orders_status', 'orders', ['status'])
    op.create_index('ix_orders_created_at', 'orders', ['created_at'])

    # ======================== admin_users ========================
    op.create_table(
        'admin_users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='管理员ID'),
        sa.Column('username', sa.String(64), nullable=False, comment='管理员用户名'),
        sa.Column('password_hash', sa.String(256), nullable=False, comment='bcrypt密码哈希'),
        sa.Column('role', sa.String(32), nullable=False, server_default='normal_admin', comment='角色'),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1', comment='是否启用'),
        sa.Column('last_login_at', sa.DateTime(), nullable=True, comment='最近登录时间'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username', name='uk_admin_username'),
    )

    # ======================== free_count_config ========================
    op.create_table(
        'free_count_config',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('config_key', sa.String(32), nullable=False, comment='配置键'),
        sa.Column('config_value', sa.String(128), nullable=False, comment='配置值'),
        sa.Column('description', sa.String(256), nullable=True, comment='说明'),
        sa.Column('updated_at', sa.String(32), nullable=True, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('config_key', name='uk_free_count_config_key'),
    )

    # ======================== system_config ========================
    op.create_table(
        'system_config',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('config_key', sa.String(64), nullable=False, comment='配置键'),
        sa.Column('config_value', sa.Text(), nullable=False, comment='配置值'),
        sa.Column('config_type', sa.Enum('string', 'int', 'json', 'bool', name='config_type_enum'), nullable=False, server_default='string'),
        sa.Column('description', sa.String(256), nullable=True, comment='说明'),
        sa.Column('updated_at', sa.String(32), nullable=True, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('config_key', name='uk_system_config_key'),
    )

    # ======================== 插入默认数据 ========================
    # 默认免费次数配置（键名必须与 app/core/pricing_config.py 的 PRICING_DEFAULTS 一致）
    # 历史 bug：这里原本种的是 register_gift / daily_gift / daily_gift_enabled，
    # 而代码读的是 register_bonus / daily_bonus_enabled / daily_bonus_count，
    # 导致后台「每日免费」开关永远是摆设。老库由 002 迁移 + 启动时的
    # ensure_pricing_config() 补齐正确键。
    op.execute("""
        INSERT INTO free_count_config (config_key, config_value, description, updated_at) VALUES
        ('unit_price', '0.99', '单次处理价格（元）', NOW()),
        ('register_bonus', '3', '新用户注册赠送次数', NOW()),
        ('daily_bonus_enabled', '0', '每日免费次数开关（1=开启）', NOW()),
        ('daily_bonus_count', '1', '每日赠送数量', NOW())
        ON DUPLICATE KEY UPDATE updated_at = NOW()
    """)

    # 默认系统配置
    op.execute("""
        INSERT INTO system_config (config_key, config_value, config_type, description, updated_at) VALUES
        ('upload_max_mb', '10', 'int', '最大上传文件大小（MB）', NOW()),
        ('temp_file_ttl_minutes', '5', 'int', '临时文件过期时间（分钟）', NOW()),
        ('rate_limit_per_minute', '60', 'int', '单IP每分钟API请求上限', NOW()),
        ('maintenance_mode', 'false', 'bool', '维护模式开关', NOW())
        ON DUPLICATE KEY UPDATE updated_at = NOW()
    """)


def downgrade() -> None:
    """删除所有表（按依赖逆序）."""
    op.drop_table('system_config')
    op.drop_table('free_count_config')
    op.drop_table('orders')
    op.drop_table('process_records')
    op.drop_table('admin_users')
    op.drop_table('spec_templates')
    op.drop_table('users')

    # 删除自定义枚举类型
    op.execute('DROP TYPE IF EXISTS user_platform_enum')
    op.execute('DROP TYPE IF EXISTS process_status_enum')
    op.execute('DROP TYPE IF EXISTS pay_method_enum')
    op.execute('DROP TYPE IF EXISTS order_status_enum')
    op.execute('DROP TYPE IF EXISTS config_type_enum')