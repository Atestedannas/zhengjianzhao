"""支付相关 Pydantic 模型."""

from typing import Optional, Any, Dict
from pydantic import BaseModel, Field


class CreatePaymentRequest(BaseModel):
    """创建支付请求."""
    pay_method: str = Field(..., description="wechat_jsapi / wechat_native / alipay")


class PayParamsWechat(BaseModel):
    """微信 JSAPI 支付参数."""
    appId: str = ""
    timeStamp: str = ""
    nonceStr: str = ""
    package: str = ""
    signType: str = "RSA"
    paySign: str = ""


class PaymentResponse(BaseModel):
    """支付创建响应 data."""
    order_no: str
    amount: float
    pay_params: Any = None  # PayParamsWechat 或 qrcode_url 字符串
