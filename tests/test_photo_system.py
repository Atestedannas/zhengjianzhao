#!/usr/bin/env python3
"""
照片处理系统自动化健康检查脚本
=================================
每天自动测试三个核心场景：
  1. 换背景（无美颜）— 验证背景是否真正更换为白色
  2. 单独美颜（轻度）— 验证美颜是否自然不抽象
  3. 换背景 + 美颜组合 — 验证组合效果是否正常

使用方法：
  python tests/test_photo_system.py

环境变量：
  PHOTO_SERVER_URL    - 服务地址（默认 http://119.91.157.252:8080）
  WEB_LOGIN_PASSWORD  - Web登录密码
  TEST_PHOTO_PATH     - 测试照片路径（默认 tests/test_photo.jpg）
  REPORT_DIR          - 报告输出目录（默认 tests/test_reports）
"""

import os
import sys
import json
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
import numpy as np
from PIL import Image

# ======================== 配置 ========================
SERVER_URL = os.getenv("PHOTO_SERVER_URL", "http://119.91.157.252:8081")
WEB_LOGIN_PASSWORD = os.getenv("WEB_LOGIN_PASSWORD", "photo2026")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123456")
TEST_PHOTO_PATH = os.getenv("TEST_PHOTO_PATH", os.path.join(os.path.dirname(__file__), "test_photo.jpg"))
REPORT_DIR = os.getenv("REPORT_DIR", os.path.join(os.path.dirname(__file__), "test_reports"))
API_PREFIX = "/api/v1"

# ======================== 分析阈值 ========================
# 背景白色判定：边缘平均RGB均 > 200
WHITE_BG_THRESHOLD = 200
# 模糊判定：拉普拉斯方差 < 100（值越小越模糊）
BLUR_THRESHOLD = 100
# 清晰判定：拉普拉斯方差 > 500
SHARP_THRESHOLD = 500


