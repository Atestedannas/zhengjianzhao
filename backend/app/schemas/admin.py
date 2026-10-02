"""管理后台相关 Pydantic 模型."""

from typing import Literal, Optional, List
from pydantic import BaseModel, Field


# ------ 登录 ------
class AdminLoginRequest(BaseModel):
    """管理员登录请求."""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AdminRefreshRequest(BaseModel):
    """管理员刷新 Token 请求."""
    refresh_token: str = Field(..., min_length=1)


# ------ 数据看板 ------
class DashboardStats(BaseModel):
    """数据看板统计."""
    today_process_count: int = 0
    total_process_count: int = 0
    today_income: float = 0.0
    total_paid_orders: int = 0
    free_usage_ratio: float = 0.0
    avg_processing_time_ms: float = 0.0
    rembg_fail_rate: float = 0.0


class TrendDataPoint(BaseModel):
    """趋势图数据点."""
    date: str
    count: int


class TemplateUsageItem(BaseModel):
    """模板使用占比."""
    name: str
    count: int
    percentage: float


# ------ 记录管理 ------
class RecordListQuery(BaseModel):
    """记录列表查询."""
    page: int = 1
    page_size: int = 20
    template_id: Optional[int] = None
    status: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class RecordOut(BaseModel):
    """记录响应."""
    id: int
    user_id: Optional[int] = None
    template_name: Optional[str] = None
    request_params: Optional[dict] = None
    is_paid: bool = False
    paid_amount: Optional[float] = None
    original_size: int
    result_size: int
    result_pixels: Optional[str] = None
    result_dpi: Optional[int] = None
    bg_color: Optional[str] = None
    processing_time_ms: int
    status: str
    error_message: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


# ------ 模板管理 ------
class TemplateCreateRequest(BaseModel):
    """新增模板."""
    name: str = Field(..., min_length=1, max_length=128)
    width_px: int = Field(..., ge=0)
    height_px: int = Field(..., ge=0)
    dpi: int = 350
    min_kb: int = 0
    max_kb: int = 100
    allowed_bg_colors: List[str] = ["white"]
    output_format: str = "JPEG"
    physical_size_mm: Optional[str] = None
    remark: Optional[str] = None
    is_active: bool = True


class TemplateUpdateRequest(TemplateCreateRequest):
    """编辑模板（字段同新增）."""
    pass


class TemplateToggleRequest(BaseModel):
    """模板启用/禁用请求."""
    is_active: Optional[bool] = None


# ------ 用户管理 ------
class UserListQuery(BaseModel):
    """用户列表查询."""
    page: int = 1
    page_size: int = 20
    keyword: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class AdjustFreeCountRequest(BaseModel):
    """手动调整用户免费次数.

    mode:
        delta（默认）—— count 是增量，可正可负，free_count += count
        set          —— count 是目标值，free_count = count（后台「调整为」用这个）
    历史 bug：后台弹窗写的是「调整为 N」，接口却是增量语义，
    5 次改成 10 会得到 15。前端现在显式传 mode="set"。
    """
    count: int = Field(..., description="mode=delta 时为调整量（可正可负）；mode=set 时为目标值")
    mode: Literal["delta", "set"] = Field("delta", description="delta=增量（兼容旧调用），set=直接设为该值")
    reason: str = "管理员手动调整"


class UserToggleRequest(BaseModel):
    """用户启用/禁用请求."""
    is_active: Optional[bool] = None


class UserOut(BaseModel):
    """用户列表项."""
    id: int
    openid: str = ""
    nickname: str = ""
    platform: str
    free_count: int = 0
    balance: float = 0.0
    total_spent: float = 0.0
    is_active: bool = True
    last_login_at: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class UserDetailOut(UserOut):
    """用户详情（含处理记录数）."""
    process_count: int = 0
    order_count: int = 0


class FreeCountAdjustRequest(BaseModel):
    """免费次数调整请求."""
    delta: int = Field(..., description="正数增加、负数减少")
    reason: str = ""


# ------ 价格策略 ------
class PricingConfig(BaseModel):
    """价格策略."""
    unit_price: float = 0.99
    register_bonus: int = 3
    daily_bonus_enabled: bool = False
    daily_bonus_count: int = 1


# ------ 系统配置（更新） ------
class SettingsUpdateRequest(BaseModel):
    """系统配置更新请求（与前端字段对齐，全可选）."""
    upload_max_mb: Optional[int] = None
    temp_file_ttl_minutes: Optional[int] = None
    rate_limit_per_minute: Optional[int] = None
    maintenance_mode: Optional[bool] = None


class SystemConfigItem(BaseModel):
    """系统配置项."""
    config_key: str
    config_value: str
    config_type: str = "string"

    class Config:
        from_attributes = True


class SystemConfigUpdateRequest(BaseModel):
    """批量更新系统配置."""
    configs: List[SystemConfigItem]


# ------ 财务 ------
class FinanceReportItem(BaseModel):
    """财务报表条目."""
    date: str
    total_income: float = 0.0
    paid_count: int = 0
    refund_count: int = 0
    refund_amount: float = 0.0
