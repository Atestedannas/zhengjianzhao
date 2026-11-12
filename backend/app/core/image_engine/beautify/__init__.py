"""证件照美颜引擎（按职责分卷）.

原实现是单文件 3633 行、177 个互相引用的顶层符号，且有 4 处前向依赖环、
6 个可变全局缓存、以及历史扫描脚本依赖的 `setattr(beautify, 常量, 值)`
动态改参数。若按常规 import 拆包，必须给几百处跨模块引用加前缀，并引入
中央注册表来打破依赖环，改动面过大且极易静默改变出图。

因此这里采用「包 + 分卷」：__init__.py 按 _PARTS 顺序把各分卷 exec 到
**同一个命名空间**（即本包的 globals）。效果上等价于原来的单文件，但物理上
按职责拆成 11 个可独立阅读/编辑的文件：

  01. 01_config.py               L   1-L 250  档位枚举 / _Params / 合规红线常量 / P0-P2 可调旋钮
  02. 02_levels.py               L 251-L 492  _Budget / 档位参数表 _PARAMS / _clamp_compliant 与强度缩放
  03. 03_detection.py            L 493-L 860  算子与模型配置 / ONNX 解析 / Haar 人脸框 / MediaPipe 关键点
  04. 04_primitives.py           L 861-L1025  基础数学与图像原语：_ramp / blur / guided filter / 尺度换算 / 色彩空间
  05. 05_masks.py                L1026-L1446  人脸几何 / 皮肤概率 / 区域蒙版 / 五官带 / 结构保护
  06. 06_tone.py                 L1447-L1722  分区肤色 / 纹理回注 / 快速导向滤波 / ITA 肤色分档
  07. 07_blemish.py              L1723-L2016  痘印斑点检测 / PatchMatch 填充 / 局部补丁修复
  08. 08_identity.py             L2017-L2318  ArcFace 身份相似度 / L0 平滑 / rolling guidance
  09. 09_retouch_support.py      L2319-L2717  Retinex 归一化 / 感知距离 / fallback 区域 / 自适应参数 / 局部 LAB 编辑
  10. 10_retouch_core.py         L2718-L3266  _retouch 主算子（高频/中频/色度/气色/锐化）/ 分阶段调试图
  11. 11_api.py                  L3267-L3633  对外入口 apply_beautify / apply_studio_beautify 与报告

好处：
  * 所有符号仍是 `beautify.<name>`（含 _xxx 私有符号），外部调用零改动；
  * 函数 globals 就是包 globals，动态 setattr / monkeypatch 依旧生效；
  * 前向依赖环不再是问题——所有分卷在入口被调用前已全部加载；
  * 分卷里除 3 处模型路径外没有任何引用改写，逐字节出图指纹可验证等价。

注意：`__file__` 现在指向本 __init__.py，比原文件深一级，故模型路径统一
由 _BACKEND_DIR（= backend/）提供，不再用 dirname(__file__) 链推算。
"""
from __future__ import annotations

import os
from pathlib import Path

# backend/ 目录：包路径比原 beautify.py 多一层，故用 parents[4]
_BACKEND_DIR = Path(__file__).resolve().parents[4]

# 加载顺序即依赖顺序（尽管共享命名空间，仍按「配置 → 原语 → 蒙版 → 算子 → 入口」排列，
# 让阅读顺序与执行链路一致）。
_PARTS = (
    "01_config.py",
    "02_levels.py",
    "03_detection.py",
    "04_primitives.py",
    "05_masks.py",
    "06_tone.py",
    "07_blemish.py",
    "08_identity.py",
    "09_retouch_support.py",
    "10_retouch_core.py",
    "11_api.py",
)

_HERE = Path(__file__).parent / "parts"
_NS = globals()
for _part in _PARTS:
    _path = _HERE / _part
    exec(compile(_path.read_text(encoding="utf-8"), str(_path), "exec"), _NS)

# 清掉加载器临时变量，避免污染 `dir(beautify)` / `from beautify import *`
del _HERE, _NS, _part, _path

__all__ = [k for k in list(globals()) if not k.startswith("__")]

# 兼容旧调用：`from app.core.image_engine.beautify import apply_beautify, BeautyLevel`
