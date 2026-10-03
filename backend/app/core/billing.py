"""计费扣费服务."""

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pricing_config import get_unit_price as _get_unit_price
from app.models.user import User
from app.utils.error_codes import NeedPaymentException


async def get_unit_price(db: AsyncSession) -> float:
    """获取单次处理价格（配置读取统一走 app.core.pricing_config）."""
    return await _get_unit_price(db)


async def get_user_free_count(db: AsyncSession, user_id: int) -> int:
    """查询用户剩余免费次数."""
    result = await db.execute(select(User.free_count).where(User.id == user_id))
    row = result.scalar()
    return row if row is not None else 0


async def decrement_free_count(db: AsyncSession, user_id: int) -> int:
    """扣减一次免费次数（原子操作），返回扣减后的剩余次数."""
    await db.execute(
        update(User)
        .where(User.id == user_id, User.free_count > 0)
        .values(free_count=User.free_count - 1)
    )
    await db.flush()
    result = await db.execute(select(User.free_count).where(User.id == user_id))
    return result.scalar()


async def add_free_count(db: AsyncSession, user_id: int, count: int) -> int:
    """增加用户免费次数."""
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            free_count=User.free_count + count,
            free_count_total=User.free_count_total + count,
        )
    )
    await db.flush()
    result = await db.execute(select(User.free_count).where(User.id == user_id))
    return result.scalar()


async def refund_free_count(db: AsyncSession, user_id: int) -> int:
    """退还一次免费次数（处理失败时调用），返回退还后的剩余次数.

    边界：
    - 无限次数用户（free_count = -1）不退还，直接返回 -1；
    - 其余用户 +1（调用方必须先确认这次确实是「扣了免费次数」的，
      即 ProcessRecord.is_paid == False）。
    只退还次数本身，不动 free_count_total（退款不是赠送）。
    """
    current = await get_user_free_count(db, user_id)
    if current is None or current < 0:
        logger.info(f"User {user_id}: unlimited free count, no refund needed")
        return -1 if current is None else current

    await db.execute(
        update(User)
        .where(User.id == user_id, User.free_count >= 0)
        .values(free_count=User.free_count + 1)
    )
    await db.flush()
    result = await db.execute(select(User.free_count).where(User.id == user_id))
    remaining = result.scalar()
    logger.info(f"User {user_id}: refunded 1 free count, remaining={remaining}")
    return remaining


async def process_with_billing(
    db: AsyncSession, user_id: int, amount: float = None,
) -> dict:
    """
    判断用户是否可免费处理:
    - 免费次数为 -1 → 无限次数，返回 {"free_used": True, "unlimited": True, "remaining_free_count": -1}
    - 有免费次数 → 扣减 1 次，返回 {"free_used": True, "remaining_free_count": N}
    - 无免费次数 → 抛出 NeedPaymentException

    注意：无限次数分支也必须带 remaining_free_count（用 -1 表示无限），
    否则调用方（app/api/v1/process.py）取不到该键会 KeyError → 接口 500。
    """
    free_count = await get_user_free_count(db, user_id)

    # 无限次数（管理员设置）
    if free_count == -1:
        logger.info(f"User {user_id}: unlimited free count used")
        return {"free_used": True, "unlimited": True, "remaining_free_count": -1}

    if free_count > 0:
        remaining = await decrement_free_count(db, user_id)
        logger.info(f"User {user_id}: used free count, remaining={remaining}")
        return {"free_used": True, "remaining_free_count": remaining}

    # 无免费次数
    price = amount if amount is not None else await get_unit_price(db)
    raise NeedPaymentException(
        amount=price,
        order_id=0,  # 此时订单尚未创建，由 payment/create 接口创建
        order_no="",
    )
