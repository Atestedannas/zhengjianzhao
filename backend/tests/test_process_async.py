"""照片处理「Celery 异步化 + 内存治理」改造的端到端验证（真实路由 + 真实数据库）.

覆盖这次改动的全部关键点：
    1. 老库结构自愈：process_records.status ENUM 扩容 + original_path 补列（幂等）
    2. POST /process/ 不再同步出图，立即返回 pending + record_id
    3. 轮询 GET /process/{id}/status：pending → success，字段与旧版成功返回同名
    4. 处理中 preview/download 返回 409；完成后能取到真实 JPEG
    5. 处理失败 → 记录 failed + **免费次数自动退还**（不白扣）
    6. 重复执行终态记录不会二次退款（幂等）
    7. celery worker 被杀导致卡死的兜底清理 reap_stuck_records（退次数）
    8. 扣费后队列不可用 → 退还次数并返回 503
    9. 抠图引擎选择 resolve_engine（auto 内存不足自动降级）+ 模型路径扩展名归一化
   10. ProcessParams 快照恢复（脏 key 过滤 / gender "None" 还原）

用法（**必须**指向独立测试库，库名里带 test，脚本会 drop_all）：
    TEST_DATABASE_URL='mysql+aiomysql://root:123456@127.0.0.1:3306/photo_service_test' \
        py -3.13 backend/tests/test_process_async.py

未设置 TEST_DATABASE_URL 时直接跳过并以 0 退出。
"""

import asyncio
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


def _check_url(url: str) -> bool:
    if not url:
        print("未设置 TEST_DATABASE_URL —— 跳过异步处理端到端测试")
        return False
    db_name = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if "test" not in db_name:
        print(f"库名 {db_name!r} 不含 'test'，拒绝在非测试库上执行")
        return False
    return True


if not _check_url(TEST_DATABASE_URL):
    sys.exit(0)

# 必须在导入 app.* 之前设置：app.config 在 import 阶段就读取环境变量
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DEBUG"] = "false"
os.environ["WEB_LOGIN_PASSWORD"] = "test-web-password"
os.environ.pop("ADMIN_INIT_PASSWORD", None)
os.environ.pop("ADMIN_RESET_PASSWORD_ON_BOOT", None)
# 临时目录：Windows/本地开发没有 /data/temp
_LOCAL_TMP = os.path.join(BACKEND_DIR, "temp", "test_async")
os.environ["TEMP_FILE_DIR"] = _LOCAL_TMP
os.environ["PROCESS_PENDING_DIR"] = os.path.join(_LOCAL_TMP, "pending")

from fastapi import FastAPI                                                        # noqa: E402
from httpx import ASGITransport, AsyncClient                                        # noqa: E402
from PIL import Image, ImageDraw                                                    # noqa: E402
from sqlalchemy import select, text                                                 # noqa: E402

from app.api.v1.router import api_router                                            # noqa: E402
from app.config import settings                                                     # noqa: E402
from app.core.admin_seed import init_admin_user                                     # noqa: E402
from app.core.db_schema import ensure_process_record_schema                         # noqa: E402
from app.core.image_engine import background as bg_engine                           # noqa: E402
from app.core.pricing_config import ensure_pricing_config, invalidate_pricing_cache  # noqa: E402
from app.core.process_runner import (                                               # noqa: E402
    load_params,
    reap_stuck_records_async,
    run_process_record,
)
from app.dependencies import async_session_factory, engine                          # noqa: E402
from app.models import Base, FreeCountConfig, ProcessRecord, User                   # noqa: E402
from app.tasks.process_task import process_photo as process_photo_task               # noqa: E402

API = settings.API_V1_PREFIX
WEB_PASSWORD = "test-web-password"
REGISTER_BONUS = 3

