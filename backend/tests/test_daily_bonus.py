"""每日免费次数 / 定价配置测试.

锁定三件事：
1. 后台「每日免费」开关与数量真的能决定是否发放（历史 bug：配了不生效）；
2. 每人每天最多发一次（幂等判断逻辑）；
3. 「一天」按业务时区划分，且时区名非法时会安全回退。

运行:
    python backend/tests/test_daily_bonus.py
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.daily_bonus import evaluate_daily_bonus                      # noqa: E402
from app.core import pricing_config                                        # noqa: E402
from app.core.pricing_config import (                                      # noqa: E402
    PRICING_DEFAULTS,
    business_today,
    parse_bool,
    parse_float,
    parse_int,
)

TODAY = date(2026, 10, 2)
YESTERDAY = date(2026, 10, 1)


# ======================== 是否发放的判断 ========================
def test_enabled_grants_first_time_of_day():
    should, reason = evaluate_daily_bonus(
        enabled=True, count=100, free_count=0, last_claim_date=None, today=TODAY
    )
    assert should is True and reason == "granted"
    print("  [OK] 开启 + 今天没领过 → 发放")


def test_enabled_grants_again_next_day():
    should, reason = evaluate_daily_bonus(
        enabled=True, count=1, free_count=0, last_claim_date=YESTERDAY, today=TODAY
    )
    assert should is True, reason
    print("  [OK] 昨天领过，今天可以再领")


def test_already_claimed_today_is_idempotent():
    should, reason = evaluate_daily_bonus(
        enabled=True, count=100, free_count=100, last_claim_date=TODAY, today=TODAY
    )
    assert should is False and reason == "already_claimed"
    print("  [OK] 今天已领过 → 不再发放（幂等）")


def test_disabled_switch_blocks_grant():
    should, reason = evaluate_daily_bonus(
        enabled=False, count=100, free_count=0, last_claim_date=None, today=TODAY
    )
    assert should is False and reason == "disabled"
    print("  [OK] 后台开关关闭 → 不发放")


def test_zero_count_blocks_grant():
    should, reason = evaluate_daily_bonus(
        enabled=True, count=0, free_count=0, last_claim_date=None, today=TODAY
    )
    assert should is False and reason == "count_zero"
    print("  [OK] 每日赠送数量为 0 → 不发放")


def test_unlimited_user_is_skipped():
    """free_count = -1 是管理员设的无限次数，不能再把 -1 加成 99."""
    should, reason = evaluate_daily_bonus(
        enabled=True, count=100, free_count=-1, last_claim_date=None, today=TODAY
    )
    assert should is False and reason == "unlimited"
    print("  [OK] 无限次数用户跳过每日赠送")


# ======================== 配置解析 ========================
def test_parse_helpers_tolerate_dirty_values():
    assert parse_bool("1") is True
    assert parse_bool("true") is True
    assert parse_bool("0") is False
    assert parse_bool(None, default=False) is False
    assert parse_int("100") == 100
    assert parse_int("abc", default=1) == 1
    assert parse_int("-5", default=1) == 1, "负数次数必须回落默认值"
    assert parse_float("0.99") == 0.99
    assert parse_float("", default=0.99) == 0.99
    print("  [OK] 脏配置值安全回落")


def test_pricing_defaults_match_admin_page_keys():
    """后台「价格策略」页的 4 个字段必须都有配置键，缺一个就会出现「配了不生效」."""
    assert set(PRICING_DEFAULTS) == {
        "unit_price", "register_bonus", "daily_bonus_enabled", "daily_bonus_count",
    }
    print("  [OK] 定价配置键与后台页面字段一一对应")


def test_migrations_seed_correct_config_keys():
    """回归测试：迁移里种的键名必须与代码读取的一致（历史 bug 就在键名不一致）."""
    versions_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "alembic", "versions"
    )
    text = ""
    for name in os.listdir(versions_dir):
        if name.endswith(".py"):
            with open(os.path.join(versions_dir, name), encoding="utf-8") as f:
                text += f.read()

    for key in PRICING_DEFAULTS:
        assert f"('{key}'" in text, f"迁移里没有种下配置键 {key}"
    for wrong in ("('register_gift'", "('daily_gift'", "('daily_gift_enabled'"):
        assert wrong not in text, f"迁移里仍有旧键名 {wrong}"
    print("  [OK] 迁移种的配置键与代码一致")


# ======================== 业务时区 ========================
def test_business_today_uses_business_timezone():
    # 2026-10-02 17:00 UTC = 2026-10-03 01:00 北京时间 → 算第二天
    assert business_today(datetime(2026, 10, 2, 17, 0, tzinfo=timezone.utc)) == date(2026, 10, 3)
    # 2026-10-02 15:59 UTC = 2026-10-02 23:59 北京时间 → 还是当天
    assert business_today(datetime(2026, 10, 2, 15, 59, tzinfo=timezone.utc)) == date(2026, 10, 2)
    # naive datetime 视为 UTC（库里的时间戳都是 UTC）
    assert business_today(datetime(2026, 10, 2, 17, 0)) == date(2026, 10, 3)
    print("  [OK] 「今天」按业务时区（默认北京时间）划分")


def test_unknown_timezone_falls_back_to_utc8():
    original = pricing_config.settings.DAILY_BONUS_TIMEZONE
    try:
        pricing_config.settings.DAILY_BONUS_TIMEZONE = "Not/AZone"
        tz = pricing_config.get_business_timezone()
        offset = datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone(tz).utcoffset()
        assert offset == timedelta(hours=8), offset
    finally:
        pricing_config.settings.DAILY_BONUS_TIMEZONE = original
        pricing_config._tz_cache.pop("Not/AZone", None)
    print("  [OK] 时区名非法时回退固定 UTC+8，不会让接口 500")


if __name__ == "__main__":
    print("=== 每日免费次数测试 ===")
    for fn in (
        test_enabled_grants_first_time_of_day,
        test_enabled_grants_again_next_day,
        test_already_claimed_today_is_idempotent,
        test_disabled_switch_blocks_grant,
        test_zero_count_blocks_grant,
        test_unlimited_user_is_skipped,
        test_parse_helpers_tolerate_dirty_values,
        test_pricing_defaults_match_admin_page_keys,
        test_migrations_seed_correct_config_keys,
        test_business_today_uses_business_timezone,
        test_unknown_timezone_falls_back_to_utc8,
    ):
        print(f"[{fn.__name__}]")
        fn()
    print("\n全部通过")
