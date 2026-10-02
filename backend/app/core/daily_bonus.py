"""每日免费次数的真正实现（后台「价格策略 → 每日免费」）.

后台的 daily_bonus_enabled / daily_bonus_count 以前只被读写、没有任何消费者，
所以开关打开、数量填 100 也不会有人拿到次数。这里补上发放逻辑：

- 幂等：daily_bonus_logs 表 (user_id, claim_date) 唯一，
  多 worker 并发下也只有一条 INSERT 能成功，不依赖 Redis，也不会重复发；
- 触发：带 token 的请求进入 get_current_user 时自动补发（每天首次即发），
  另外保留 POST /api/v1/user/daily-bonus/claim 供前端显式「领取」；
- 「一天」按业务时区（DAILY_BONUS_TIMEZONE，默认 Asia/Shanghai）划分；
- 无限次数用户（free_count = -1）不再发放，避免把 -1 加成正数。
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.billing import add_free_count
from app.core.pricing_config import business_today, get_daily_bonus_config
from app.models.daily_bonus import DailyBonusLog
from app.models.user import User


def evaluate_daily_bonus(
    *,
    enabled: bool,
    count: int,
    free_count: int,
    last_claim_date: Optional[date],
    today: date,
) -> tuple:
    """是否应该发放今天的免费次数（纯函数，便于单测）.

    返回 (是否发放, 原因)，原因取值：disabled / count_zero / unlimited /
    already_claimed / granted。
    """
    if not enabled:
        return False, "disabled"
    if count <= 0:
        return False, "count_zero"
    if free_count == -1:
        # 无限次数用户不需要每日赠送
        return False, "unlimited"
    if last_claim_date is not None and last_claim_date >= today:
        return False, "already_claimed"
    return True, "granted"


def _no_grant(today: date, reason: str, free_count: int = 0) -> dict:
    """统一构造「未发放」返回值."""
    return {
        "granted": False,
        "reason": reason,
        "count": 0,
        "free_count": free_count,
        "claim_date": today.isoformat(),
    }


async def get_last_claim_date(db: AsyncSession, user_id: int) -> Optional[date]:
    """最近一次发放日期（没有则 None）."""
    result = await db.execute(
        select(DailyBonusLog.claim_date)
        .where(DailyBonusLog.user_id == user_id)
        .order_by(DailyBonusLog.claim_date.desc())
        .limit(1)
    )
    return result.scalar()


async def grant_daily_bonus(db: AsyncSession, user_id: int) -> dict:
    """按需发放当日免费次数（幂等，不 commit，由调用方决定事务边界）.

    返回 {granted, reason, count, free_count, claim_date}。
    """
    today = business_today()
    enabled, count = await get_daily_bonus_config(db)

    # 快速短路：开关关闭 / 数量为 0 时一次数据库查询都不做。
    # 配置读有 30 秒进程内缓存，所以带 token 的每个请求都能承受这个判断。
    if not enabled:
        return _no_grant(today, "disabled")
    if count <= 0:
        return _no_grant(today, "count_zero")

    result = await db.execute(
        select(User.free_count, User.free_count_total).where(
            User.id == user_id,
            # 被禁用的账号不发（否则禁用用户每天还在涨次数）
            User.is_active == 1,
        )
    )
    row = result.first()
    if row is None:
        return _no_grant(today, "user_unavailable")

    free_count, free_count_total = row
    last_claim_date = await get_last_claim_date(db, user_id)
    should_grant, reason = evaluate_daily_bonus(
        enabled=enabled,
        count=count,
        free_count=free_count,
        last_claim_date=last_claim_date,
        today=today,
    )

    if not should_grant:
        return _no_grant(today, reason, free_count)

    # 唯一键兜底：并发请求里只有一个能插入 (user_id, today)，其余走 already_claimed
    savepoint = await db.begin_nested()
    try:
        db.add(DailyBonusLog(user_id=user_id, claim_date=today, bonus_count=count))
        await db.flush()
        await savepoint.commit()
    except IntegrityError:
        await savepoint.rollback()
        logger.info(f"User {user_id}: 今日免费次数已由并发请求发放，跳过")
        return _no_grant(today, "already_claimed", free_count)

    remaining = await add_free_count(db, user_id, count)
    logger.info(f"User {user_id}: 每日免费发放 {count} 次，剩余 {remaining}")

    return {
        "granted": True,
        "reason": "granted",
        "count": count,
        "free_count": remaining,
        "free_count_total": (free_count_total or 0) + count,
        "claim_date": today.isoformat(),
    }


async def grant_daily_bonus_on_request(db: AsyncSession, user_id: int) -> dict:
    """请求入口调用（get_current_user）：需要时才发放，出问题不影响本次请求.

    发放成功后立即 commit，一是尽快落库，二是结束本次请求的读事务，
    否则在 MySQL REPEATABLE READ 下，随后加载的 User 可能仍读到发放前的旧次数。
    """
    try:
        result = await grant_daily_bonus(db, user_id)
    except Exception as exc:  # 发放失败绝不能让正常接口 500
        logger.warning(f"每日免费发放失败（已忽略，不影响本次请求）: {exc}")
        try:
            await db.rollback()
        except Exception:
            pass
        return {"granted": False, "reason": "error", "count": 0}

    if result.get("granted"):
        await db.commit()
    return result


async def daily_bonus_status(db: AsyncSession, user_id: int) -> dict:
    """今日每日免费状态（供 /user/daily-bonus 与手动领取接口返回）."""
    enabled, count = await get_daily_bonus_config(db)
    today = business_today()
    last_claim_date = await get_last_claim_date(db, user_id)

    result = await db.execute(select(User.free_count).where(User.id == user_id))
    free_count = result.scalar()
    if free_count is None:
        free_count = 0

    return {
        "enabled": enabled,
        "count": count,
        "claim_date": today.isoformat(),
        "claimed_today": last_claim_date is not None and last_claim_date >= today,
        "free_count": free_count,
    }
