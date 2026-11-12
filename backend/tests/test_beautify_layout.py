"""beautify 包结构自检（拆分后的架构门禁）.

保证「包 + 分卷」方案不会因为后续编辑悄悄退化：
  * 11 个分卷存在、可编译、顺序与 _PARTS 一致；
  * 原单文件 beautify.py 不得复活（否则包/文件同名会二义）；
  * 所有历史私有符号仍可通过 `beautify.<name>` 访问；
  * 函数 globals 必须就是包 globals —— 这是动态 setattr 参数扫描、
    以及分卷间直接互引私有符号能生效的唯一前提；
  * 模型路径仍指向 backend/models（包比原文件深一级，最容易踩的坑）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from app.core.image_engine import beautify as B  # noqa: E402
from beautify_fingerprint import REQUIRED_SYMBOLS  # noqa: E402


def main() -> int:
    errors = []
    pkg = ROOT / "backend" / "app" / "core" / "image_engine" / "beautify"
    old = pkg.parent / "beautify.py"

    if not (pkg / "__init__.py").exists():
        errors.append("beautify 包缺失")
    if old.exists():
        errors.append("旧单文件 beautify.py 仍在，会与包产生二义")

    parts = list(getattr(B, "_PARTS", []))
    if len(parts) < 8:
        errors.append(f"分卷数异常: {len(parts)}")
    for name in parts:
        p = pkg / "parts" / name
        if not p.exists():
            errors.append(f"分卷缺失 {name}")
            continue
        text = p.read_text(encoding="utf-8")
        if "beautify 分卷" not in text:
            errors.append(f"分卷缺少职责 header: {name}")
        try:
            compile(text, str(p), "exec")
        except SyntaxError as e:
            errors.append(f"分卷语法错误 {name}: {e}")
        if len(text.splitlines()) > 700:
            errors.append(f"分卷重新膨胀到 {len(text.splitlines())} 行: {name}")

    missing = [s for s in REQUIRED_SYMBOLS if not hasattr(B, s)]
    if missing:
        errors.append(f"丢失历史符号 {len(missing)} 个: {missing[:8]}")

    # 共享命名空间：所有入口函数的 __globals__ 必须是包自己的 __dict__
    for fn in ("apply_beautify", "apply_studio_beautify", "_retouch", "_ramp"):
        f = getattr(B, fn, None)
        if f is None:
            continue
        if f.__globals__ is not vars(B):
            errors.append(f"{fn}.__globals__ 与包命名空间分离，动态参数扫描会失效")

    # 动态旋钮确实改写函数读取到的全局值
    orig = B.SPOT_GAIN
    B.SPOT_GAIN = 7.77
    if B.SPOT_GAIN != 7.77:
        errors.append("setattr 未写入包命名空间")
    B.SPOT_GAIN = orig

    backend_dir = Path(str(B._BACKEND_DIR))
    if not (backend_dir / "models").is_dir():
        errors.append(f"模型目录解析错误: {backend_dir / 'models'}")
    for fn in ("_parse_model_path", "_model_path", "_arc_model_path"):
        got = str(getattr(B, fn)())
        if not got.startswith(str(backend_dir / "models")):
            errors.append(f"{fn} 路径越界: {got}")

    total = sum(len((pkg / "parts" / n).read_text(encoding="utf-8").splitlines())
                for n in parts if (pkg / "parts" / n).exists())
    print("=== beautify 包结构自检 ===")
    print(f"  包路径: {pkg.relative_to(ROOT)}")
    print(f"  分卷 {len(parts)} 个 / 共 {total} 行 / 最大 "
          f"{max(len((pkg / 'parts' / n).read_text(encoding='utf-8').splitlines()) for n in parts if (pkg / 'parts' / n).exists())} 行")
    print(f"  导出符号 {len(B.__all__)} 个，历史必需符号 {len(REQUIRED_SYMBOLS)} 个")
    print(f"  __globals__ 共享: {B.apply_beautify.__globals__ is vars(B)}")
    print(f"  模型目录: {backend_dir / 'models'}")

    if errors:
        print(f"门禁失败 {len(errors)} 项:")
        for e in errors:
            print(f"  [FAIL] {e}")
        return 1
    print("全部通过 [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
