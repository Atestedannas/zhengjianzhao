"""照片处理相关 Pydantic 模型（扩展版）."""

from typing import Optional, List
from pydantic import BaseModel, Field


class ProcessResponse(BaseModel):
    """处理成功响应 data."""
    record_id: int
    result_url: str
    download_url: str
    file_size_kb: float
    pixels: str
    dpi: int
    output_format: str = "JPEG"
    mime_type: str = "image/jpeg"
    warnings: List[str] = []
    free_used: bool = False
    remaining_free_count: int = 0
    faces_detected: int = 0
    processing_time_ms: int = 0


class ProcessNeedPaymentResponse(BaseModel):
    """需要付费响应 data."""
    order_id: int
    order_no: str
    amount: float
    need_pay: bool = True


class ProcessHistoryItem(BaseModel):
    """处理历史条目."""
    id: int
    template_name: Optional[str] = None
    result_pixels: Optional[str] = None
    result_size: Optional[int] = None
    bg_color: Optional[str] = None
    output_format: Optional[str] = None
    status: str
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class ProcessRequest(BaseModel):
    """处理请求参数."""
    width: int = Field(0, description="目标宽度（像素）")
    height: int = Field(0, description="目标高度（像素）")
    resize_mode: str = Field("crop", description="缩放模式: crop|exact|fit|fill|by_width|by_height")
    dpi: int = Field(300, description="目标 DPI（证件照不得低于 300）")
    min_kb: int = Field(0, description="最小文件大小（KB）")
    max_kb: int = Field(100, description="最大文件大小（KB）")
    bg_color: str = Field("white", description="背景色: 预设色名(white|blue|red|...) | keep | custom:#RRGGBB 自定义十六进制")
    output_format: str = Field("JPEG", description="输出格式: JPEG|PNG|PDF|WEBP|BMP|TIFF")
    beautify_level: int = Field(1, description="美颜等级: 0=OFF, 1=自然微调(默认), 2/3=内部测试档, 4=艺术档(仅非证件照,不受合规红线约束)")
    beautify_smooth: bool = Field(True, description="是否启用磨皮")
    beautify_brighten: bool = Field(True, description="是否启用提亮")
    beautify_blemish: bool = Field(True, description="是否启用去瑕疵（分区 + 结构保护）")
    beautify_adaptive: bool = Field(True, description="是否启用自适应强度（按肤质/曝光/清晰度/人脸占比）")
    beautify_tone: bool = Field(True, description="是否启用分区域低频肤色修正（额头/两颊/下颌/脖子各自拉向参考肤色）")
    beautify_texture: bool = Field(True, description="是否启用纹理回注（把磨皮拿走的毛孔细节按比例补回）")
    beautify_feature: bool = Field(True, description="是否启用五官轮廓带局部增强（眼/眉/鼻翼/唇缘，不改形状）")
    retouch_strength: float = Field(1.0, ge=0.0, le=1.0, description="还原度滑杆: 1=完整档位强度(默认), 0.7=轻修(推荐), 0=完全还原原图")
    id_photo_align: bool = Field(False, description="是否启用证件照人脸对齐")
    gender: Optional[str] = Field(None, description="性别: male|female")
    template_id: Optional[int] = Field(None, description="模板 ID")