class PhotoSystemTester:
    """照片处理系统测试器."""

    def __init__(self):
        self.session = requests.Session()
        self.session.timeout = 60
        self.token = None
        self.results = []

    # ======================== 认证 ========================
    def admin_login(self) -> bool:
        """管理员登录，获取 admin token 用于设置无限次数."""
        try:
            resp = self.session.post(
                f"{SERVER_URL}{API_PREFIX}/admin/login",
                params={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
                timeout=15,
            )
            data = resp.json()
            if data.get("code") == 200:
                self.admin_token = data["data"]["access_token"]
                return True
            print(f"  [WARN] 管理员登录失败: {data.get('detail', data.get('message', '未知错误'))}")
            return False
        except requests.RequestException as e:
            print(f"  [WARN] 管理员登录异常: {e}")
            return False

    def ensure_unlimited_free_count(self) -> bool:
        """确保 Web 用户拥有无限免费次数."""
        try:
            # 使用 admin token 设置 Web 用户无限次数
            headers = {"Authorization": f"Bearer {self.admin_token}"}
            # 先获取 Web 用户信息
            resp = self.session.get(
                f"{SERVER_URL}{API_PREFIX}/user/me",
                timeout=15,
            )
            if resp.status_code != 200:
                print("  [WARN] 无法获取用户信息")
                return False
            user_data = resp.json()
            user_id = user_data.get("data", {}).get("id")
            if not user_id:
                print("  [WARN] 无法获取用户ID")
                return False

            print(f"  用户ID: {user_id}, 当前免费次数: {user_data['data'].get('free_count')}")

            # 如果已经是无限，跳过
            if user_data["data"].get("free_count") == -1:
                print("  ✅ 已是无限免费次数")
                return True

            # 设置无限免费次数
            resp = self.session.post(
                f"{SERVER_URL}{API_PREFIX}/admin/users/{user_id}/set-unlimited",
                headers=headers,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") == 200:
                print(f"  ✅ {data.get('message', '已设置无限免费次数')}")
                return True
            print(f"  [WARN] 设置无限次数失败: {data.get('detail', data.get('message', '未知错误'))}")
            return False
        except requests.RequestException as e:
            print(f"  [WARN] 设置无限次数异常: {e}")
            return False

    def login(self) -> bool:
        """Web 密码登录，获取 access_token."""
        try:
            resp = self.session.post(
                f"{SERVER_URL}{API_PREFIX}/auth/web-login",
                json={"password": WEB_LOGIN_PASSWORD},
                timeout=15,
            )
            data = resp.json()
            if data.get("code") == 200:
                self.token = data["data"]["access_token"]
                self.session.headers["Authorization"] = f"Bearer {self.token}"
                return True
            print(f"  [ERROR] 登录失败: {data.get('message', '未知错误')}")
            return False
        except requests.RequestException as e:
            print(f"  [ERROR] 登录请求异常: {e}")
            return False

    # ======================== 照片处理 ========================
    def process_photo(self, photo_path: str, **params) -> dict:
        """调用照片处理接口."""
        if not os.path.exists(photo_path):
            return {"code": -1, "message": f"测试照片不存在: {photo_path}"}

        try:
            with open(photo_path, "rb") as f:
                files = {"file": (os.path.basename(photo_path), f, "image/jpeg")}
                form_data = {
                    "beautify_level": 0,
                    "bg_color": "white",
                    "output_format": "JPEG",
                    "width": 0,
                    "height": 0,
                    "beautify_smooth": False,
                    "beautify_brighten": False,
                    "beautify_blemish": False,
                    **params,
                }
                resp = self.session.post(
                    f"{SERVER_URL}{API_PREFIX}/process/",
                    files=files,
                    data=form_data,
                    timeout=120,
                )
            return resp.json()
        except requests.RequestException as e:
            return {"code": -1, "message": f"请求异常: {e}"}

    def download_result(self, record_id: int) -> bytes | None:
        """下载处理结果图片."""
        try:
            resp = self.session.get(
                f"{SERVER_URL}{API_PREFIX}/process/{record_id}/download",
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.content
            print(f"  [WARN] 下载失败: HTTP {resp.status_code}")
            return None
        except requests.RequestException as e:
            print(f"  [WARN] 下载异常: {e}")
            return None

    # ======================== 图像分析 ========================
    def analyze_background(self, image_bytes: bytes) -> dict:
        """
        分析背景是否真正更换为白色.
        采样图片四边边缘像素，计算平均RGB值.
        """
        try:
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
            arr = np.array(img, dtype=np.float64)
            h, w = arr.shape[:2]

            # 采样边缘（每边取 5% 宽度的像素）
            edge_h = max(1, int(h * 0.05))
            edge_w = max(1, int(w * 0.05))

            top = arr[:edge_h, :, :].reshape(-1, 3)
            bottom = arr[-edge_h:, :, :].reshape(-1, 3)
            left = arr[:, :edge_w, :].reshape(-1, 3)
            right = arr[:, -edge_w:, :].reshape(-1, 3)

            edges = np.vstack([top, bottom, left, right])
            avg_color = edges.mean(axis=0)

            # 同时检查四个角（最容易出现背景残留）
            corners = np.vstack([
                arr[:edge_h, :edge_w, :].reshape(-1, 3),
                arr[:edge_h, -edge_w:, :].reshape(-1, 3),
                arr[-edge_h:, :edge_w, :].reshape(-1, 3),
                arr[-edge_h:, -edge_w:, :].reshape(-1, 3),
            ])
            corner_avg = corners.mean(axis=0)

            is_white_bg = bool(all(avg_color > WHITE_BG_THRESHOLD))
            is_clean_corners = bool(all(corner_avg > WHITE_BG_THRESHOLD))

            return {
                "avg_edge_rgb": [round(v, 1) for v in avg_color],
                "avg_corner_rgb": [round(v, 1) for v in corner_avg],
                "is_white_bg": is_white_bg,
                "is_clean_corners": is_clean_corners,
                "verdict": "PASS" if is_white_bg and is_clean_corners else "FAIL",
            }
        except Exception as e:
            return {"error": str(e), "verdict": "ERROR"}

    def analyze_sharpness(self, image_bytes: bytes) -> dict:
        """
        分析图片清晰度（是否模糊/抽象）.
        使用拉普拉斯方差法：方差越大越清晰，越小越模糊.
        """
        try:
            img = Image.open(BytesIO(image_bytes)).convert("L")
            # 缩放到合理尺寸以加速计算
            img.thumbnail((512, 512), Image.LANCZOS)
            arr = np.array(img, dtype=np.float64)

            # 拉普拉斯卷积核
            kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
            h, w = arr.shape
            laplacian = np.zeros_like(arr)
            for i in range(1, h - 1):
                for j in range(1, w - 1):
                    laplacian[i, j] = np.sum(arr[i - 1:i + 2, j - 1:j + 2] * kernel)

            variance = float(laplacian.var())
            is_blurry = variance < BLUR_THRESHOLD
            is_sharp = variance > SHARP_THRESHOLD

            if is_sharp:
                verdict = "PASS"
            elif is_blurry:
                verdict = "FAIL"
            else:
                verdict = "WARN"

            return {
                "laplacian_variance": round(variance, 2),
                "is_blurry": is_blurry,
                "is_sharp": is_sharp,
                "verdict": verdict,
            }
        except Exception as e:
            return {"error": str(e), "verdict": "ERROR"}

    def analyze_naturalness(self, original_bytes: bytes, beautified_bytes: bytes) -> dict:
        """
        分析美颜后是否自然.
        对比原始图和美颜图的差异，差异过大说明过度处理.
        """
        try:
            orig = Image.open(BytesIO(original_bytes)).convert("RGB")
            beauty = Image.open(BytesIO(beautified_bytes)).convert("RGB")

            # 缩放到相同尺寸
            orig.thumbnail((512, 512), Image.LANCZOS)
            beauty.thumbnail((512, 512), Image.LANCZOS)

            orig_arr = np.array(orig, dtype=np.float64)
            beauty_arr = np.array(beauty, dtype=np.float64)

            # 计算像素级差异
            diff = np.abs(beauty_arr - orig_arr)
            mean_diff = float(diff.mean())
            max_diff = float(diff.max())

            # 全图均值会被"人脸只占画面一小块"稀释：证件照里人脸往往不到画面 5%，
            # 只看全图均值会把正常美颜误判成"没效果"，
            # 也会把"同时换了背景"误判成"过度处理"。
            # 因此改为统计"被改动的像素"：
            #   changed_ratio —— 改动范围（正常美颜只覆盖皮肤，占比很小）
            #   changed_mean  —— 改动幅度（过大就是磨皮过度/假脸）
            changed = diff > 3.0
            changed_ratio = float(changed.mean())
            changed_mean = float(diff[changed].mean()) if bool(changed.any()) else 0.0

            # 自然美颜：改动集中在皮肤区域（通常远小于 50% 画面），
            # 改动幅度约 3~30 灰阶；范围过大或幅度过大即视为过度处理
            is_over_processed = bool(changed_ratio > 0.5 or changed_mean > 45.0)
            is_no_effect = bool(changed_ratio < 0.001 or changed_mean < 1.5)

            if is_no_effect:
                verdict = "WARN"  # 美颜无效果
            elif is_over_processed:
                verdict = "FAIL"  # 过度处理
            else:
                verdict = "PASS"

            return {
                "mean_pixel_diff": round(mean_diff, 2),
                "changed_ratio": round(changed_ratio, 4),
                "changed_mean_pixel_diff": round(changed_mean, 2),
                "max_pixel_diff": round(max_diff, 2),
                "is_over_processed": is_over_processed,
                "is_no_effect": is_no_effect,
                "verdict": verdict,
            }
        except Exception as e:
            return {"error": str(e), "verdict": "ERROR"}

    def save_result_image(self, image_bytes: bytes, name: str) -> str:
        """保存测试结果图片到报告目录."""
        os.makedirs(REPORT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(REPORT_DIR, f"{timestamp}_{name}.jpg")
        with open(path, "wb") as f:
            f.write(image_bytes)
        return path

    # ======================== 测试场景 ========================
    def test_bg_replace_only(self) -> dict:
        """测试1: 换背景（无美颜）."""
        print("\n" + "-" * 50)
        print("[测试1] 换背景-白色（美颜=OFF）")
        print("-" * 50)

        result = self.process_photo(
            TEST_PHOTO_PATH,
            beautify_level=0,
            bg_color="white",
            beautify_smooth=False,
            beautify_brighten=False,
            beautify_blemish=False,
        )

        if result.get("code") != 200:
            return {
                "test": "换背景(无美颜)",
                "status": "FAIL",
                "details": {"error": result.get("message", str(result))},
            }

        record_id = result["data"]["record_id"]
        img_data = self.download_result(record_id)
        if img_data is None:
            return {"test": "换背景(无美颜)", "status": "FAIL", "details": {"error": "无法下载结果"}}

        self.save_result_image(img_data, "bg_replace_only")

        bg_analysis = self.analyze_background(img_data)
        print(f"  边缘平均RGB: {bg_analysis.get('avg_edge_rgb', 'N/A')}")
        print(f"  四角平均RGB: {bg_analysis.get('avg_corner_rgb', 'N/A')}")
        print(f"  背景判定: {bg_analysis.get('verdict', 'ERROR')}")

        return {
            "test": "换背景(无美颜)",
            "status": bg_analysis.get("verdict", "ERROR"),
            "details": bg_analysis,
        }

    def test_beautify_only(self) -> dict:
        """测试2: 单独美颜（轻度）."""
        print("\n" + "-" * 50)
        print("[测试2] 单独美颜-轻度（背景=不变）")
        print("-" * 50)

        result = self.process_photo(
            TEST_PHOTO_PATH,
            beautify_level=1,
            beautify_smooth=True,
            beautify_brighten=True,
            beautify_blemish=True,
            # 关键：这一项是"单独美颜"，必须保持原背景，
            # 否则换背景会主导像素差异，自然度判定完全失真
            bg_color="keep",
        )

        if result.get("code") != 200:
            return {
                "test": "单独美颜",
                "status": "FAIL",
                "details": {"error": result.get("message", str(result))},
            }

        record_id = result["data"]["record_id"]
        img_data = self.download_result(record_id)
        if img_data is None:
            return {"test": "单独美颜", "status": "FAIL", "details": {"error": "无法下载结果"}}

        self.save_result_image(img_data, "beautify_only")

        # 分析清晰度
        sharpness = self.analyze_sharpness(img_data)
        print(f"  拉普拉斯方差: {sharpness.get('laplacian_variance', 'N/A')}")
        print(f"  清晰度判定: {sharpness.get('verdict', 'ERROR')}")

        # 分析自然度（与原始图对比）
        with open(TEST_PHOTO_PATH, "rb") as f:
            original = f.read()
        naturalness = self.analyze_naturalness(original, img_data)
        print(f"  全图像素差异均值: {naturalness.get('mean_pixel_diff', 'N/A')}")
        print(f"  改动像素占比: {naturalness.get('changed_ratio', 'N/A')} "
              f"改动像素平均幅度: {naturalness.get('changed_mean_pixel_diff', 'N/A')}")
        print(f"  自然度判定: {naturalness.get('verdict', 'ERROR')}")

        # 综合判定：清晰度PASS + 自然度PASS = 整体PASS
        if sharpness.get("verdict") == "PASS" and naturalness.get("verdict") == "PASS":
            status = "PASS"
        elif sharpness.get("verdict") == "FAIL" or naturalness.get("verdict") == "FAIL":
            status = "FAIL"
        else:
            status = "WARN"

        return {
            "test": "单独美颜",
            "status": status,
            "details": {
                "sharpness": sharpness,
                "naturalness": naturalness,
            },
        }

    def test_combined(self) -> dict:
        """测试3: 换背景+美颜组合."""
        print("\n" + "-" * 50)
        print("[测试3] 换背景+美颜组合")
        print("-" * 50)

        result = self.process_photo(
            TEST_PHOTO_PATH,
            beautify_level=1,
            bg_color="white",
            beautify_smooth=True,
            beautify_brighten=True,
            beautify_blemish=True,
        )

        if result.get("code") != 200:
            return {
                "test": "换背景+美颜组合",
                "status": "FAIL",
                "details": {"error": result.get("message", str(result))},
            }

        record_id = result["data"]["record_id"]
        img_data = self.download_result(record_id)
        if img_data is None:
            return {"test": "换背景+美颜组合", "status": "FAIL", "details": {"error": "无法下载结果"}}

        self.save_result_image(img_data, "combined")

        # 分析背景
        bg_analysis = self.analyze_background(img_data)
        print(f"  边缘平均RGB: {bg_analysis.get('avg_edge_rgb', 'N/A')}")
        print(f"  背景判定: {bg_analysis.get('verdict', 'ERROR')}")

        # 分析清晰度
        sharpness = self.analyze_sharpness(img_data)
        print(f"  拉普拉斯方差: {sharpness.get('laplacian_variance', 'N/A')}")
        print(f"  清晰度判定: {sharpness.get('verdict', 'ERROR')}")

        # 分析自然度
        with open(TEST_PHOTO_PATH, "rb") as f:
            original = f.read()
        naturalness = self.analyze_naturalness(original, img_data)
        print(f"  全图像素差异均值: {naturalness.get('mean_pixel_diff', 'N/A')}")
        print(f"  改动像素占比: {naturalness.get('changed_ratio', 'N/A')} "
              f"改动像素平均幅度: {naturalness.get('changed_mean_pixel_diff', 'N/A')}")
        print(f"  自然度(仅供参考，本项换背景不参与判定): {naturalness.get('verdict', 'ERROR')}")
        naturalness["note"] = "本项同时换了背景，像素差异被背景主导，不用于自然度判定"
        naturalness["verdict"] = "SKIP"

        # 综合判定：背景PASS + 清晰度PASS = 整体PASS
        # 注意：这一项同时换了背景，像素差异被背景主导，无法据此判断美颜是否自然；
        # 因此 naturalness 只作为信息输出，"美颜是否自然"由测试2（单独美颜）负责判定。
        checks = [
            bg_analysis.get("verdict"),
            sharpness.get("verdict"),
        ]
        if all(c == "PASS" for c in checks):
            status = "PASS"
        elif any(c == "FAIL" for c in checks):
            status = "FAIL"
        else:
            status = "WARN"

        return {
            "test": "换背景+美颜组合",
            "status": status,
            "details": {
                "background": bg_analysis,
                "sharpness": sharpness,
                "naturalness": naturalness,
            },
        }

    # ======================== 主流程 ========================
    def run(self) -> dict:
        """运行所有测试."""
        print("=" * 60)
        print(f"照片处理系统自动化测试")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"服务: {SERVER_URL}")
        print("=" * 60)

        # 检查测试照片
        if not os.path.exists(TEST_PHOTO_PATH):
            msg = f"测试照片不存在: {TEST_PHOTO_PATH}"
            print(f"\n❌ {msg}")
            return {"status": "ERROR", "message": msg}

        # 1. Web 登录（必须先登录才能调用接口）
        print("\n>>> Web 用户登录...")
        if not self.login():
            print("  ❌ Web 登录失败，无法继续测试")
            return {"status": "ERROR", "message": "Web 登录失败"}

        # 2. 管理员登录（可选，用于设置无限次数）
        print("\n>>> 管理员登录...")
        if self.admin_login():
            print("\n>>> 设置无限免费次数...")
            self.ensure_unlimited_free_count()
        else:
            print("  ⚠️ 管理员登录失败，跳过无限次数设置（可能影响免费次数耗尽后的测试）")

        # 4. 运行三个测试

        # 2. 运行三个测试
        self.results.append(self.test_bg_replace_only())
        time.sleep(1)  # 短暂间隔避免请求过快
        self.results.append(self.test_beautify_only())
        time.sleep(1)
        self.results.append(self.test_combined())

        # 3. 生成报告
        report = self.generate_report()
        return report

    def generate_report(self) -> dict:
        """生成JSON测试报告."""
        os.makedirs(REPORT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = os.path.join(REPORT_DIR, f"test_report_{timestamp}.json")

        summary = {
            "total": len(self.results),
            "pass": sum(1 for r in self.results if r["status"] == "PASS"),
            "fail": sum(1 for r in self.results if r["status"] == "FAIL"),
            "warn": sum(1 for r in self.results if r["status"] == "WARN"),
            "error": sum(1 for r in self.results if r["status"] == "ERROR"),
        }

        report = {
            "timestamp": datetime.now().isoformat(),
            "server": SERVER_URL,
            "results": self.results,
            "summary": summary,
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 打印摘要
        print("\n" + "=" * 60)
        print("测试报告")
        print("=" * 60)
        for r in self.results:
            icon_map = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "ERROR": "💥"}
            icon = icon_map.get(r["status"], "❓")
            print(f"  {icon} {r['test']}: {r['status']}")

        print(f"\n通过: {summary['pass']}/{summary['total']}")
        if summary["fail"] > 0:
            print(f"失败: {summary['fail']} — 请检查系统!")
        if summary["warn"] > 0:
            print(f"警告: {summary['warn']} — 效果可能不理想")
        print(f"\n报告: {report_path}")

        return report


def main():
    tester = PhotoSystemTester()
    report = tester.run()

    # 返回退出码
    if report.get("status") == "ERROR":
        sys.exit(2)
    summary = report.get("summary", {})
    if summary.get("fail", 0) > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()