"""API 级端到端验证（可选脚本）：真实路由 + 真实数据库 + 生产同款 asyncmy 驱动.

覆盖范围（都是这次改动的地方）：
    1. POST /auth/web-login        注册赠送次数读后台 register_bonus（不再硬编码 5）
    2. GET  /user/profile          第一次带 token 的请求自动发放每日免费次数
    3. GET  /user/daily-bonus      状态：开关 / 数量 / 今天是否已发
    4. POST /user/daily-bonus/claim 幂等：同一天重复领取不加次数
    5. POST /admin/users/{id}/free-count  mode=set 是「设为 N」，省略 mode 仍是增量
    6. PUT  /admin/pricing         保存后立刻生效（配置缓存被失效）
    7. POST /admin/users/{id}/set-unlimited  无限次数用户跳过每日赠送，不会被加成正数

用法（**必须**指向独立测试库，库名里带 test，脚本会 drop_all）:

    TEST_DATABASE_URL='mysql+asyncmy://user:pw@127.0.0.1:3306/photo_service_test' \
        python backend/tests/test_daily_bonus_api.py

没设置 TEST_DATABASE_URL 时直接跳过并以 0 退出。
"""

import asyncio
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


def _check_url(url: str) -> bool:
    if not url:
        print("未设置 TEST_DATABASE_URL —— 跳过 API 端到端测试")
        return False
    db_name = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if "test" not in db_name:
        print(f"库名 {db_name!r} 不含 'test'，拒绝在非测试库上执行")
        return False
    return True


if not _check_url(TEST_DATABASE_URL):
    sys.exit(0)

# 必须在导入 app.* 之前设置：app.dependencies 在 import 阶段就会创建引擎 / 读配置
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DEBUG"] = "false"
os.environ["WEB_LOGIN_PASSWORD"] = "test-web-password"
os.environ.pop("ADMIN_INIT_PASSWORD", None)
os.environ.pop("ADMIN_RESET_PASSWORD_ON_BOOT", None)

from fastapi import FastAPI                                                        # noqa: E402
from httpx import ASGITransport, AsyncClient                                        # noqa: E402
from sqlalchemy import select                                                       # noqa: E402

from app.api.v1.router import api_router                                            # noqa: E402
from app.config import settings                                                     # noqa: E402
from app.core.admin_seed import init_admin_user                                     # noqa: E402
from app.core.pricing_config import ensure_pricing_config, invalidate_pricing_cache  # noqa: E402
from app.dependencies import async_session_factory, engine                          # noqa: E402
from app.models import Base, DailyBonusLog, FreeCountConfig                         # noqa: E402

API = settings.API_V1_PREFIX
WEB_PASSWORD = "test-web-password"
ADMIN_PASSWORD = "admin_test"
REGISTER_BONUS = 7
DAILY_COUNT = 100


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ok(text: str) -> None:
    print(f"  [OK] {text}")


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


