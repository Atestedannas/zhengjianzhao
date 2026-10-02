"""每日免费次数 —— 真库集成验证（可选脚本）.

test_daily_bonus.py 只覆盖纯函数；这里把 SQL 行为也真跑一遍：
唯一键幂等、并发只发一次、无限次数（-1）不会被加成、
以及「无限次数导致处理接口 500」那个坑的修复。

用法（**必须**指向独立测试库，库名里带 test 才允许执行，脚本会 drop_all）:

    # 建库示例
    #   CREATE DATABASE photo_service_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    TEST_DATABASE_URL='mysql+asyncmy://photo_app:pw@127.0.0.1:3306/photo_service_test' \
        python backend/tests/test_daily_bonus_db.py

没设置 TEST_DATABASE_URL 时直接跳过并以 0 退出，不会影响常规测试流程。
"""

import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import select, update                                    # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core import billing                                             # noqa: E402
from app.core import daily_bonus as daily_bonus_mod                      # noqa: E402
from app.core.pricing_config import ensure_pricing_config, invalidate_pricing_cache  # noqa: E402
from app.models import Base, FreeCountConfig, User                       # noqa: E402
from app.utils.error_codes import NeedPaymentException                   # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

DAY1 = date(2026, 10, 2)
DAY2 = date(2026, 10, 3)
DAY3 = date(2026, 10, 4)
DAY4 = date(2026, 10, 5)
DAY5 = date(2026, 10, 6)


def _check_url(url: str) -> bool:
    """安全阀：没配 URL 跳过；库名里没有 test 一律拒绝（脚本会 drop_all）."""
    if not url:
        print("未设置 TEST_DATABASE_URL —— 跳过真库集成测试")
        return False
    db_name = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if "test" not in db_name:
        print(f"库名 {db_name!r} 不含 'test'，拒绝在非测试库上执行")
        return False
    return True


async def _set_config(db, key: str, value) -> None:
    cfg = (
        await db.execute(select(FreeCountConfig).where(FreeCountConfig.config_key == key))
    ).scalar()
    if cfg is None:
        db.add(FreeCountConfig(config_key=key, config_value=str(value)))
    else:
        cfg.config_value = str(value)
    await db.commit()
    invalidate_pricing_cache()


async def _free_count(db, user_id: int):
    return (await db.execute(select(User.free_count).where(User.id == user_id))).scalar()


async def _grant_on(db, user_id: int, day: date) -> dict:
    """把「今天」固定成 day 再发放（daily_bonus 内部按名字引用 business_today）."""
    original = daily_bonus_mod.business_today
    daily_bonus_mod.business_today = lambda *a, **k: day
    try:
        return await daily_bonus_mod.grant_daily_bonus(db, user_id)
    finally:
        daily_bonus_mod.business_today = original


async def _attempt(session_factory, user_id: int) -> dict:
    """一次并发发放尝试（独立 session / 独立连接）."""
    async with session_factory() as db:
        result = await daily_bonus_mod.grant_daily_bonus(db, user_id)
        await db.commit()
        return result


async def run() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            # 1) 启动补齐缺失配置键（老库键名错的兜底）
            added = await ensure_pricing_config(db)
            assert set(added) == {
                "unit_price", "register_bonus", "daily_bonus_enabled", "daily_bonus_count",
            }, added
            print("  [OK] ensure_pricing_config 补齐 4 个配置键")

            # 后台开启每日免费：每天 100 次
            await _set_config(db, "daily_bonus_enabled", "1")
            await _set_config(db, "daily_bonus_count", "100")

            user = User(
                openid="test_daily_bonus_user", platform="wechat_web",
                nickname="集成测试", free_count=0, free_count_total=0,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            uid = user.id

            # 2) 当天首次发放
            result = await _grant_on(db, uid, DAY1)
            await db.commit()
            assert result["granted"] and result["count"] == 100, result
            assert await _free_count(db, uid) == 100
            print("  [OK] 开启开关后当天自动发放 100 次")

            # 3) 同一天重复触发 → 幂等
            result = await _grant_on(db, uid, DAY1)
            await db.commit()
            assert not result["granted"] and result["reason"] == "already_claimed", result
            assert await _free_count(db, uid) == 100
            print("  [OK] 同一天重复触发不会重复发放（唯一键幂等）")

            # 4) 第二天再发一次
            result = await _grant_on(db, uid, DAY2)
            await db.commit()
            assert result["granted"], result
            assert await _free_count(db, uid) == 200
            print("  [OK] 跨天后继续发放（累计 200 次）")

            # 5) 关掉开关 → 不再发放（后台设置真的生效）
            await _set_config(db, "daily_bonus_enabled", "0")
            result = await _grant_on(db, uid, DAY3)
            await db.commit()
            assert not result["granted"] and result["reason"] == "disabled", result
            assert await _free_count(db, uid) == 200
            print("  [OK] 关闭开关后不再发放")

            # 6) 无限次数用户不发放，也不会把 -1 加成正数
            await _set_config(db, "daily_bonus_enabled", "1")
            await db.execute(update(User).where(User.id == uid).values(free_count=-1))
            await db.commit()
            result = await _grant_on(db, uid, DAY4)
            await db.commit()
            assert not result["granted"] and result["reason"] == "unlimited", result
            assert await _free_count(db, uid) == -1
            print("  [OK] 无限次数（-1）用户跳过每日赠送")

            # 7) 坑1 修复：无限次数下计费返回 -1，而不是 KeyError → 500
            billing_result = await billing.process_with_billing(db, uid)
            assert billing_result.get("unlimited") is True, billing_result
            assert billing_result.get("remaining_free_count") == -1, billing_result
            print("  [OK] 无限次数计费返回 remaining_free_count=-1（处理接口不再 500）")

            # 8) 正常扣减 + 用尽后抛 402 支付异常
            await db.execute(update(User).where(User.id == uid).values(free_count=1))
            await db.commit()
            billing_result = await billing.process_with_billing(db, uid)
            assert billing_result["free_used"] and billing_result["remaining_free_count"] == 0
            try:
                await billing.process_with_billing(db, uid)
                raise AssertionError("次数用尽应当抛 NeedPaymentException")
            except NeedPaymentException:
                pass
            await db.commit()
            print("  [OK] 有次数扣 1、用尽抛 402 支付异常")

            # 9) 并发：同一天两个请求同时发放，只能成功一次
            u2 = User(
                openid="test_daily_bonus_concurrent", platform="wechat_web",
                nickname="并发测试", free_count=0, free_count_total=0,
            )
            db.add(u2)
            await db.commit()
            await db.refresh(u2)
            uid2 = u2.id
            await _set_config(db, "daily_bonus_count", "7")

            original_today = daily_bonus_mod.business_today
            daily_bonus_mod.business_today = lambda *a, **k: DAY5
            try:
                results = await asyncio.gather(
                    _attempt(session_factory, uid2),
                    _attempt(session_factory, uid2),
                    return_exceptions=True,
                )
            finally:
                daily_bonus_mod.business_today = original_today

            granted = [r for r in results if isinstance(r, dict) and r.get("granted")]
            assert len(granted) == 1, f"并发下应只发放一次，实际: {results}"
            assert await _free_count(db, uid2) == 7, await _free_count(db, uid2)
            print("  [OK] 并发两个请求只发放一次（7 次，而不是 14 次）")

        print("\n真库集成测试全部通过")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if _check_url(TEST_DATABASE_URL):
        asyncio.run(run())
