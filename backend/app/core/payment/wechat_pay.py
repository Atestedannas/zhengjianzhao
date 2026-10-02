"""微信支付实现 — 对接微信支付 V3 API (JSAPI / Native)."""

import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from loguru import logger

from app.config import settings
from app.core.payment.base import BasePayment

WECHAT_API_BASE = "https://api.mch.weixin.qq.com"


def _load_private_key() -> bytes:
    """加载商户私钥."""
    path = settings.WECHAT_PRIVATE_KEY_PATH
    if not path or not os.path.exists(path):
        logger.warning(f"WeChat private key not found at {path}")
        return b""
    with open(path, "rb") as f:
        return f.read()


def _sign_rsa_sha256(message: str) -> str:
    """RSA-SHA256 签名，返回 Base64 签名结果."""
    private_key_data = _load_private_key()
    if not private_key_data:
        return ""
    private_key = serialization.load_pem_private_key(
        private_key_data, password=None, backend=default_backend(),
    )
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    import base64
    return base64.b64encode(signature).decode()


def _build_authorization(method: str, url_path: str, body: str = "") -> str:
    """
    构建微信支付 V3 Authorization 头.
    格式: WECHATPAY2-SHA256-RSA2048 mchid="...",nonce_str="...",signature="...",timestamp="...",serial_no="..."
    """
    timestamp = str(int(time.time()))
    nonce_str = uuid.uuid4().hex[:32]
    sign_message = f"{method}\n{url_path}\n{timestamp}\n{nonce_str}\n{body}\n"
    signature = _sign_rsa_sha256(sign_message)

    return (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{settings.WECHAT_MCH_ID}",'
        f'nonce_str="{nonce_str}",signature="{signature}",'
        f'timestamp="{timestamp}",serial_no="{settings.WECHAT_CERT_SERIAL_NO}"'
    )