async def _free_count(client: AsyncClient, token: str) -> int:
    resp = await client.get(f"{API}/user/free-count", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["free_count"]


async def run() -> None:
    api = FastAPI()
    api.include_router(api_router, prefix=API)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        # 后台配置：注册送 7 次，每日送 100 次
        async with async_session_factory() as db:
            await ensure_pricing_config(db)
            await _set_config(db, "register_bonus", REGISTER_BONUS)
            await _set_config(db, "daily_bonus_enabled", 1)
            await _set_config(db, "daily_bonus_count", DAILY_COUNT)
            await init_admin_user(db)

        transport = ASGITransport(app=api)
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
            # 1) Web 密码登录 → 新用户
            resp = await client.post(f"{API}/auth/web-login", json={"password": WEB_PASSWORD})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["code"] == 200, body
            user_token = body["data"]["access_token"]
            _ok("POST /auth/web-login 登录成功")

            # 2) 第一次带 token 的请求：注册赠送 + 每日赠送同时到账
            resp = await client.get(f"{API}/user/profile", headers=_auth(user_token))
            assert resp.status_code == 200, resp.text
            profile = resp.json()["data"]
            uid = profile["id"]
            assert profile["free_count"] == REGISTER_BONUS + DAILY_COUNT, profile
            _ok(
                f"注册赠送读后台配置 {REGISTER_BONUS} + 每日自动发放 {DAILY_COUNT} "
                f"= {profile['free_count']}"
            )

            # 3) 每日状态
            resp = await client.get(f"{API}/user/daily-bonus", headers=_auth(user_token))
            status_data = resp.json()["data"]
            assert status_data["enabled"] is True, status_data
            assert status_data["count"] == DAILY_COUNT, status_data
            assert status_data["claimed_today"] is True, status_data
            assert status_data["free_count"] == REGISTER_BONUS + DAILY_COUNT, status_data
            _ok("GET /user/daily-bonus 显示今天已发放")

            # 4) 重复领取 → 幂等
            resp = await client.post(f"{API}/user/daily-bonus/claim", headers=_auth(user_token))
            claim = resp.json()["data"]
            assert claim["granted"] is False and claim["reason"] == "already_claimed", claim
            assert await _free_count(client, user_token) == REGISTER_BONUS + DAILY_COUNT
            _ok("POST /user/daily-bonus/claim 重复领取不加次数")

            # 5) 后台登录 + 价格策略读取
            resp = await client.post(
                f"{API}/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD}
            )
            assert resp.status_code == 200, resp.text
            admin_token = resp.json()["data"]["access_token"]
            resp = await client.post(
                f"{API}/admin/login", json={"username": "admin", "password": "wrong-password"}
            )
            assert resp.status_code == 401, resp.text
            resp = await client.get(f"{API}/admin/pricing", headers=_auth(admin_token))
            pricing = resp.json()["data"]
            assert pricing["register_bonus"] == REGISTER_BONUS, pricing
            assert pricing["daily_bonus_enabled"] is True, pricing
            assert pricing["daily_bonus_count"] == DAILY_COUNT, pricing
            _ok("POST /admin/login 成功，GET /admin/pricing 键名与值正确")

            # 6) mode=set 是「设为 N」（历史 bug：被当成增量）
            resp = await client.post(
                f"{API}/admin/users/{uid}/free-count",
                headers=_auth(admin_token),
                json={"count": 25, "mode": "set"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["free_count"] == 25, resp.json()
            assert await _free_count(client, user_token) == 25
            _ok("POST /admin/users/{id}/free-count mode=set → 设为 25")

            # 7) 省略 mode 时保持旧的增量语义
            resp = await client.post(
                f"{API}/admin/users/{uid}/free-count",
                headers=_auth(admin_token),
                json={"count": 5},
            )
            assert resp.status_code == 200, resp.text
            assert await _free_count(client, user_token) == 30
            _ok("省略 mode → 增量语义（25+5=30），旧调用不破坏")

            # 8) 后台关开关 → 立刻生效（配置缓存被主动失效）
            resp = await client.put(
                f"{API}/admin/pricing",
                headers=_auth(admin_token),
                json={"daily_bonus_enabled": False},
            )
            assert resp.status_code == 200, resp.text
            resp = await client.get(f"{API}/user/daily-bonus", headers=_auth(user_token))
            assert resp.json()["data"]["enabled"] is False, resp.json()
            _ok("PUT /admin/pricing 关闭每日免费 → 下一次请求就生效")

            # 9) 无限次数用户：不发每日赠送，也不把 -1 加成正数
            resp = await client.post(
                f"{API}/admin/users/{uid}/set-unlimited", headers=_auth(admin_token)
            )
            assert resp.status_code == 200, resp.text
            assert await _free_count(client, user_token) == -1
            resp = await client.post(f"{API}/user/daily-bonus/claim", headers=_auth(user_token))
            claim = resp.json()["data"]
            assert claim["reason"] in ("unlimited", "disabled"), claim
            assert await _free_count(client, user_token) == -1
            _ok("无限次数（-1）用户跳过每日赠送，次数保持 -1")

            # 数据库侧确认：只有一次发放记录
            async with async_session_factory() as db:
                logs = (await db.execute(select(DailyBonusLog))).scalars().all()
                assert len(logs) == 1, [(x.user_id, x.claim_date, x.bonus_count) for x in logs]
                assert logs[0].bonus_count == DAILY_COUNT, logs[0].bonus_count
            _ok("daily_bonus_logs 只有一条发放记录（并发/重复调用都不会多）")

        print("\nAPI 端到端测试全部通过")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