_LEGACY_TABLE_SQL = """
CREATE TABLE process_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    template_id INT NULL,
    request_params JSON NOT NULL,
    is_paid TINYINT NOT NULL DEFAULT 0,
    paid_amount DECIMAL(10,2) NULL,
    payment_trade_no VARCHAR(64) NULL,
    original_size INT NOT NULL,
    result_size INT NOT NULL,
    result_pixels VARCHAR(16) NULL,
    result_dpi INT NULL,
    bg_color VARCHAR(16) NULL,
    processing_time_ms INT NOT NULL,
    status ENUM('success','failed') NOT NULL DEFAULT 'success',
    error_message VARCHAR(512) NULL,
    thumb_path VARCHAR(512) NULL,
    created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_jpeg(width: int = 600, height: int = 800) -> bytes:
    """造一张带「人脸」占位的测试图（避免依赖外部素材）."""
    img = Image.new("RGB", (width, height), (198, 178, 165))
    draw = ImageDraw.Draw(img)
    draw.ellipse((width // 4, height // 7, width * 3 // 4, height // 2),
                 fill=(236, 205, 182))
    draw.rectangle((width // 3, height // 2, width * 2 // 3, height * 5 // 6),
                   fill=(60, 80, 140))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _process_params(**overrides) -> dict:
    """最小成本的处理参数：keep 背景 + 关闭美颜 → 不加载任何大模型."""
    data = {
        "width": "295",
        "height": "413",
        "resize_mode": "crop",
        "upscale": "true",
        "dpi": "300",
        "min_kb": "0",
        "max_kb": "100",
        "bg_color": "keep",
        "output_format": "JPEG",
        "beautify_level": "0",
        "beautify_smooth": "true",
        "beautify_brighten": "true",
        "beautify_blemish": "true",
        "id_photo_align": "false",
    }
    data.update(overrides)
    return data


async def _get_user(db, user_id: int) -> User:
    return (await db.execute(select(User).where(User.id == user_id))).scalar()


async def _free_count_of(db, user_id: int) -> int:
    return (await db.execute(select(User.free_count).where(User.id == user_id))).scalar()


async def _make_record(db, user_id: int, *, status: str, original_path: str,
                       created_at=None, is_paid: int = 0) -> int:
    record = ProcessRecord(
        user_id=user_id,
        template_id=None,
        request_params=json.dumps({"bg_color": "keep", "beautify_level": 0,
                                   "output_format": "JPEG", "max_kb": 100}),
        is_paid=is_paid,
        paid_amount=0,
        original_size=1234,
        result_size=0,
        processing_time_ms=0,
        bg_color="keep",
        status=status,
        original_path=original_path,
    )
    if created_at is not None:
        record.created_at = created_at
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record.id


async def run() -> None:
    api = FastAPI()
    api.include_router(api_router, prefix=API)

    # ---------------------------------------------------------------
    # 1) 老库结构自愈：把「只有 success/failed 且没有 original_path」的
    #    旧表交给 ensure_process_record_schema，验证它能自动补齐（幂等）
    # ---------------------------------------------------------------
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text(_LEGACY_TABLE_SQL))
        await ensure_process_record_schema(conn)

        status_type = (await conn.execute(text(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'process_records' "
            "AND COLUMN_NAME = 'status'"
        ))).scalar()
        assert "pending" in status_type and "processing" in status_type, status_type

        columns = [row[0] for row in (await conn.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'process_records'"
        ))).fetchall()]
        assert "original_path" in columns, columns

        # 幂等：重复执行不应报错、不应重复 ALTER
        await ensure_process_record_schema(conn)
        _ok("老库结构自愈：status ENUM 扩容到 pending/processing 并补出 original_path（幂等）")

        # 恢复成 ORM 定义的标准结构，供后续用例使用
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # ---------------------------------------------------------------
    # 2) 业务准备：注册赠送 3 次，登录拿到 token
    # ---------------------------------------------------------------
    async with async_session_factory() as db:
        await ensure_pricing_config(db)
        await init_admin_user(db)
        cfg = (await db.execute(
            select(FreeCountConfig).where(FreeCountConfig.config_key == "register_bonus")
        )).scalar()
        if cfg is None:
            db.add(FreeCountConfig(config_key="register_bonus", config_value=str(REGISTER_BONUS)))
        else:
            cfg.config_value = str(REGISTER_BONUS)
        await db.commit()
        invalidate_pricing_cache()

    submitted: list[int] = []

    def _capture_delay(record_id: int):
        """替换 celery 的 delay：只记录投递，稍后由测试自己跑任务体."""
        submitted.append(record_id)
        return None

    original_delay = process_photo_task.delay
    process_photo_task.delay = _capture_delay  # type: ignore[assignment]

    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
        resp = await client.post(f"{API}/auth/web-login", json={"password": WEB_PASSWORD})
        assert resp.status_code == 200, resp.text
        token = resp.json()["data"]["access_token"]

        async with async_session_factory() as db:
            user = (await db.execute(select(User).order_by(User.id.desc()).limit(1))).scalar()
            user_id = user.id
            free_before = user.free_count
        assert free_before == REGISTER_BONUS, f"注册赠送次数异常: {free_before}"
        _ok(f"web 登录成功，user_id={user_id}，免费次数={free_before}")

        # -----------------------------------------------------------
        # 3) 提交：立即返回 pending + record_id（且已扣 1 次）
        # -----------------------------------------------------------
        files = {"file": ("test.jpg", _make_jpeg(), "image/jpeg")}
        resp = await client.post(f"{API}/process/", files=files,
                                 data=_process_params(), headers=_auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 200, body
        submit = body["data"]
        assert submit["status"] == "pending", submit
        record_id = submit["record_id"]
        assert submit["status_url"] == f"/api/v1/process/{record_id}/status"
        assert submit["remaining_free_count"] == free_before - 1, submit
        assert submitted == [record_id], submitted
        _ok(f"POST /process/ 立即返回 pending（record_id={record_id}，已扣 1 次 → "
            f"{submit['remaining_free_count']}）")

        # -----------------------------------------------------------
        # 4) 处理中：status=pending、preview/download 应 409
        # -----------------------------------------------------------
        resp = await client.get(f"{API}/process/{record_id}/status", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "pending"
        resp = await client.get(f"{API}/process/{record_id}/preview", headers=_auth(token))
        assert resp.status_code == 409, f"处理中 preview 应为 409，实际 {resp.status_code}"
        resp = await client.get(f"{API}/process/{record_id}/download", headers=_auth(token))
        assert resp.status_code == 409, f"处理中 download 应为 409，实际 {resp.status_code}"
        _ok("处理中状态：status=pending，preview/download 返回 409")

        # -----------------------------------------------------------
        # 5) 跑任务体（等价于 celery worker 执行），再查状态 → success
        # -----------------------------------------------------------
        out = await run_process_record(record_id)
        assert out["status"] == "success", out

        resp = await client.get(f"{API}/process/{record_id}/status", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "success", data
        assert data["error"] is None, data
        assert data["file_size_kb"] > 0, data
        assert data["dpi"] == 300, data
        assert data["result_url"].endswith("/preview")
        assert data["download_url"].endswith("/download")
        assert data["remaining_free_count"] == free_before - 1, data
        _ok(f"任务执行完成 → status=success（{data['file_size_kb']}KB, dpi={data['dpi']}）")

        resp = await client.get(f"{API}/process/{record_id}/preview", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        assert resp.content[:2] == b"\xff\xd8", "preview 不是 JPEG"
        resp = await client.get(f"{API}/process/{record_id}/download", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        _ok("preview / download 均能取到真实 JPEG")

        # -----------------------------------------------------------
        # 6) 扣费后队列不可用 → 退还次数 + 503
        # -----------------------------------------------------------
        def _boom(record_id: int):
            raise RuntimeError("broker down")

        process_photo_task.delay = _boom  # type: ignore[assignment]
        async with async_session_factory() as db:
            before_queue_fail = await _free_count_of(db, user_id)

        files = {"file": ("test.jpg", _make_jpeg(), "image/jpeg")}
        resp = await client.post(f"{API}/process/", files=files,
                                 data=_process_params(), headers=_auth(token))
        assert resp.status_code == 503, f"队列不可用应返回 503，实际 {resp.status_code}: {resp.text}"

        async with async_session_factory() as db:
            after_queue_fail = await _free_count_of(db, user_id)
        assert after_queue_fail == before_queue_fail, (
            f"队列不可用时次数没有退还: {before_queue_fail} → {after_queue_fail}"
        )
        _ok("队列不可用：返回 503 且免费次数已退还")

        process_photo_task.delay = _capture_delay  # type: ignore[assignment]

    process_photo_task.delay = original_delay  # type: ignore[assignment]

    # ---------------------------------------------------------------
    # 7) 处理失败（原图丢失）→ failed + 退次数；重复执行不二次退款
    # ---------------------------------------------------------------
    async with async_session_factory() as db:
        before_fail = await _free_count_of(db, user_id)
        fail_id = await _make_record(db, user_id, status="pending",
                                     original_path=os.path.join(_LOCAL_TMP, "not-exists.jpg"))

    out = await run_process_record(fail_id)
    assert out["status"] == "failed", out
    async with async_session_factory() as db:
        record = await db.get(ProcessRecord, fail_id)
        assert record.status == "failed" and record.error_message, record.error_message
        after_fail = await _free_count_of(db, user_id)
    assert after_fail == before_fail + 1, f"失败未退还次数: {before_fail} → {after_fail}"
    _ok(f"处理失败：status=failed 且次数退还（{before_fail} → {after_fail}）")

    out = await run_process_record(fail_id)
    assert out["status"] == "failed", out
    async with async_session_factory() as db:
        after_replay = await _free_count_of(db, user_id)
    assert after_replay == after_fail, f"终态记录被重复执行导致二次退款: {after_fail} → {after_replay}"
    _ok("终态记录重复投递：直接跳过，不会二次退款")

    # ---------------------------------------------------------------
    # 8) 卡死兜底：worker 被 OOM 杀掉后记录会停在 processing
    # ---------------------------------------------------------------
    async with async_session_factory() as db:
        before_reap = await _free_count_of(db, user_id)
        stuck_id = await _make_record(
            db, user_id, status="processing",
            original_path=os.path.join(_LOCAL_TMP, "stuck.jpg"),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=settings.PROCESS_STUCK_MINUTES + 10),
        )

    result = await reap_stuck_records_async()
    assert result["reaped"] >= 1, result
    async with async_session_factory() as db:
        stuck = await db.get(ProcessRecord, stuck_id)
        assert stuck.status == "failed", stuck.status
        assert stuck.error_message, stuck.error_message
        after_reap = await _free_count_of(db, user_id)
    assert after_reap == before_reap + 1, f"兜底未退还次数: {before_reap} → {after_reap}"
    _ok(f"卡死兜底：processing 超时记录被判 failed 并退还次数（{before_reap} → {after_reap}）")

    # ---------------------------------------------------------------
    # 9) 纯函数：抠图引擎选择 / 模型路径 / 参数快照恢复
    # ---------------------------------------------------------------
    origin_engine = settings.BG_ENGINE
    origin_threshold = settings.BIREFNET_MIN_AVAILABLE_MB
    try:
        settings.BG_ENGINE = "u2net"
        assert bg_engine.resolve_engine() == "u2net"
        settings.BG_ENGINE = "off"
        assert bg_engine.resolve_engine() == "off"
        settings.BG_ENGINE = "birefnet"
        assert bg_engine.resolve_engine() == "birefnet"

        settings.BG_ENGINE = "auto"
        # 注入可用内存，避免依赖宿主机 /proc/meminfo（Windows 上读不到）
        settings.BIREFNET_MIN_AVAILABLE_MB = 3600
        assert bg_engine.resolve_engine(available_mb=800) == "u2net", "auto 模式下内存不足没有降级"
        assert bg_engine.resolve_engine(available_mb=8000) == "birefnet", "内存充足却没用 BiRefNet"
        assert bg_engine.resolve_engine(available_mb=None) in ("birefnet", "u2net")
        # 边界：刚好等于阈值 → 用 BiRefNet
        assert bg_engine.resolve_engine(available_mb=3600) == "birefnet"
    finally:
        settings.BG_ENGINE = origin_engine
        settings.BIREFNET_MIN_AVAILABLE_MB = origin_threshold
    _ok("resolve_engine：off/u2net/birefnet 直选正确，auto 内存不足自动降级")

    for name in ("u2net_human_seg.onnx", "u2net_human_seg"):
        candidates = bg_engine._model_candidates(name)
        assert any(c.endswith(os.sep + "u2net_human_seg.onnx") for c in candidates), candidates
        assert not any(c.endswith(".onnx.onnx") for c in candidates), candidates
    _ok("模型候选路径：带不带 .onnx 都能命中，不再出现 xxx.onnx.onnx")

    params = load_params({"width": 295, "gender": "None", "bogus_key": 1, "bg_color": "keep"})
    assert params.width == 295 and params.gender is None and params.bg_color == "keep"
    params = load_params("not-json")
    assert params.bg_color == "white"
    _ok("ProcessParams 快照恢复：脏 key 被过滤，gender 'None' 还原为 None")

    assert process_photo_task.name == "app.tasks.process.process_photo"
    _ok("celery 任务注册名正确")


def main() -> None:
    print("=" * 72)
    print("照片处理异步化改造验证（真实路由 + 真实数据库）")
    print("=" * 72)
    asyncio.run(run())
    print("\n全部通过")


if __name__ == "__main__":
    main()
