"""支付抽象基类 — 定义统一的支付接口."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BasePayment(ABC):
    """支付抽象基类，所有支付实现必须继承."""

    @abstractmethod
    async def create_order(
        self, out_trade_no: str, amount: float, description: str,
        notify_url: str, **kwargs,
    ) -> Dict[str, Any]:
        """
        创建支付订单 / 统一下单.

        参数:
            out_trade_no: 商户订单号
            amount: 金额（元）
            description: 商品描述
            notify_url: 支付回调 URL
            **kwargs: 平台特有参数（如 openid）

        返回:
            包含支付参数的字典（前端用于调起支付）
        """
        ...

    @abstractmethod
    async def query_order(self, out_trade_no: str) -> Dict[str, Any]:
        """
        查询订单状态.

        返回:
            {"status": "paid"/"pending"/"closed", "trade_no": "...", "amount": ...}
        """
        ...

    @abstractmethod
    async def refund(
        self, out_trade_no: str, refund_amount: float, total_amount: float,
        refund_reason: str = "",
    ) -> Dict[str, Any]:
        """
        申请退款.

        参数:
            out_trade_no: 原商户订单号
            refund_amount: 退款金额（元）
            total_amount: 原订单金额（元）
            refund_reason: 退款原因

        返回:
            {"success": True/False, "refund_trade_no": "...", "message": "..."}
        """
        ...

    @abstractmethod
    async def verify_notify(self, body: bytes, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """
        验证支付回调签名并解析回调数据.

        参数:
            body: 请求体原始 bytes
            headers: 请求头字典

        返回:
            解析后的回调数据字典（验签失败返回 None）
        """
        ...
