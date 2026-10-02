"""配置启动自检测试 —— 防止「误用 SQLite / 配置缺失」再次引发容器无限重启.

历史事故:
    服务器 backend/.env 误留了开发用的
        DATABASE_URL=sqlite+aiosqlite:///./test.db
    而镜像里没有 aiosqlite，于是 gunicorn 每个 worker 起来就抛
        ModuleNotFoundError: No module named 'aiosqlite'
    容器反复重启，只在日志里留下难懂的堆栈。

    现在 app/config.py 的 runtime_config_errors() 会在启动时直接
    用明确文案拒绝启动。本测试锁定该行为。

运行:
    python backend/tests/test_config_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import Settings, WEB_OAUTH_CALLBACK_PATH   # noqa: E402


def _s(**kw) -> Settings:
    """构造隔离的 Settings（不读 .env，避免受本机环境影响）."""
    return Settings(_env_file=None, **kw)


def test_production_sqlite_rejected():
    s = _s(
        APP_ENV="production",
        DATABASE_URL="sqlite+aiosqlite:///./test.db",
        REDIS_URL="redis://127.0.0.1:6379/0",
    )
    assert s.IS_SQLITE is True
    errs = s.runtime_config_errors()
    assert any("SQLite" in e for e in errs), "生产环境使用 SQLite 必须被拦截"
    print(f"  [OK] 生产 + SQLite 被拦截: {errs[0][:60]}...")


def test_production_mysql_accepted():
    s = _s(
        APP_ENV="production",
        DATABASE_URL="mysql+asyncmy://photo_app:pw@photo-mysql:3306/photo_service",
        REDIS_URL="redis://photo-redis:6379/0",
    )
    assert s.IS_SQLITE is False
    assert s.runtime_config_errors() == [], "正确的 MySQL 配置不应报错"
    print("  [OK] 生产 + MySQL 通过自检")


def test_development_mysql_parts_build_correct_url():
    """分项配置（DB_HOST/DB_USER/...）必须拼出 mysql+asyncmy URL."""
    s = _s(
        APP_ENV="development",
        DB_HOST="127.0.0.1", DB_PORT=3306, DB_USER="photo_app",
        DB_PASSWORD="p@ss w0rd", DB_NAME="photo_service",
        REDIS_URL="redis://127.0.0.1:6379/0",
    )
    url = s.DATABASE_URL_FINAL
    assert url.startswith("mysql+asyncmy://"), url
    assert "p%40ss+w0rd" in url, f"密码未正确 URL 编码: {url}"
    assert s.runtime_config_errors() == []

    sync = s.DATABASE_URL_SYNC
    assert sync.startswith("mysql+pymysql://"), sync
    print(f"  [OK] 分项配置拼接: {url}")


def test_missing_database_config_detected():
    s = _s(APP_ENV="development", DB_HOST="", DATABASE_URL="",
           REDIS_URL="redis://127.0.0.1:6379/0")
    errs = s.runtime_config_errors()
    assert any("数据库未配置" in e for e in errs), errs
    print(f"  [OK] 数据库未配置被检出: {errs[0][:40]}...")


def test_bad_redis_url_detected():
    s = _s(
        APP_ENV="production",
        DATABASE_URL="mysql+asyncmy://u:p@h:3306/db",
        REDIS_URL="127.0.0.1:6379",          # 少了 redis:// 前缀
    )
    errs = s.runtime_config_errors()
    assert any("REDIS_URL" in e for e in errs), errs
    print(f"  [OK] 非法 Redis URL 被检出: {errs[0][:50]}...")


def test_oauth_redirect_uri_fallback():
    """扫码登录回调：显式优先，否则用 PUBLIC_BASE_URL 拼默认路径，都无则空串."""
    s1 = _s(APP_ENV="production", DATABASE_URL="mysql+asyncmy://u:p@h:3306/db",
            PUBLIC_BASE_URL="http://1.2.3.4:8081/")
    assert s1.web_oauth_redirect_uri("wechat") == f"http://1.2.3.4:8081{WEB_OAUTH_CALLBACK_PATH}"

    s2 = _s(APP_ENV="production", DATABASE_URL="mysql+asyncmy://u:p@h:3306/db",
            PUBLIC_BASE_URL="http://1.2.3.4:8081",
            WECHAT_WEB_REDIRECT_URI="https://custom.example.com/cb")
    assert s2.web_oauth_redirect_uri("wechat") == "https://custom.example.com/cb"

    s3 = _s(APP_ENV="production", DATABASE_URL="mysql+asyncmy://u:p@h:3306/db",
            PUBLIC_BASE_URL="", WECHAT_WEB_REDIRECT_URI="")
    assert s3.web_oauth_redirect_uri("wechat") == "", "未配置时应返回空串（由接口给 503），而非抛异常"
    print("  [OK] OAuth 回调地址三级回退正确")


if __name__ == "__main__":
    print("=== 配置启动自检测试 ===")
    for fn in (
        test_production_sqlite_rejected,
        test_production_mysql_accepted,
        test_development_mysql_parts_build_correct_url,
        test_missing_database_config_detected,
        test_bad_redis_url_detected,
        test_oauth_redirect_uri_fallback,
    ):
        print(f"[{fn.__name__}]")
        fn()
    print("\n全部通过 ✅")
