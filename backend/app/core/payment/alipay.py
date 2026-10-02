"""支付宝支付实现 — 对接支付宝开放平台."""

import hashlib
import json
import os
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

import httpx
from loguru import logger

from app.config import settings
from app.core.payment.base import BasePayment

ALIPAY_API_BASE = "https://openapi.alipay.com/gateway.do"


def _load_private_key() -> bytes:
    """加载应用私钥."""
    path = settings.ALIPAY_PRIVATE_KEY_PATH
    if not path or not os.path.exists(path):
        logger.warning(f"Alipay private key not found at {path}")
        return b""
    with open(path, "rb") as f:
        return f.read()


def _load_public_key() -> bytes:
    """加载支付宝公钥."""
    path = settings.ALIPAY_PUBLIC_KEY_PATH
    if not path or not os.path.exists(path):
        logger.warning(f"Alipay public key not found at {path}")
        return b""
    with open(path, "rb") as f:
        return f.read()


class AlipayPayment(BasePayment):
    """支付宝支付实现."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._client = http_client or httpx.AsyncClient(timeout=30.0)

    def _sign(self, params: Dict[str, str]) -> str:
        """RSA2 签名."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        private_key_data = _load_private_key()
        if not private_key_data:
            return ""

        # 按 key 字母序排序拼接待签字符串
        sorted_items = sorted(
            [(k, v) for k, v in params.items() if v and k != "sign"],
            key=lambda x: x[0],
        )
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)

        private_key = serialization.load_pem_private_key(
            private_key_data, password=None, backend=default_backend(),
        )
        signature = private_key.sign(
            sign_str.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        import base64
        return base64.b64encode(signature).decode()

    def _verify_sign(self, params: Dict[str, str], signature: str) -> bool:
        """验证支付宝回调签名."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        public_key_data = _load_public_key()
        if not public_key_data:
            return False

        sorted_items = sorted(
            [(k, v) for k, v in params.items() if v and k not in ("sign", "sign_type")],
            key=lambda x: x[0],
        )
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)

        import base64
        public_key = serialization.load_pem_public_key(public_key_data, backend=default_backend())
        try:
            public_key.verify(
                base64.b64decode(signature),
                sign_str.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

    async def create_order(
        self, out_trade_no: str, amount: float, description: str,
        notify_url: str, **kwargs,
    ) -> Dict[str, Any]:
        """支付宝扫码支付 — 返回支付页面 URL."""
        biz_content = {
            "out_trade_no": out_trade_no,
            "total_amount": f"{amount:.2f}",
            "subject": description,
            "product_code": "FAST_INSTANT_TRADE_PAY",
        }

        params = {
            "app_id": settings.ALIPAY_APP_ID,
            "method": "alipay.trade.page.pay",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": "",  # 将由框架填充
            "version": "1.0",
            "notify_url": notify_url,
            "biz_content": json.dumps(biz_content, ensure_ascii=False),
        }

        params["sign"] = self._sign(params)

        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        pay_url = f"{ALIPAY_API_BASE}?{query_string}"

        return {
            "success": True,
            "order_no": out_trade_no,
            "pay_params": {"pay_url": pay_url},
        }

    async def query_order(self, out_trade_no: str) -> Dict[str, Any]:
        """查询支付宝订单."""
        biz_content = json.dumps({"out_trade_no": out_trade_no})

        params = {
            "app_id": settings.ALIPAY_APP_ID,
            "method": "alipay.trade.query",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": "",
            "version": "1.0",
            "biz_content": biz_content,
        }
        params["sign"] = self._sign(params)

        try:
            resp = await self._client.post(ALIPAY_API_BASE, data=params)
            data = resp.json()
            resp_data = data.get("alipay_trade_query_response", {})
            trade_status = resp_data.get("trade_status", "")

            status_map = {
                "TRADE_SUCCESS": "paid",
                "TRADE_CLOSED": "closed",
                "TRADE_FINISHED": "paid",
            }
            return {
                "status": status_map.get(trade_status, "pending"),
                "trade_no": resp_data.get("trade_no", ""),
                "amount": float(resp_data.get("total_amount", 0)),
            }
        except Exception as e:
            logger.exception(f"Alipay query_order error: {e}")
            return {"status": "unknown", "message": str(e)}

    async def refund(
        self, out_trade_no: str, refund_amount: float, total_amount: float,
        refund_reason: str = "",
    ) -> Dict[str, Any]:
        """支付宝退款."""
        biz_content = json.dumps({
            "out_trade_no": out_trade_no,
            "refund_amount": f"{refund_amount:.2f}",
            "refund_reason": refund_reason or "用户退款",
        })

        params = {
            "app_id": settings.ALIPAY_APP_ID,
            "method": "alipay.trade.refund",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": "",
            "version": "1.0",
            "biz_content": biz_content,
        }
        params["sign"] = self._sign(params)

        try:
            resp = await self._client.post(ALIPAY_API_BASE, data=params)
            data = resp.json()
            resp_data = data.get("alipay_trade_refund_response", {})
            code = resp_data.get("code", "")

            if code == "10000":
                return {
                    "success": True,
                    "refund_trade_no": out_trade_no,
                    "message": "退款成功",
                }
            return {"success": False, "message": resp_data.get("sub_msg", "退款失败")}
        except Exception as e:
            logger.exception(f"Alipay refund error: {e}")
            return {"success": False, "message": str(e)}

    async def verify_notify(self, body: bytes, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """验证支付宝回调签名."""
        try:
            body_str = body.decode("utf-8")
            params = {k: v[0] for k, v in parse_qs(body_str).items()}
            signature = params.pop("sign", "")

            if not self._verify_sign(params, signature):
                logger.warning("Alipay notify signature verification failed.")
                return None

            trade_status = params.get("trade_status", "")
            if trade_status == "TRADE_SUCCESS":
                return {
                    "out_trade_no": params.get("out_trade_no"),
                    "trade_no": params.get("trade_no"),
                    "amount": float(params.get("total_amount", 0)),
                    "status": "paid",
                }
            return {"event_type": trade_status}
        except Exception as e:
            logger.error(f"Alipay verify_notify failed: {e}")
            return None
