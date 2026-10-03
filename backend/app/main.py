"""FastAPI 应用入口."""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.api.v1.router import api_router
from app.middleware.rate_limit import limiter
from app.middleware.request_id import RequestIDMiddleware
from app.utils.error_codes import make_response, ErrorCode


def _mask_dsn(dsn: str) -> str:
    """隐藏 DSN 里的密码，避免凭据写进日志."""
    import re
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", dsn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理."""
    # ---------- 启动自检 ----------
    # 把配置错误在启动那一刻用明确文案抛出，而不是让 worker 崩溃重启、
    # 只在日志里留下一串难懂的 ModuleNotFoundError / AttributeError。
    config_errors = settings.runtime_config_errors()
    if config_errors:
        for err in config_errors:
            logger.error(f"配置错误: {err}")
        raise RuntimeError("启动自检未通过: " + "；".join(config_errors))

    from app.models import Base
    from app.dependencies import engine, async_session_factory
    from app.core.template_service import init_templates
    from app.core.admin_seed import init_admin_user
    from app.core.pricing_config import ensure_pricing_config
    from app.core.db_schema import ensure_process_record_schema

    logger.info(f"Starting PhotoService... env={settings.APP_ENV}")
    logger.info(f"Database: {_mask_dsn(settings.DATABASE_URL_FINAL)}")
    logger.info(f"Redis   : {settings.REDIS_URL}")

    # 自动补齐缺失的表结构（生产结构变更请走 alembic 迁移）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 既有库的列级变更（status ENUM 扩容 / original_path）必须是幂等 ALTER
        await ensure_process_record_schema(conn)

    await init_templates()
    logger.info("Templates loaded.")

    # 初始化管理员账户 + 补齐免费次数配置键
    # （老库里迁移种下的键名是错的，这里保证 daily_bonus_* / register_bonus 一定存在）
    async with async_session_factory() as db:
        await init_admin_user(db)
        await ensure_pricing_config(db)
    logger.info("Admin user checked; pricing config ensured.")

    yield

    from app.dependencies import redis_client as rc
    if rc is not None:
        await rc.close()
    logger.info("PhotoService shut down.")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# ---------- CORS ----------
_cors_origins = settings.CORS_ORIGINS
if not _cors_origins:
    if settings.APP_ENV == "development":
        # 开发环境允许本地前端
        _cors_origins = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"
    else:
        # 生产环境必须显式配置，否则拒绝所有跨域请求
        _cors_origins = ""

_cors_origin_list = [o.strip() for o in _cors_origins.split(",") if o.strip()] if _cors_origins else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origin_list if _cors_origin_list else ["http://localhost"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# ---------- 频率限制 ----------
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------- 请求 ID ----------
app.add_middleware(RequestIDMiddleware)

# ---------- 路由 ----------
# admin_router 已通过 api_router 以 /admin 前缀挂载，
# 最终路径为 {API_V1_PREFIX}/admin/...（即 /api/v1/admin/...），此处不重复挂载。
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content=make_response(ErrorCode.INTERNAL_ERROR, message="服务器内部错误"),
    )


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME}


# ---------- 静态文件（Web 前端） ----------
_web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "web")
if os.path.isdir(_web_dir):
    app.mount("/web", StaticFiles(directory=_web_dir, html=True), name="web")
    logger.info(f"Web frontend mounted from {_web_dir}")
