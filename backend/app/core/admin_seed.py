"""初始化管理员账户 — 在应用启动时自动创建默认管理员.

安全说明（重要）：
    历史实现是「每次启动都把管理员密码重置为代码里的默认值 admin_test」，
    这意味着 (1) 改过的密码会被悄悄覆盖回默认值，(2) 只要知道仓库内容
    就能登录任何一套部署的后台。现在改为：

    - 默认行为：只在管理员不存在时创建，已存在则不动密码；
    - 初始密码取 .env 的 ADMIN_INIT_PASSWORD（为空才用代码默认值）；
    - 确实需要强制重置（例如忘了密码）时，显式设置
      ADMIN_RESET_PASSWORD_ON_BOOT=true 再重启。
"""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin import AdminUser
from app.core.security import hash_password, verify_password


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin_test"


def _initial_password() -> str:
    """管理员初始密码：.env 的 ADMIN_INIT_PASSWORD 优先，否则代码默认值."""
    return (getattr(settings, "ADMIN_INIT_PASSWORD", "") or "").strip() or DEFAULT_ADMIN_PASSWORD


def _should_reset_password() -> bool:
    """是否在启动时强制重置密码（默认关闭）."""
    return bool(getattr(settings, "ADMIN_RESET_PASSWORD_ON_BOOT", False))


async def init_admin_user(db: AsyncSession) -> None:
    """确保默认管理员账户存在（默认不覆盖已有密码）."""
    result = await db.execute(
        select(AdminUser).where(AdminUser.username == DEFAULT_ADMIN_USERNAME)
    )
    existing = result.scalar()

    if existing is None:
        password = _initial_password()
        admin = AdminUser(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password(password),
            role="super_admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        logger.info(f"Default admin user created: username={DEFAULT_ADMIN_USERNAME}")
        if password == DEFAULT_ADMIN_PASSWORD:
            logger.warning(
                "管理员密码仍是代码里的默认值 admin_test！请在 .env 设置 "
                "ADMIN_INIT_PASSWORD=<强密码> 并配合 ADMIN_RESET_PASSWORD_ON_BOOT=true 重启一次"
            )
        return

    if _should_reset_password():
        existing.password_hash = hash_password(_initial_password())
        await db.commit()
        logger.warning(
            f"Admin '{DEFAULT_ADMIN_USERNAME}' 密码已按 ADMIN_RESET_PASSWORD_ON_BOOT 强制重置；"
            "重置完请把该开关改回 false"
        )
        return

    # 默认密码仍然可用时给一条启动告警，方便发现没改密码的部署
    try:
        if verify_password(DEFAULT_ADMIN_PASSWORD, existing.password_hash):
            logger.warning(
                f"Admin '{DEFAULT_ADMIN_USERNAME}' 仍在使用默认密码 admin_test，请尽快修改"
            )
    except Exception:  # 哈希格式异常时不影响启动
        pass
