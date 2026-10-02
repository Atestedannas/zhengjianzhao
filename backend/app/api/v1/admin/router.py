"""管理后台路由汇总."""

from fastapi import APIRouter

from app.api.v1.admin.auth import router as auth_router
from app.api.v1.admin.dashboard import router as dashboard_router
from app.api.v1.admin.records import router as records_router
from app.api.v1.admin.templates import router as templates_router
from app.api.v1.admin.users import router as users_router
from app.api.v1.admin.orders import router as orders_router
from app.api.v1.admin.finance import router as finance_router
from app.api.v1.admin.pricing import router as pricing_router
from app.api.v1.admin.settings import router as settings_router

admin_router = APIRouter()

admin_router.include_router(auth_router, tags=["Admin-Auth"])
admin_router.include_router(dashboard_router, prefix="/dashboard", tags=["Admin-Dashboard"])
admin_router.include_router(records_router, prefix="/records", tags=["Admin-Records"])
admin_router.include_router(templates_router, prefix="/templates", tags=["Admin-Templates"])
admin_router.include_router(users_router, prefix="/users", tags=["Admin-Users"])
admin_router.include_router(orders_router, prefix="/orders", tags=["Admin-Orders"])
admin_router.include_router(finance_router, prefix="/finance", tags=["Admin-Finance"])
admin_router.include_router(pricing_router, prefix="/pricing", tags=["Admin-Pricing"])
admin_router.include_router(settings_router, prefix="/settings", tags=["Admin-Settings"])
