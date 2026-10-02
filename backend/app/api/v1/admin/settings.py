"""管理后台 — 系统配置."""

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin, require_super_admin
from app.models.system_config import SystemConfig
from app.schemas.admin import SettingsUpdateRequest
from app.schemas.common import ResponseModel

router = APIRouter()

SETTING_KEYS = [
    "upload_max_mb", "temp_file_ttl_minutes",
    "rate_limit_per_minute", "maintenance_mode",
]


@router.get("", response_model=ResponseModel)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """获取系统配置."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.config_key.in_(SETTING_KEYS))
    )
    configs = result.scalars().all()

    data = {}
    for c in configs:
        if c.config_type == "int":
            data[c.config_key] = int(c.config_value)
        elif c.config_type == "bool":
            data[c.config_key] = True if c.config_value in ("1", "true", "True") else False
        else:
            data[c.config_key] = c.config_value

    defaults = {
        "upload_max_mb": 10,
        "temp_file_ttl_minutes": 5,
        "rate_limit_per_minute": 60,
        "maintenance_mode": False,
    }
    for k, v in defaults.items():
        if k not in data:
            data[k] = v

    return ResponseModel(code=200, data=data)


@router.put("", response_model=ResponseModel)
async def update_settings(
    payload: SettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """更新系统配置."""
    updates = payload.model_dump(exclude_unset=True)

    if not updates:
        return ResponseModel(code=400, message="无更新参数")

    for key, val in updates.items():
        config_type = "bool" if key == "maintenance_mode" else "int"
        str_val = "1" if key == "maintenance_mode" and val else str(val)

        result = await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == key)
        )
        config = result.scalar()
        if config:
            config.config_value = str_val
        else:
            db.add(SystemConfig(config_key=key, config_value=str_val, config_type=config_type))

    await db.commit()
    logger.info(f"Admin {admin.id} updated settings: {updates}")
    return ResponseModel(code=200, message="系统配置已更新")