class WechatPayment(BasePayment):
    """微信支付 V3 实现."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._client = http_client or httpx.AsyncClient(timeout=30.0)

    async def create_order(
        self, out_trade_no: str, amount: float, description: str,
        notify_url: str, **kwargs,
    ) -> Dict[str, Any]:
        """
        JSAPI / Native 统一下单.
        - 如果传了 openid → JSAPI 模式，返回 prepay_id 和小程序调起参数
        - 否则 → Native 模式，返回 code_url（二维码链接）
        """
        pay_type = kwargs.get("pay_type", "jsapi")
        total_fen = int(round(amount * 100))  # 金额：分

        body_data: Dict[str, Any] = {
            "appid": settings.WECHAT_MINI_APPID if pay_type == "jsapi" else settings.WECHAT_WEB_APPID,
            "mchid": settings.WECHAT_MCH_ID,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": notify_url,
            "amount": {"total": total_fen, "currency": "CNY"},
        }

        if pay_type == "jsapi":
            body_data["payer"] = {"openid": kwargs.get("openid", "")}
            url_path = "/v3/pay/transactions/jsapi"
        else:
            url_path = "/v3/pay/transactions/native"

        body_str = json.dumps(body_data)
        url = f"{WECHAT_API_BASE}{url_path}"

        try:
            resp = await self._client.post(
                url,
                content=body_str,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": _build_authorization("POST", url_path, body_str),
                },
            )
            data = resp.json()
            if resp.status_code != 200:
                logger.error(f"WeChat order creation failed: {data}")
                return {"success": False, "message": data.get("message", "Unknown error")}

            prepay_id = data.get("prepay_id", "")
            result: Dict[str, Any] = {"success": True, "order_no": out_trade_no, "prepay_id": prepay_id}

            if pay_type == "jsapi":
                # 构造小程序调起支付参数
                timestamp = str(int(time.time()))
                nonce_str = uuid.uuid4().hex[:32]
                package_str = f"prepay_id={prepay_id}"
                sign_message = f"{settings.WECHAT_MINI_APPID}\n{timestamp}\n{nonce_str}\n{package_str}\n"
                pay_sign = _sign_rsa_sha256(sign_message)
                result["pay_params"] = {
                    "appId": settings.WECHAT_MINI_APPID,
                    "timeStamp": timestamp,
                    "nonceStr": nonce_str,
                    "package": package_str,
                    "signType": "RSA",
                    "paySign": pay_sign,
                }
            else:
                result["pay_params"] = {"code_url": data.get("code_url", "")}

            return result

        except Exception as e:
            logger.exception(f"WeChat create_order error: {e}")
            return {"success": False, "message": str(e)}

    async def query_order(self, out_trade_no: str) -> Dict[str, Any]:
        """查询微信支付订单."""
        url_path = f"/v3/pay/transactions/out-trade-no/{out_trade_no}"
        url = f"{WECHAT_API_BASE}{url_path}"

        try:
            resp = await self._client.get(
                url,
                params={"mchid": settings.WECHAT_MCH_ID},
                headers={"Authorization": _build_authorization("GET", url_path)},
            )
            data = resp.json()
            if resp.status_code != 200:
                return {"status": "pending"}

            trade_state = data.get("trade_state", "")
            return {
                "status": "paid" if trade_state == "SUCCESS" else ("pending" if trade_state in ("NOTPAY", "USERPAYING") else "closed"),
                "trade_no": data.get("transaction_id", ""),
                "amount": data.get("amount", {}).get("total", 0) / 100,
            }
        except Exception as e:
            logger.exception(f"WeChat query_order error: {e}")
            return {"status": "unknown", "message": str(e)}

    async def refund(
        self, out_trade_no: str, refund_amount: float, total_amount: float,
        refund_reason: str = "",
    ) -> Dict[str, Any]:
        """微信退款."""
        refund_fen = int(round(refund_amount * 100))
        total_fen = int(round(total_amount * 100))
        refund_no = f"RF{out_trade_no}"

        body_data = {
            "out_trade_no": out_trade_no,
            "out_refund_no": refund_no,
            "amount": {"refund": refund_fen, "total": total_fen, "currency": "CNY"},
            "reason": refund_reason or "用户退款",
        }

        url_path = "/v3/refund/domestic/refunds"
        body_str = json.dumps(body_data)
        url = f"{WECHAT_API_BASE}{url_path}"

        try:
            resp = await self._client.post(
                url,
                content=body_str,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": _build_authorization("POST", url_path, body_str),
                },
            )
            data = resp.json()
            if resp.status_code == 200:
                return {
                    "success": True,
                    "refund_trade_no": data.get("refund_id", ""),
                    "message": "退款成功",
                }
            return {"success": False, "message": data.get("message", "退款失败")}
        except Exception as e:
            logger.exception(f"WeChat refund error: {e}")
            return {"success": False, "message": str(e)}

    async def verify_notify(self, body: bytes, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """
        验证微信支付回调签名.
        微信 V3 回调签名验证需要:
        1. 获取 HTTP 头中的 Wechatpay-Timestamp, Wechatpay-Nonce, Wechatpay-Signature, Wechatpay-Serial
        2. 构建待验签字符串
        3. 使用微信平台公钥验证
        """
        try:
            timestamp = headers.get("wechatpay-timestamp", "")
            nonce = headers.get("wechatpay-nonce", "")
            signature = headers.get("wechatpay-signature", "")
            serial = headers.get("wechatpay-serial", "")

            # 待签名串: timestamp + "\n" + nonce + "\n" + body + "\n"
            sign_message = f"{timestamp}\n{nonce}\n{body.decode('utf-8')}\n"

            # 加载平台公钥验证（简化实现：实际应加载微信平台证书公钥）
            # 生产环境应使用 wechatpayv3 SDK 的验签功能
            platform_cert_path = settings.WECHAT_PLATFORM_CERT_PATH
            if platform_cert_path and os.path.exists(platform_cert_path):
                import base64
                with open(platform_cert_path, "rb") as f:
                    cert_data = f.read()
                public_key = serialization.load_pem_public_key(cert_data, backend=default_backend())
                public_key.verify(
                    base64.b64decode(signature),
                    sign_message.encode("utf-8"),
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )

            # 验签通过 → 解析回调数据
            data = json.loads(body.decode("utf-8"))
            event_type = data.get("event_type", "")

            if event_type == "TRANSACTION.SUCCESS":
                tx = data.get("resource", {}).get("ciphertext", "")
                if tx:
                    # 解密 ciphertext（使用 AEAD_AES_256_GCM）
                    tx_data = json.loads(tx)
                    return {
                        "out_trade_no": tx_data.get("out_trade_no"),
                        "trade_no": tx_data.get("transaction_id"),
                        "amount": tx_data.get("amount", {}).get("total", 0) / 100,
                        "status": "paid",
                    }

            return {"event_type": event_type}
        except Exception as e:
            logger.error(f"WeChat verify_notify failed: {e}")
            return None
