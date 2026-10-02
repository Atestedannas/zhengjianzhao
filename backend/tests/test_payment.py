#!/usr/bin/env python3
"""
支付沙箱端到端测试脚本

此脚本用于验证微信支付和支付宝支付的完整流程：
1. 创建订单
2. 获取支付参数/二维码
3. 模拟支付回调（使用沙箱环境）

使用方法:
    1. 确保后端服务已启动: python -m app.main
    2. 确保已配置 .env 中的支付相关变量
    3. 运行测试: python tests/test_payment.py

注意:
    - 微信支付沙箱: 需要在微信支付商户平台申请沙箱账号
    - 支付宝沙箱: 使用支付宝开放平台沙箱环境，无需真实商户资质
    - 回调测试需要公网可达的 URL，或使用 ngrok 等内网穿透工具
"""

import asyncio
import json
import sys
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import httpx


# ======================== 配置 ========================
BASE_URL = os.getenv('TEST_BASE_URL', 'http://127.0.0.1:8000')
API_PREFIX = '/api/v1'

# 测试用户信息（需先在数据库中创建或通过微信登录获取）
TEST_USER_ACCESS_TOKEN = os.getenv('TEST_ACCESS_TOKEN', '')


class PaymentTester:
    """支付流程测试器."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    def _headers(self, access_token: str = None) -> dict:
        token = access_token or TEST_USER_ACCESS_TOKEN
        if token:
            return {'Authorization': f'Bearer {token}'}
        return {}

    # ======================== 微信支付测试 ========================
    async def test_wechat_payment(self) -> dict:
        """测试微信支付完整流程."""
        print('=' * 60)
        print('微信支付测试')
        print('=' * 60)

        results = []

        # 1. 创建支付订单
        print('\n[1/4] 创建微信支付订单...')
        order_data = {
            'amount': 0.01,  # 测试金额 0.01 元
            'pay_method': 'wechat_native',
            'description': '照片处理服务-测试订单',
        }
        try:
            resp = await self.client.post(
                f'{self.base_url}{API_PREFIX}/payment/orders',
                json=order_data,
                headers=self._headers(),
            )
            data = resp.json()
            print(f'  响应状态: {resp.status_code}')
            print(f'  响应内容: {json.dumps(data, ensure_ascii=False, indent=2)}')

            if resp.status_code == 200 and data.get('code') == 200:
                results.append({'step': '创建订单', 'status': 'PASS', 'order_no': data['data'].get('order_no')})

                order_no = data['data']['order_no']
                payment_params = data['data'].get('payment_params', {})

                # 2. 验证支付参数
                print(f'\n[2/4] 支付参数:')
                print(f'  订单号: {order_no}')
                if 'code_url' in payment_params:
                    print(f'  Native扫码URL: {payment_params["code_url"][:50]}...')
                print(f'  状态: {"PASS" if payment_params else "FAIL (无支付参数)"} ')
                results.append({'step': '支付参数', 'status': 'PASS' if payment_params else 'FAIL'})

                # 3. 查询订单状态
                print(f'\n[3/4] 查询订单状态...')
                query_resp = await self.client.get(
                    f'{self.base_url}{API_PREFIX}/payment/orders/{order_no}',
                    headers=self._headers(),
                )
                query_data = query_resp.json()
                print(f'  订单状态: {query_data.get("data", {}).get("status", "unknown")}')
                results.append({'step': '查询订单', 'status': 'PASS' if query_resp.status_code == 200 else 'FAIL'})

                # 4. 模拟支付回调
                # 注意：沙箱环境的回调需要公网可达 URL
                print(f'\n[4/4] 支付回调:')
                print('  注意: 沙箱回调需要公网可达 URL')
                print('  建议使用 ngrok 将本地 8000 端口暴露到公网')
                print('  或使用支付宝沙箱的"模拟回调"功能')
                results.append({'step': '支付回调', 'status': 'SKIP (需要公网URL)'})

            else:
                error_msg = data.get('message', '未知错误')
                print(f'  失败: {error_msg}')
                results.append({'step': '创建订单', 'status': f'FAIL: {error_msg}'})

        except Exception as e:
            print(f'  异常: {e}')
            results.append({'step': '创建订单', 'status': f'ERROR: {e}'})

        return {'test': '微信支付', 'results': results}

    # ======================== 支付宝支付测试 ========================
    async def test_alipay_payment(self) -> dict:
        """测试支付宝支付完整流程."""
        print('\n' + '=' * 60)
        print('支付宝支付测试')
        print('=' * 60)

        results = []

        # 1. 创建支付订单
        print('\n[1/4] 创建支付宝支付订单...')
        order_data = {
            'amount': 0.01,
            'pay_method': 'alipay',
            'description': '照片处理服务-测试订单',
        }
        try:
            resp = await self.client.post(
                f'{self.base_url}{API_PREFIX}/payment/orders',
                json=order_data,
                headers=self._headers(),
            )
            data = resp.json()
            print(f'  响应状态: {resp.status_code}')
            print(f'  响应内容: {json.dumps(data, ensure_ascii=False, indent=2)}')

            if resp.status_code == 200 and data.get('code') == 200:
                results.append({'step': '创建订单', 'status': 'PASS', 'order_no': data['data'].get('order_no')})

                order_no = data['data']['order_no']
                payment_params = data['data'].get('payment_params', {})

                # 2. 验证支付参数
                print(f'\n[2/4] 支付参数:')
                print(f'  订单号: {order_no}')
                if 'qr_code' in payment_params:
                    print(f'  支付宝付款码URL: {payment_params["qr_code"][:50]}...')
                print(f'  状态: {"PASS" if payment_params else "FAIL (无支付参数)"} ')
                results.append({'step': '支付参数', 'status': 'PASS' if payment_params else 'FAIL'})

                # 3. 查询订单状态
                print(f'\n[3/4] 查询订单状态...')
                query_resp = await self.client.get(
                    f'{self.base_url}{API_PREFIX}/payment/orders/{order_no}',
                    headers=self._headers(),
                )
                query_data = query_resp.json()
                print(f'  订单状态: {query_data.get("data", {}).get("status", "unknown")}')
                results.append({'step': '查询订单', 'status': 'PASS' if query_resp.status_code == 200 else 'FAIL'})

                # 4. 支付宝沙箱支持模拟回调
                print(f'\n[4/4] 支付回调:')
                print('  支付宝沙箱支持"模拟回调"功能')
                print('  访问: https://openhome.alipay.com/platform/appDaily.htm')
                print('  选择应用 -> 沙箱工具 -> 模拟回调')
                results.append({'step': '支付回调', 'status': 'SKIP (使用沙箱模拟)'})

            else:
                error_msg = data.get('message', '未知错误')
                print(f'  失败: {error_msg}')
                results.append({'step': '创建订单', 'status': f'FAIL: {error_msg}'})

        except Exception as e:
            print(f'  异常: {e}')
            results.append({'step': '创建订单', 'status': f'ERROR: {e}'})

        return {'test': '支付宝支付', 'results': results}

    # ======================== 健康检查 ========================
    async def check_service(self) -> bool:
        """检查后端服务是否运行."""
        print('检查后端服务...')
        try:
            resp = await self.client.get(f'{self.base_url}/api/v1/health')
            if resp.status_code == 200:
                print(f'  服务运行正常: {resp.json()}')
                return True
            else:
                print(f'  服务异常: {resp.status_code}')
                return False
        except Exception as e:
            print(f'  无法连接服务: {e}')
            print(f'  请确保后端服务已启动: cd backend && python -m app.main')
            return False


async def main():
    """主测试流程."""
    tester = PaymentTester(BASE_URL)

    try:
        # 检查服务
        if not await tester.check_service():
            print('\n❌ 后端服务未启动，请先启动服务')
            print('  启动命令: cd backend && python -m app.main')
            return

        # 检查 Token
        if not TEST_USER_ACCESS_TOKEN:
            print('\n⚠️  未设置 TEST_ACCESS_TOKEN 环境变量')
            print('  请先通过微信登录获取 access_token，或手动设置:')
            print('  $env:TEST_ACCESS_TOKEN="your_token_here"')
            print('  然后重新运行此脚本')
            return

        # 执行测试
        print('\n开始支付流程测试...\n')

        wechat_result = await tester.test_wechat_payment()
        alipay_result = await tester.test_alipay_payment()

        # 汇总
        print('\n' + '=' * 60)
        print('测试汇总')
        print('=' * 60)
        for test_result in [wechat_result, alipay_result]:
            print(f'\n{test_result["test"]}:')
            for r in test_result['results']:
                icon = '✅' if r['status'] == 'PASS' else '⚠️' if 'SKIP' in r['status'] else '❌'
                print(f'  {icon} {r["step"]}: {r["status"]}')

    finally:
        await tester.close()


if __name__ == '__main__':
    asyncio.run(main())