"""所有模型的集中导入."""

from app.models.base import Base
from app.models.user import User
from app.models.template import SpecTemplate
from app.models.process_record import ProcessRecord
from app.models.order import Order
from app.models.admin import AdminUser
from app.models.system_config import FreeCountConfig, SystemConfig
from app.models.daily_bonus import DailyBonusLog

__all__ = [
    "Base",
    "User",
    "SpecTemplate",
    "ProcessRecord",
    "Order",
    "AdminUser",
    "FreeCountConfig",
    "SystemConfig",
    "DailyBonusLog",
]
