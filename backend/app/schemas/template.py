"""模板相关 Pydantic 模型."""

from typing import List, Optional
from pydantic import BaseModel


class TemplateOut(BaseModel):
    """模板列表项."""
    id: int
    name: str
    width_px: int
    height_px: int
    dpi: int
    min_kb: int
    max_kb: int
    allowed_bg_colors: List[str] = []
    output_format: str = "JPEG"
    physical_size_mm: Optional[str] = None
    is_active: bool = True
    remark: Optional[str] = None

    class Config:
        from_attributes = True


class TemplateDetail(TemplateOut):
    """模板详情（继承列表项，含时间戳）."""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
