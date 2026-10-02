"""管理后台 — 价格策略配置."""

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin, require_super_admin
from app.core.pricing_config import (
    PRICING_DEFAULTS,
    get_pricing_values,
    invalidate_pricing_cache,
    parse_bool,
    parse_float,
    parse_int,
)
from app.models.system_config import FreeCountConfig
from app.schemas.admin import PricingConfig
from app.schemas.common import ResponseModel

router = APIRouter()

# 允许通过本接口读写的键（键名与默认值的唯一来源见 app/core/pricing_config.py）
PRICING_KEYS = list(PRICING_DEFAULTS.keys())


def _now_str() -> str:
    """free_count_config.updated_at 是 String(32)，统一格式化."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.get("", response_model=ResponseModel)
async def get_pricing(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """获取当前价格策略."""
    values = await get_pricing_values(db)

    return ResponseModel(
        code=200,
        data={
            "unit_price": parse_float(values.get("unit_price"), 0.99),
            "register_bonus": parse_int(values.get("register_bonus"), 3),
            "daily_bonus_enabled": parse_bool(values.get("daily_bonus_enabled"), False),
            "daily_bonus_count": parse_int(values.get("daily_bonus_count"), 1),
        },
    )


@router.put("", response_model=ResponseModel)
async def update_pricing(
    payload: PricingConfig,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """更新价格策略.

    保存后立刻让配置读缓存失效，这样「每日免费」开关/数量下一次请求就生效，
    不会出现「后台开了开关但用户还要等缓存过期」的诡异现象。
    """
    updates = payload.model_dump(exclude_unset=True)
    updates = {k: v for k, v in updates.items() if k in PRICING_KEYS}

    if not updates:
        return ResponseModel(code=400, message="无更新参数")

    for key, val in updates.items():
        if isinstance(val, bool):
            str_val = "1" if val else "0"
        else:
            str_val = str(val)
        result = await db.execute(
            select(FreeCountConfig).where(FreeCountConfig.config_key == key)
        )
        config = result.scalar()
        if config:
            config.config_value = str_val
            config.updated_at = _now_str()
        else:
            db.add(
                FreeCountConfig(
                    config_key=key,
                    config_value=str_val,
                    description=PRICING_DEFAULTS.get(key, ("", ""))[1],
                    updated_at=_now_str(),
                )
            )

    await db.commit()
    invalidate_pricing_cache()

    logger.info(f"Admin {admin.id} updated pricing: {updates}")
    return ResponseModel(code=200, message="价格策略已更新")
