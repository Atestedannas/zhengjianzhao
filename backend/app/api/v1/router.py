"""API v1 路由汇总."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.process import router as process_router
from app.api.v1.template import router as template_router
from app.api.v1.payment import router as payment_router
from app.api.v1.user import router as user_router
from app.api.v1.admin.router import admin_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
api_router.include_router(process_router, prefix="/process", tags=["Process"])
api_router.include_router(template_router, prefix="/templates", tags=["Templates"])
api_router.include_router(payment_router, prefix="/payment", tags=["Payment"])
api_router.include_router(user_router, prefix="/user", tags=["User"])
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
