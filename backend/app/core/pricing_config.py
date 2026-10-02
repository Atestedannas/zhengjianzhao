"""价格 / 免费次数配置的单一来源（free_count_config 表）.

背景（历史 bug）：
    后台「价格策略」保存的 4 个配置项散落在 billing / auth / admin 三处读取，
    而且 alembic 迁移里种的键名是 register_gift / daily_gift / daily_gift_enabled，
    代码读的却是 register_bonus / daily_bonus_enabled / daily_bonus_count —
    连键名都对不上，所以后台「每日免费」开关永远不会生效。

本模块统一：
1. 键名与默认值只有 PRICING_DEFAULTS 这一份；
2. ensure_pricing_config() 在应用启动时补齐缺失的键，老库不需要手工插数据；
3. 读取带 30 秒进程内缓存，避免每个请求都查一次配置表；
4. 提供「业务时区」工具 —— 「每天」按 DAILY_BONUS_TIMEZONE 划分，
   而不是 UTC 零点（否则北京时间的用户会在早上 8 点看到次数重置）。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.system_config import FreeCountConfig

# 键名 → (默认值, 说明)。后台 GET 返回的默认值同样取自这里。
PRICING_DEFAULTS: dict[str, tuple[str, str]] = {
    "unit_price": ("0.99", "单次处理价格（元）"),
    "register_bonus": ("3", "新用户注册赠送次数"),
    "daily_bonus_enabled": ("0", "每日免费次数开关（1=开启）"),
    "daily_bonus_count": ("1", "每日赠送数量"),
}

DEFAULT_UNIT_PRICE = 0.99
DEFAULT_REGISTER_BONUS = 3
DEFAULT_DAILY_BONUS_COUNT = 1

# 配置读缓存 TTL（秒）：后台保存时会主动失效，所以这里大一点也没关系
_CONFIG_CACHE_TTL_SECONDS = 30

_pricing_cache: dict = {"ts": 0.0, "values": {}}
_tz_cache: dict = {}


def _now_str() -> str:
    """free_count_config.updated_at 是 String(32)，统一格式化."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ======================== 类型转换（纯函数，便于测试） ========================
def parse_bool(raw, default: bool = False) -> bool:
    """把配置里的字符串解析成布尔值（1/true/yes/on 都算开）."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "enabled")


def parse_int(raw, default: int = 0) -> int:
    """解析整数，非法值回落默认值；负数一律视为非法（次数不能为负）."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def parse_float(raw, default: float = 0.0) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


# ======================== 读取配置 ========================
async def get_pricing_values(db: AsyncSession, keys: Optional[list] = None) -> dict:
    """读取定价配置原始值（缺失的键用默认值补齐）."""
    wanted = list(keys) if keys else list(PRICING_DEFAULTS.keys())
    result = await db.execute(
        select(FreeCountConfig.config_key, FreeCountConfig.config_value).where(
            FreeCountConfig.config_key.in_(wanted)
        )
    )
    values = {key: value for key, value in result.all() if value is not None}
    for key in wanted:
        if key not in values:
            values[key] = PRICING_DEFAULTS.get(key, ("", ""))[0]
    return values


async def get_pricing_values_cached(
    db: AsyncSession, ttl: int = _CONFIG_CACHE_TTL_SECONDS
) -> dict:
    """带进程内缓存的读取 —— 供每个请求都要判断的场景（如每日免费）使用."""
    now = time.monotonic()
    if _pricing_cache["values"] and now - _pricing_cache["ts"] < ttl:
        return _pricing_cache["values"]
    values = await get_pricing_values(db)
    _pricing_cache["values"] = values
    _pricing_cache["ts"] = now
    return values


def invalidate_pricing_cache() -> None:
    """后台保存配置后调用：下一次读取立刻拿到新值（不用等 TTL）."""
    _pricing_cache["values"] = {}
    _pricing_cache["ts"] = 0.0


async def get_unit_price(db: AsyncSession) -> float:
    """单次处理价格（元）."""
    values = await get_pricing_values_cached(db)
    return parse_float(values.get("unit_price"), DEFAULT_UNIT_PRICE)


async def get_register_bonus(db: AsyncSession) -> int:
    """新用户注册赠送次数（所有登录渠道共用，不再各写各的硬编码）."""
    values = await get_pricing_values_cached(db)
    return parse_int(values.get("register_bonus"), DEFAULT_REGISTER_BONUS)


async def get_daily_bonus_config(db: AsyncSession) -> tuple:
    """返回 (是否开启每日免费, 每日赠送数量)."""
    values = await get_pricing_values_cached(db)
    enabled = parse_bool(values.get("daily_bonus_enabled"), False)
    count = parse_int(values.get("daily_bonus_count"), DEFAULT_DAILY_BONUS_COUNT)
    return enabled, count


async def ensure_pricing_config(db: AsyncSession) -> list:
    """补齐缺失的配置键（启动时调用，幂等）.

    历史库里的键名是错的（register_gift / daily_gift / daily_gift_enabled），
    这里只补正确键，不改动旧数据；旧键没有代码读取，留着无害。
    """
    result = await db.execute(
        select(FreeCountConfig.config_key).where(
            FreeCountConfig.config_key.in_(list(PRICING_DEFAULTS.keys()))
        )
    )
    existing = set(result.scalars().all())

    added = []
    for key, (value, description) in PRICING_DEFAULTS.items():
        if key in existing:
            continue
        db.add(
            FreeCountConfig(
                config_key=key,
                config_value=value,
                description=description,
                updated_at=_now_str(),
            )
        )
        added.append(key)

    if added:
        await db.commit()
        invalidate_pricing_cache()
        logger.info(f"已补齐免费次数默认配置: {', '.join(added)}")
    return added


# ======================== 业务时区 / 「今天」 ========================
def get_business_timezone() -> tzinfo:
    """业务时区 —— 决定「每天」从几点开始算.

    优先用 zoneinfo（镜像里装了 tzdata 就能解析任意时区名）；
    解析失败时回退固定 UTC+8（Asia/Shanghai 自 1991 年起无夏令时，等价）。
    """
    name = (getattr(settings, "DAILY_BONUS_TIMEZONE", "") or "Asia/Shanghai").strip()
    name = name or "Asia/Shanghai"

    cached = _tz_cache.get(name)
    if cached is not None:
        return cached

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(name)
    except Exception as exc:  # 缺 tzdata / 时区名写错
        logger.warning(f"时区 {name} 不可用（{exc}），回退为固定 UTC+8")
        tz = timezone(timedelta(hours=8))

    _tz_cache[name] = tz
    return tz


def business_today(now: Optional[datetime] = None) -> date:
    """按业务时区取「今天」的日期."""
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(get_business_timezone()).date()
