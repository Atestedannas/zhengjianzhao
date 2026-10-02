"""错误码定义与统一响应."""

from typing import Any, Optional


class ErrorCode:
    """统一错误码枚举."""

    # 通用 (1000-1999)
    SUCCESS = 200
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    TOO_MANY_REQUESTS = 429
    INTERNAL_ERROR = 500

    # 图像处理 (2000-2999)
    IMAGE_FORMAT_UNSUPPORTED = 2001
    IMAGE_TOO_LARGE = 2002
    IMAGE_CROP_FAILED = 2003
    IMAGE_REMBG_FAILED = 2004
    IMAGE_COMPRESS_FAILED = 2005

    # 支付 (3000-3999)
    PAYMENT_ORDER_NOT_FOUND = 3001
    PAYMENT_ORDER_EXPIRED = 3002
    PAYMENT_FAILED = 3003
    PAYMENT_REFUND_FAILED = 3004
    PAYMENT_NEEDED = 402

    # 用户 (4000-4999)
    USER_NOT_FOUND = 4001
    USER_DISABLED = 4002
    FREE_COUNT_INSUFFICIENT = 4003
    LOGIN_FAILED = 4004

    # 管理员 (5000-5999)
    ADMIN_NOT_FOUND = 5001
    ADMIN_PERMISSION_DENIED = 5002
    ADMIN_TOKEN_EXPIRED = 5003

    # 模板 (6000-6999)
    TEMPLATE_NOT_FOUND = 6001
    TEMPLATE_DISABLED = 6002


DEFAULT_MESSAGES = {
    200: "操作成功",
    400: "请求参数错误",
    401: "未授权",
    403: "无权限",
    404: "资源不存在",
    429: "请求过于频繁",
    500: "服务器内部错误",
    2001: "不支持的文件格式",
    2002: "文件超过大小限制",
    2003: "裁剪失败",
    2004: "AI抠图失败，请上传正面免冠照",
    2005: "压缩失败，无法满足目标文件大小",
    3001: "订单不存在",
    3002: "订单已过期",
    3003: "支付失败",
    3004: "退款失败",
    402: "无可用免费次数，请先支付",
    4001: "用户不存在",
    4002: "用户已被禁用",
    4003: "免费次数不足",
    4004: "登录失败",
    5001: "管理员不存在",
    5002: "权限不足",
    5003: "管理员Token已过期",
    6001: "模板不存在",
    6002: "模板已禁用",
}


def get_default_message(code: int) -> str:
    return DEFAULT_MESSAGES.get(code, "")


def make_response(code: int = 200, data: Any = None, message: str = "") -> dict:
    """构建统一响应体."""
    return {
        "code": code,
        "message": message or get_default_message(code),
        "data": data,
    }


class NeedPaymentException(Exception):
    """需要付费的异常."""

    def __init__(self, amount: float, order_id: int, order_no: str):
        self.amount = amount
        self.order_id = order_id
        self.order_no = order_no
        super().__init__(f"Need payment: {amount} yuan, order {order_no}")
