"""背景替换模块 — BiRefNet ONNX 抠图 + 边缘精修 + 环境光融合 + 软投影.

相比旧方案 rembg/u2net_human_seg，BiRefNet 采用双边参考机制，
在头发丝、绒毛、半透明边缘上分割质量显著更优（CAAI AIR 2024）。
模型文件：backend/models/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx (214MB, MIT)
推理后端：onnxruntime CPU（无新增重依赖，离线运行）。

在 BiRefNet 之后额外做了四项「出图质量」处理（对应 PRD 二/三）：
    1. 引导滤波贴合真实轮廓        → 消除 1024→原图 的插值锯齿
    2. 形态学腐蚀割污染带          → 消除白边 / 黑边 / 色晕
    3. 发丝级高斯羽化 + 环境光融合 → 颈部肩部自然过渡，不再「硬切」
    4. 贴合轮廓的软投影            → 产生空间纵深，消除「剪纸漂浮感」
"""

import io
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np
from PIL import Image
from loguru import logger

from app.config import settings

# 标准证件照背景色映射（扩展版）
# "keep" 不是颜色：pipeline 见到 keep 直接跳过换底（保留原背景）；
# 放进 MAP 只是为了让 /bg-colors 接口把它下发到前端色板里。
BG_COLOR_MAP = {
    "keep":       (-1, -1, -1),      # 保留原背景（特殊值，pipeline 侧跳过换底）
    "white":      (255, 255, 255),   # 白色
    "blue":       (67, 142, 219),    # 标准蓝
    "red":        (213, 43, 30),     # 标准红
    "light_blue": (173, 216, 230),   # 浅蓝
    "dark_blue":  (25, 55, 128),     # 深蓝（护照蓝）
    "gray":       (192, 192, 192),   # 灰色
    "light_gray": (220, 220, 220),   # 浅灰
    "green":      (0, 128, 0),       # 绿色
    "pink":       (255, 192, 203),   # 粉色
    "cream":      (255, 253, 208),   # 米色
    "sky_blue":   (135, 206, 235),   # 天蓝
    "navy":       (0, 0, 128),       # 藏青
    "maroon":     (128, 0, 0),       # 深红
    "teal":       (0, 128, 128),     # 青色
    "lavender":   (230, 230, 250),   # 淡紫
    # ---- 扩展色板（2026-10：用户反馈「背景颜色太单调」）----
    "pearl_white": (245, 245, 245),  # 珍珠白（比纯白柔和，打印不刺眼）
    "wine_red":    (158, 31, 53),    # 酒红（部分考试/社保卡）
    "forest_green":(34, 85, 51),     # 墨绿
    "mint_green":  (199, 237, 204),  # 薄荷绿
    "rose":        (255, 228, 225),  # 玫瑰粉
    "champagne":   (247, 231, 206),  # 香槟米
    "dark_gray":   (96, 96, 96),     # 深灰

    # ---- 证件照标准色号（2026-10：按各机构实际审核要求整理）----
    # 【为什么要按标准色号收录，而不是「差不多就行」】
    # 报名系统的背景审核是按像素 RGB 卡阈值的（例如白底要求 ≥(250,250,250)），
    # 用户肉眼觉得「都是蓝色」的两种蓝，可能只有一种能过审。所以这里每个色
    # 都对应一个明确的使用场景，前端按分组展示并标注 HEX，用户按报考要求挑。
    #
    # 白底：身份证 / 护照 / 签证 / 驾驶证（最通用）
    "white_std":   (255, 255, 255),  # #FFFFFF 标准白
    # 蓝底：三种常见深浅，适用场景完全不同
    "blue_std":    (67, 142, 219),   # #438EDB 标准天蓝（简历/工牌/企业入职/毕业证）
    "blue_gov":    (0, 102, 204),    # #0066CC 考公深蓝（公务员/事业单位/四六级，饱和度更高）
    "blue_alt":    (29, 141, 213),   # #1D8DD5 蓝色变体（部分平台认证）
    "blue_cyan":   (0, 191, 243),    # #00BFF3 青蓝变体（特定证件采集）
    # 红底：标准正红 vs 深红，打印溢出风险不同
    "red_std":     (255, 0, 0),      # #FF0000 标准正红（结婚证/荣誉证书/党团材料）
    "red_deep":    (220, 20, 60),    # #DC143C 深红（党团关系转接/职业资格证，打印不易溢出）
    "red_crimson": (204, 0, 0),      # #CC0000 正深红（与 #DC143C 并列的常见深红写法）
    "red_alt":     (206, 0, 0),      # #CE0000 红色变体
    "red_dark":    (149, 2, 8),      # #950208 暗红变体（特定机构要求）
    # 灰底 / 浅蓝底：签证与学历采集专用
    "gray_light":  (240, 240, 240),  # #F0F0F0 浅灰（英国签证/高级职称评审/特定考试）
    "gray_mid":    (230, 230, 230),  # #E6E6E6 中浅灰
    "gray_deep":   (211, 211, 211),  # #D3D3D3 深一档浅灰
    "sky_light":   (100, 197, 255),  # #64C5FF 浅蓝（学信网/自考/成人高考专用）
}

# 色板分组与标准色号标注（/bg-colors 接口下发给前端做分组展示）.
# 前端按 group 渲染分组标题，按 label 显示中文名，按 hex 显示标准色号，
# 按 usage 显示「这个色用在哪」——用户是按报考公告里的 HEX 挑色的，
# 光看色块分辨不出 #438EDB 与 #0066CC。
BG_COLOR_META = {
    # ---- 白 ----
    "white":        {"group": "白", "label": "标准白",   "hex": "#FFFFFF",
                     "usage": "身份证/护照/签证/驾驶证，审核普遍要求 RGB≥250"},
    "white_std":    {"group": "白", "label": "标准白",   "hex": "#FFFFFF",
                     "usage": "身份证/护照/签证/驾驶证，审核普遍要求 RGB≥250"},
    "pearl_white":  {"group": "白", "label": "珍珠白",   "hex": "#F5F5F5",
                     "usage": "打印偏灰时的柔和白，非审核阈值白"},
    # ---- 蓝 ----
    "blue":         {"group": "蓝", "label": "标准天蓝", "hex": "#438EDB",
                     "usage": "简历/工牌/企业入职/毕业证，最通用的蓝底"},
    "blue_std":     {"group": "蓝", "label": "标准天蓝", "hex": "#438EDB",
                     "usage": "简历/工牌/企业入职/毕业证，最通用的蓝底"},
    "blue_gov":     {"group": "蓝", "label": "考公深蓝", "hex": "#0066CC",
                     "usage": "公务员/事业单位/四六级，饱和度要求更高的官方系统"},
    "blue_alt":     {"group": "蓝", "label": "蓝色变体", "hex": "#1D8DD5",
                     "usage": "部分平台认证、特定证件采集"},
    "blue_cyan":    {"group": "蓝", "label": "青蓝",     "hex": "#00BFF3",
                     "usage": "偏青的蓝底，部分平台认证使用"},
    "light_blue":   {"group": "蓝", "label": "浅蓝",     "hex": "#ADD8E6",
                     "usage": "偏淡的蓝底，观感柔和"},
    "sky_blue":     {"group": "蓝", "label": "天蓝",     "hex": "#87CEEB",
                     "usage": "偏浅的天蓝，非标准审核色"},
    "sky_light":    {"group": "蓝", "label": "学历浅蓝", "hex": "#64C5FF",
                     "usage": "学信网/自考/成人高考等学历采集专用"},
    "dark_blue":    {"group": "蓝", "label": "护照深蓝", "hex": "#193780",
                     "usage": "护照类深蓝，正式感强"},
    "navy":         {"group": "蓝", "label": "藏青",     "hex": "#000080",
                     "usage": "藏青深蓝，非证件照通用色"},
    # ---- 红 ----
    "red":          {"group": "红", "label": "标准红",   "hex": "#D52B1E",
                     "usage": "通用红底，视觉稳重"},
    "red_std":      {"group": "红", "label": "正红",     "hex": "#FF0000",
                     "usage": "结婚证/荣誉证书/入党入团材料，标准正红"},
    "red_deep":     {"group": "红", "label": "深红",     "hex": "#DC143C",
                     "usage": "党团关系转接/职业资格证，打印不易溢出"},
    "red_crimson":  {"group": "红", "label": "正深红",   "hex": "#CC0000",
                     "usage": "深红的另一种常见写法，打印更稳"},
    "red_alt":      {"group": "红", "label": "红色变体", "hex": "#CE0000",
                     "usage": "部分资料给出的红色参考值"},
    "red_dark":     {"group": "红", "label": "暗红",     "hex": "#950208",
                     "usage": "特定机构要求的暗红，随公告变化"},
    "wine_red":     {"group": "红", "label": "酒红",     "hex": "#9E1F35",
                     "usage": "部分考试/社保卡使用的酒红"},
    "maroon":       {"group": "红", "label": "深红褐",   "hex": "#800000",
                     "usage": "深红褐，非证件照通用色"},
    # ---- 灰 ----
    "gray_light":   {"group": "灰", "label": "浅灰",     "hex": "#F0F0F0",
                     "usage": "英国签证/高级职称评审/特定考试采集"},
    "gray_mid":     {"group": "灰", "label": "中浅灰",   "hex": "#E6E6E6",
                     "usage": "浅灰的另一档写法 (230,230,230)"},
    "gray_deep":    {"group": "灰", "label": "灰",       "hex": "#D3D3D3",
                     "usage": "深一档浅灰 (211,211,211)"},
    "gray":         {"group": "灰", "label": "中灰",     "hex": "#C0C0C0",
                     "usage": "通用中灰，非审核标准色"},
    "light_gray":   {"group": "灰", "label": "浅灰（旧）", "hex": "#DCDCDC",
                     "usage": "历史记录兼容色"},
    "dark_gray":    {"group": "灰", "label": "深灰",     "hex": "#606060",
                     "usage": "深灰，非证件照通用色"},
    # ---- 其他（历史记录兼容）----
    "keep":         {"group": "其他", "label": "保留原背景", "hex": "", "usage": ""},
    "custom":       {"group": "其他", "label": "自定义", "hex": "", "usage": ""},
    "green":        {"group": "其他", "label": "绿色",   "hex": "#008000", "usage": ""},
    "forest_green": {"group": "其他", "label": "墨绿",   "hex": "#225533", "usage": ""},
    "mint_green":   {"group": "其他", "label": "薄荷绿", "hex": "#C7EDCC", "usage": ""},
    "pink":         {"group": "其他", "label": "粉色",   "hex": "#FFC0CB", "usage": ""},
    "rose":         {"group": "其他", "label": "玫瑰粉", "hex": "#FFE4E1", "usage": ""},
    "cream":        {"group": "其他", "label": "米色",   "hex": "#FFFDD0", "usage": ""},
    "champagne":    {"group": "其他", "label": "香槟米", "hex": "#F7E7CE", "usage": ""},
    "teal":         {"group": "其他", "label": "青色",   "hex": "#008080", "usage": ""},
    "lavender":     {"group": "其他", "label": "淡紫",   "hex": "#E6E6FA", "usage": ""},
}

# 色板默认展示顺序（前端按此顺序分组；未列出的色排到最后）
BG_COLOR_GROUP_ORDER = ["白", "蓝", "红", "灰", "其他"]

# 证件照常用色板（前端默认展示的这批，按业务优先级排序）.
# 【为什么不是把 BG_COLOR_MAP 全量铺出来】后端保留 30+ 色值是给历史记录回显和
# 自定义取色用的；面板里全铺是「功能列表」而不是「可选答案」。这里只收录
# 有明确使用场景的标准色号（对照用户提供的《证件照常用背景色 RGB 参考》）：
# 白 1 / 蓝 5 / 红 5 / 灰 3，加「保留原背景」与「自定义」共 16 项，
# 按 白→蓝→红→灰 分组展示，每项标 HEX 与适用场景。
STANDARD_BG_COLORS = [
    "white",
    "blue_std", "blue_gov", "blue_alt", "blue_cyan", "sky_light",
    "red_std", "red_deep", "red_crimson", "red_alt", "red_dark",
    "gray_light", "gray_mid", "gray_deep",
]


def bg_color_group(name: str) -> str:
    """取某个色名所属的色调分组（白/蓝/红/灰/其他）.

    【为什么模板校验要按分组而不是按色名】模板的 allowed_bg_colors 里写的是
    组级别的意图（"blue" = 蓝底），而用户需要精确到 #438EDB / #0066CC。
    如果只认字面色名，用户选了考公深蓝就会被 400 拒绝；按分组放行后，
    「模板要蓝底 + 用户要考公深蓝」是对的，「模板要白底 + 用户要深蓝」仍然拒绝。
    """
    return BG_COLOR_META.get(name, {}).get("group", "其他")


def same_color_family(a: str, b: str) -> bool:
    """两个色名是否属于同一色调分组（用于模板兼容判定）."""
    ga, gb = bg_color_group(a), bg_color_group(b)
    return ga == gb and ga not in ("其他",)


def parse_bg_color(bg_color: str):
    """解析背景色参数 → (R,G,B) 或 None（keep）.

    支持两种形态：
      1. 预设色名（BG_COLOR_MAP 的 key）；
      2. 自定义十六进制 "custom:#RRGGBB"（前端色板取色器下发）。
    无法识别返回 "white" 对应的色值（保持旧版的兜底语义）。
    """
    if bg_color == "keep":
        return None
    if bg_color.startswith("custom:"):
        hexpart = bg_color.split(":", 1)[1].lstrip("#")
        try:
            if len(hexpart) == 6:
                return (int(hexpart[0:2], 16), int(hexpart[2:4], 16), int(hexpart[4:6], 16))
        except ValueError:
            pass
        logger.warning(f"Invalid custom bg_color '{bg_color}', defaulting to white.")
        return BG_COLOR_MAP["white"]
    if bg_color not in BG_COLOR_MAP:
        logger.warning(f"Unknown bg_color '{bg_color}', defaulting to white.")
        bg_color = "white"
    return BG_COLOR_MAP[bg_color]

# BiRefNet 模型文件名（backend/models/ 目录）
BIRefNET_MODEL_FILE = "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx"
# 轻量兜底模型：输入 320x320，单次推理峰值只有几百 MB
LITE_MODEL_FILE = "u2net_human_seg.onnx"

# 每个引擎的「模型文件 → 推理输入边长」。
# 注意：两个模型的输入形状都是导出时写死的（BiRefNet 1024 / u2net 320），
# 不能靠改代码降分辨率，所以内存不够时唯一的办法是换轻量模型。
_ENGINE_MODELS = {
    "birefnet": (BIRefNET_MODEL_FILE, 1024),
    "u2net": (LITE_MODEL_FILE, 320),
}

# 每个引擎「输出的是 logits 还是概率」——这是模型本身的属性，必须显式声明。
#   birefnet：输出 logits（实测本机 -22 ~ +121，必须 sigmoid）
#   u2net   ：d0 是图内已 sigmoid 的概率（实测 0.0000 ~ 1.0000，不能再 sigmoid）
# 旧代码不区分引擎，只靠「值域是否越界」猜 —— 概率图只要有一个 -1e-7 的浮点噪声
# 就会被当成 logits 再过一次 sigmoid，人物 alpha 从 1.0 掉到 0.73、背景从 0 升到
# 0.50，换红底直接得到「紫红背景 + 整体泛红的人物」，即用户截图的现象。
_ENGINE_OUTPUT_KIND = {
    "birefnet": "logits",
    "u2net": "prob",
}

# ImageNet 归一化参数（BiRefNet 与 u2net_human_seg 用的是同一套）
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)


@dataclass
class _ModelSession:
    """一个已加载的 onnxruntime session 及其输入/输出元信息.

    不能像以前那样把 input_name/output_name 挂到 _get_session 函数对象上 ——
    现在缓存里同时会有两个模型，挂函数属性会互相覆盖。
    """
    path: str
    engine: str
    size: int
    sess: object
    input_name: str
    output_name: str


def _intra_op_threads() -> int:
    """算子内并行线程数：限制线程数可以同时压低峰值内存与 CPU 争抢."""
    configured = int(getattr(settings, "ORT_INTRA_OP_THREADS", 0) or 0)
    if configured > 0:
        return configured
    return max(1, min(4, os.cpu_count() or 2))


def _meminfo() -> dict:
    """读 /proc/meminfo（单位 MB）；读不到返回空 dict."""
    vals = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    try:
                        vals[parts[0][:-1]] = int(parts[1]) // 1024
                    except ValueError:
                        pass
    except OSError:
        return {}
    return vals


def available_memory_mb() -> Optional[int]:
    """读取当前**可分配**内存（MB）= MemAvailable + 可用 Swap；读不到返回 None.

    【为什么要把 swap 算进来】BiRefNet 单次推理峰值 RSS ≈ 3.1GB，而线上机器
    物理内存只有 3.7GB。只看 MemAvailable（永远到不了 3.6GB 阈值）的结果就是
    「永远降级到 u2net」—— 这正是线上从未用上 BiRefNet 的直接原因。

    内核判断 OOM 用的是「物理内存 + swap 是否还有可用页」，不是「物理内存够不够」。
    既然 swap 也能承载匿名页（推理过程的中间张量就是匿名页），就该把它计入可分配
    总量，否则代码的判据比内核更保守，白白放弃画质。

    代价：超出物理内存的部分会落到 swap 上，推理明显变慢（实测 56~61 秒）。
    这个代价是用户明确接受的（预算 1~3 分钟），换来 BiRefNet 的发丝级抠图。
    """
    mi = _meminfo()
    if not mi:
        return None
    avail = mi.get("MemAvailable")
    if avail is None:
        return None
    swap_free = max(0, mi.get("SwapFree", 0) or 0)
    return int(avail + swap_free)


def physical_memory_mb() -> Optional[int]:
    """物理内存总量（MB），仅用于日志/诊断（不含 swap）."""
    total = _meminfo().get("MemTotal")
    return int(total) if total else None


def resolve_engine(available_mb: Optional[int] = None) -> str:
    """决定本次抠图用哪个引擎.

    auto 模式下可分配内存（含 swap）低于阈值就用轻量模型 —— 宁可质量略降，
    也不能让一个 3.1GB 的推理把整台小机器拖进 OOM。

    available_mb 仅用于测试注入；不传时读 /proc/meminfo。
    读不到（非 Linux）时按 BiRefNet 处理。
    """
    mode = (getattr(settings, "BG_ENGINE", "auto") or "auto").strip().lower()
    if mode in ("birefnet", "u2net", "off"):
        return mode
    if mode != "auto":
        logger.warning(f"BG_ENGINE='{mode}' 不是合法值，按 auto 处理")

    threshold = int(getattr(settings, "BIREFNET_MIN_AVAILABLE_MB", 3600) or 3600)
    avail = available_memory_mb() if available_mb is None else available_mb
    if avail is None or avail >= threshold:
        return "birefnet"

    logger.warning(
        f"可分配内存（含 swap）{avail}MB < 阈值 {threshold}MB，抠图自动降级为轻量模型 "
        f"{LITE_MODEL_FILE}（320x320）以避免 OOM；加内存或扩 swap 后会恢复 BiRefNet"
    )
    return "u2net"


def _model_candidates(model_name: str) -> list:
    """返回模型候选搜索路径（项目 models 目录 / 容器内 /app/models / rembg 下载目录）.

    统一按「去掉扩展名再补 .onnx」处理：调用方传带不带 .onnx 都能命中，
    否则会出现 ~/.u2net/u2net_human_seg.onnx.onnx 这种找不到的路径。
    """
    stem = model_name[:-5] if model_name.endswith(".onnx") else model_name
    backend_models = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "models",
    )
    return [
        os.path.join(backend_models, f"{stem}.onnx"),          # backend/models/（本地开发）
        os.path.join("/app/models", f"{stem}.onnx"),           # 容器内模型目录
        os.path.expanduser(f"~/.u2net/{stem}.onnx"),           # Dockerfile COPY 的位置
        os.path.join("/root/.u2net", f"{stem}.onnx"),
        os.path.join(os.getcwd(), f"{stem}.onnx"),
        os.path.join(os.getcwd(), "models", f"{stem}.onnx"),
    ]


def _resolve_model_path(model_name: str):
    """按候选路径查找模型文件，返回存在的第一个绝对路径，找不到返回 None."""
    return next((p for p in _model_candidates(model_name) if os.path.exists(p)), None)


@lru_cache(maxsize=4)
def _get_session(model_path: str, engine: str, size: int) -> _ModelSession:
    """模块级缓存 onnxruntime session（session 可并发复用）."""
    import onnxruntime as ort

    logger.info(f"Loading {engine} model session from: {model_path} (input {size}x{size})")
    # 动态选择可用 provider（CPU 兜底，有 GPU 自动加速）
    available = set(ort.get_available_providers())
    providers = [p for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider", "CPUExecutionProvider")
                 if p in available]
    if not providers:
        providers = ["CPUExecutionProvider"]

    opts = ort.SessionOptions()
    # 【内存治理关键项】关掉 CPU memory arena：
    # 默认开启时推理过程中分配过的峰值内存会被 arena 长期缓存、不还给操作系统，
    # 在 4GB 以下的小机器上会把进程直接撑到被内核 OOM 杀掉（gunicorn SIGKILL → nginx 502）。
    opts.enable_cpu_mem_arena = False
    opts.enable_mem_pattern = False
    opts.intra_op_num_threads = _intra_op_threads()
    try:
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    except AttributeError:  # 老版本 onnxruntime 没有该枚举
        pass

    sess = ort.InferenceSession(model_path, sess_options=opts, providers=providers)
    info = _ModelSession(
        path=model_path,
        engine=engine,
        size=size,
        sess=sess,
        input_name=sess.get_inputs()[0].name,
        output_name=sess.get_outputs()[0].name,
    )
    logger.info(
        f"{engine} model ready: input={info.input_name} output={info.output_name} "
        f"threads={opts.intra_op_num_threads} arena=off"
    )
    return info


# 概率图判定容差：模型直接输出概率时，浮点误差允许 ±该值仍视为概率
_PROB_TOL = 0.05
# 判定为 logits 的下界：极差超过这个值就一定不是概率图
_LOGIT_RANGE = 1.5


def normalize_mask(mask: np.ndarray, engine: Optional[str] = None) -> np.ndarray:
    """把模型原始输出归一化成 0~1 概率图（幂等，可安全重复调用）.

    【为什么不能用 `if mask.max()>1 or mask.min()<0` 判断】
    u2net_human_seg 的 d0 是**图内已经 sigmoid 过**的概率图，取值范围本来就是
    [0,1]；导出/推理的浮点误差会让个别像素落到 -1e-7 或 1.0000001。
    旧判据只要撞上这一个像素，就认为「这是 logits」，于是把整张概率图
    **再过一次 sigmoid**：

        sigmoid(0.00) = 0.500   ← 背景 alpha（本该是 0）
        sigmoid(1.00) = 0.731   ← 人物 alpha（本该是 1）

    合成 out = α·人物 + (1-α)·背景色 之后：
        背景 = 0.50×旧背景 + 0.50×新背景  → 蓝底换红底得到**紫红**，而不是红
        人物 = 0.73×人物 + 0.27×新背景    → 整张脸、头发、衣服**整体泛红**
    这就是用户截图里「换成红色背景时人物区域也被修改了」的成因。

    【判定策略：声明优先 + 数值兜底】
      1. 引擎声明 prob，且数值确实落在 [0,1] 区间 → 只 clip（正确路径）；
      2. 引擎声明 prob 但数值明显越界（-20~+120 这种）→ 说明该导出确实是 logits，
         仍然做 sigmoid（声明写错也不至于把蒙版剪成空白）；
      3. 引擎声明 logits → 直接 sigmoid；
      4. 未知引擎 → 按整体分布判断：1%/99% 分位落在 [-tol, 1+tol] 且极差 < 1.5
         视为概率图，否则视为 logits。
    幂等：概率图再进一次仍然只 clip，不会被二次 sigmoid。
    """
    m = np.asarray(mask, dtype=np.float32)
    lo = float(np.percentile(m, 1.0))
    hi = float(np.percentile(m, 99.0))
    looks_like_prob = (lo >= -_PROB_TOL and hi <= 1.0 + _PROB_TOL
                       and (hi - lo) <= _LOGIT_RANGE)
    kind = _ENGINE_OUTPUT_KIND.get(engine or "")

    if kind == "logits":
        return _sigmoid(m)
    if kind == "prob" and looks_like_prob:
        return np.clip(m, 0.0, 1.0)
    if kind is None and looks_like_prob:
        return np.clip(m, 0.0, 1.0)

    # 走到这里：声明是 logits、声明 prob 但数值越界、或未知引擎且不像概率图
    return _sigmoid(m)


def _sigmoid(m: np.ndarray) -> np.ndarray:
    """数值稳定的 sigmoid（float64 计算，大负数不会溢出）."""
    out = 1.0 / (1.0 + np.exp(-m.astype(np.float64)))
    return np.clip(out, 0.0, 1.0).astype(np.float32)


# ============================================================ 推理互斥锁
# 【为什么必须有这把锁】BiRefNet 的 ONNX 输入写死 1x3x1024x1024，实测单次推理
# 峰值 RSS ≈ 3.1GB（2026-10 线上实测 3125MB，耗时 56~61 秒）。4GB 内存的机器
# 上**并发两次**必然被内核 OOM 杀掉（表现为 gunicorn worker 收 SIGKILL → 502，
# 或 celery 任务半途消失 → 记录永久卡在 processing）。
#
# 现状：celery worker 用 -c 1 串行，所以默认是安全的。但那是**部署参数**给的
# 保证，不是代码的保证 —— 谁把 -c 调成 2、或以后把抠图挪回 web 进程
# （gunicorn -w 2 就是两个进程），就会静默踩爆。所以代码层再加一道：
# 同一进程内模型推理严格串行，后来者等锁而不是一起往上冲。
#
# 注意这是「进程内」锁（threading.Lock 不跨进程）。跨进程安全仍依赖
# 「只有 celery worker 这一个进程做抠图」这个部署事实，见 compose 注释。
_INFER_LOCK = threading.Lock()


def _infer_alpha(image: Image.Image, engine: str) -> np.ndarray:
    """用指定引擎推理前景 alpha 蒙版（进程内串行，见 _INFER_LOCK）.

    返回:
        np.ndarray: float32, 0~1, 尺寸与原图一致 (H, W)，1=前景 0=背景.
    """
    import cv2

    model_file, size = _ENGINE_MODELS[engine]
    model_path = _resolve_model_path(model_file)
    if model_path is None:
        raise FileNotFoundError(
            f"Model file not found. Looked in: {_model_candidates(model_file)}. "
            f"Please place {model_file} in backend/models/."
        )

    with _INFER_LOCK:
        info = _get_session(model_path, engine, size)

        # 预处理：RGB 归一化 → resize 到模型输入边长
        # （两个模型的输入形状都是导出时写死的，喂错尺寸 onnxruntime 会直接报错）
        rgb = np.array(image.convert("RGB"), dtype=np.float32) / 255.0          # HWC
        rgb_resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_LINEAR)
        chw = np.transpose(rgb_resized, (2, 0, 1))[None, ...]                    # 1,3,H,W
        x = np.ascontiguousarray((chw - _MEAN) / _STD, dtype=np.float32)

        # 只取第一个输出（u2net 家族的第 0 个输出就是融合后的 d0，
        # 其余 d1~d6 用不到，onnxruntime 会按需裁剪不参与计算）
        out = info.sess.run([info.output_name], {info.input_name: x})[0]         # 1,1,size,size

        mask = np.squeeze(out)                                                   # size,size
        # 按引擎声明归一化：u2net 的概率图绝不能再 sigmoid（见 normalize_mask 说明）
        mask = normalize_mask(mask, engine)

        # 缩回原图尺寸作为 alpha（放锁内：这步 cv2 大数组也是峰值内存的一部分）
        h, w = image.size[1], image.size[0]
        alpha = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
        alpha = np.clip(alpha, 0.0, 1.0).astype(np.float32)

        # 显式释放中间张量：小内存机器上这几百 MB 回来的很及时
        del x, chw, rgb_resized, rgb, out, mask
        return alpha


def _run_alpha(image: Image.Image) -> np.ndarray:
    """按配置与当前可用内存选择抠图引擎（auto 模式下内存不足自动降级）."""
    return _infer_alpha(image, resolve_engine())


# ============================================================ 边缘精修 / 光影融合
# 对应 PRD《二、抠图与边缘融合规范》《三、背景色彩与光影规范》
#
# 旧实现直接把 BiRefNet 输出的 alpha 贴到纯色底上，产生三类问题：
#   1. alpha 由 1024 插值回原图，边缘带锯齿 → 发丝像被剪掉
#   2. 边缘 1~3px 是「前景 × 旧背景」的混合污染带 → 白边/黑边/色晕
#   3. 人物与背景无任何光影交互 → 剪纸般漂浮在色块上
# 下面四步逐一解决。

# 腐蚀：向内收，割掉污染带
_EDGE_ERODE_RATIO = 0.0015
_EDGE_ERODE_MIN = 1
_EDGE_ERODE_MAX = 4
# 羽化：向外渐变，得到发丝级透明过渡而非二值切边
_EDGE_FEATHER_RATIO = 0.0012
_EDGE_FEATHER_MIN = 1.0
_EDGE_FEATHER_MAX = 3.0
# ---- 高调底（白/浅灰）专用「收紧」参数 ----
# 【为什么高调底要单独一套】深底上的软边是**加分项**：蓝/红底会把 alpha 过渡带
# 融进同色调的暗部，看起来是自然的镜头景深。同一圈软边放到白底上就变成废片：
# 背景 255、头发边缘 240~250 的灰带会被眼睛读成「白雾 / 发光 / 没抠干净」。
#
# 【实测数据（1680×2240 蓝底原片，单人半身）】轮廓过渡带（luma 246→60 的横向像素）：
#     现状（引导半径 0.006·短边≈10px + 羽化 2px）→ 中位 12px
#     收紧（0.003·短边≈5px + 羽化 1.2px + 过渡带压缩 0.55）→ 中位 4.5px
# 半透明带（0.01<α<0.5）像素数 48659 → 14095（-71%），也就是那圈「灰雾」
# 少了七成；背景四角 254 → 255，发丝根数肉眼无减少，缩到 295×413 一寸后无锯齿。
#
# 深底（蓝/红/灰/绿）继续走原来的柔边参数 —— 收紧会让红底出现剪刀切边。
_EDGE_TIGHT_GUIDE_RATIO = 0.0030
_EDGE_TIGHT_FEATHER_RATIO = 0.0007
_EDGE_TIGHT_FEATHER_MIN = 1.0
_EDGE_TIGHT_FEATHER_MAX = 1.4
# 过渡带压缩强度：0=不压缩，0.55=把 [0.02,0.98] 的斜坡往 0/1 两端推（实测最佳）
_EDGE_TIGHT_BAND_COMPRESS = 0.55
# 高调底判定阈值：相对亮度 ≥ 该值的底色按「高调底」处理（收边 + 弱投影 + 弱环境光）
_HIGH_KEY_LUMA = 235.0
# 环境光：过渡带混入背景色的强度（0.28 = 明显可见但不脏）
_AMBIENT_STRENGTH = 0.28
# 软投影
_SHADOW_OPACITY = 0.16
_SHADOW_BLUR_RATIO = 0.012
_SHADOW_BLUR_MIN = 6.0
_SHADOW_BLUR_MAX = 28.0
_SHADOW_OFFSET_RATIO = 0.004
# 去色污启用门槛：旧背景逐通道 std 低于此值才算「纯色底」。
# 实测：蓝底 2.1 / 红底 1.6 / 白底 3.0（可解）；户外实拍原片 73.1（不可解）。
# 门槛取 6：留出 JPEG 压缩噪声与轻微渐变棚拍底的余量，又远低于实拍纹理。
_FLAT_BG_STD = 6.0


def _box_filter(src: np.ndarray, radius: int) -> np.ndarray:
    """O(1) 均值滤波（引导滤波基元）."""
    import cv2
    k = radius * 2 + 1
    return cv2.boxFilter(src, ddepth=-1, ksize=(k, k), normalize=True,
                         borderType=cv2.BORDER_REFLECT)


def _guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """引导滤波：以原图结构引导 alpha，保发丝、去插值锯齿."""
    mean_i = _box_filter(guide, radius)
    mean_p = _box_filter(src, radius)
    var_i = _box_filter(guide * guide, radius) - mean_i * mean_i
    cov_ip = _box_filter(guide * src, radius) - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    return _box_filter(a, radius) * guide + _box_filter(b, radius)


def _is_high_key(target_rgb) -> bool:
    """是否「高调底」：白底与接近白的浅灰（相对亮度 ≥ _HIGH_KEY_LUMA）.

    【高调底与深底为什么必须分开处理】背景只是一层底色，但眼睛对「边缘对比」
    的解读完全不同：

      * 深底（蓝 67,142,219 / 红 213,43,30）：人物剪影比背景暗，alpha 过渡带
        里那几十个中间值像素落在暗部，读起来是镜头景深，是加分项。
      * 高调底（白 255 / 浅灰 240）：背景 255，过渡带 240~250 的灰带对比度
        只有 5~15 级，眼睛读成「一层白雾、头发在发光、没抠干净」，也就是
        用户说的「白色底不自然」。

    所以高调底要额外做三件事（三件都不做在深底上）：
      1. 收紧 alpha 过渡带（_refine_alpha 的 tight 模式）；
      2. 不加环境光（否则白发丝会描一圈更亮的白光）；
      3. 不加软投影（255 被压暗一级就是可见灰影，且审核要求 ≥250）。
    第 1 条是本轮修复的核心：实测把过渡带从 12px 收到 4.5px。
    """
    r, g, b = (float(c) for c in target_rgb)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return luma >= _HIGH_KEY_LUMA


def _compress_alpha_band(alpha: np.ndarray, strength: float) -> np.ndarray:
    """压缩 alpha 过渡带：把 [0.02,0.98] 的斜坡往 0/1 两端推（幂函数族，可逆）.

    【为什么不用 S 曲线 / smoothstep】那两者的斜率在端点趋于 0，会把羽化带
    压成一段**平台**（视觉上是「一条等宽的灰线」），比原来的软边更假。
    这里用「过半程加速」的幂函数：a<0.5 走 (a/0.5)^(1+3s)·0.5，
    a>0.5 镜像对称。端点斜率 = 1+3s > 1，过渡带变陡但仍是连续的，
    半透明像素（发丝真正需要的中间值）不会被抹成 0 或 1。

    strength=0 时原样返回；0.55 实测把 12px 坡收到 4.5px，发丝根数不减。
    """
    if strength <= 0.0:
        return alpha.astype(np.float32)
    s = float(strength)
    a = np.clip(alpha, 0.0, 1.0).astype(np.float32)
    exp = 1.0 + 3.0 * s
    lo = np.power(np.clip(a / 0.5, 0.0, 1.0), exp) * 0.5
    hi = 1.0 - np.power(np.clip((1.0 - a) / 0.5, 0.0, 1.0), exp) * 0.5
    return np.where(a < 0.5, lo, hi).astype(np.float32)


def _refine_alpha(alpha: np.ndarray, rgb: np.ndarray, *,
                  high_key: bool = False) -> np.ndarray:
    """Alpha 边缘精修四步（PRD 二.发丝级抠图 / 颈部肩部过渡）.

    1. 引导滤波：以原图灰度为导向，把 1024→原图的插值锯齿贴合真实轮廓
    2. 形态学腐蚀：向内收 1~4px，割掉「前景与旧背景混合」的污染带
    3. 高斯羽化：向外 1~3px 渐变，得到发丝级透明过渡
    4. **内部不透明化**（_solidify_core）：把「人物内部却不等于 1」的 alpha 拉满

    high_key=True 时（白底/浅灰底）额外做两件事，见 _is_high_key：
        * 引导半径 0.006→0.003 短边、羽化 1~3px→1~1.4px；
        * 过渡带压缩 _EDGE_TIGHT_BAND_COMPRESS。
    深底一律走原参数（收紧会让红/蓝底出现剪刀切边）。

    【第 4 步为什么必须有】低分辨率引擎（u2net 320×320 升采样）或概率图被压扁时，
    人物躯干/脸颊内部会大面积停在 α≈0.6~0.8。此时合成公式
        out = α·人物 + (1-α)·背景色
    会让**人物内部**掺进 20%~40% 的背景色 —— 换红底就是整张脸、整件衣服泛红，
    背景也因为 α 不到 1 而变成「红压蓝」的紫红。这不是边缘问题，是内部问题，
    羽化/腐蚀都救不了，只能在内部把 alpha 顶到 1。
    做法：取 α>0.5 的二值前景，向内腐蚀一个羽化半径得到「核心区」，再把核心区
    的 alpha 用平滑权重补到 1 —— 只动真正的内部，边缘过渡带（发丝）保持原样。
    """
    import cv2

    h, w = alpha.shape[:2]
    short = min(h, w)

    gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    g_ratio = _EDGE_TIGHT_GUIDE_RATIO if high_key else 0.006
    g_radius = max(2, int(round(short * g_ratio)))
    a_ref = np.clip(_guided_filter(gray, alpha.astype(np.float32), g_radius, 1e-4), 0.0, 1.0)

    erode_px = max(_EDGE_ERODE_MIN, min(_EDGE_ERODE_MAX, int(round(short * _EDGE_ERODE_RATIO))))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1,) * 2)
    a_ref = cv2.erode(a_ref, k, iterations=1)

    if high_key:
        feather_px = float(min(_EDGE_TIGHT_FEATHER_MAX,
                               max(_EDGE_TIGHT_FEATHER_MIN,
                                   short * _EDGE_TIGHT_FEATHER_RATIO)))
    else:
        feather_px = float(min(_EDGE_FEATHER_MAX, max(_EDGE_FEATHER_MIN, short * _EDGE_FEATHER_RATIO)))
    a_ref = cv2.GaussianBlur(a_ref, (0, 0), feather_px)
    a_ref = np.clip(a_ref, 0.0, 1.0).astype(np.float32)

    # 4) 内部不透明化：只把「离轮廓足够远」的区域顶到 1
    core = _core_weight(a_ref, feather_px)
    a_ref = np.clip(a_ref + (1.0 - a_ref) * core, 0.0, 1.0).astype(np.float32)

    # 5) 高调底：压缩过渡带（放在内部不透明化之后，避免被 core 权重拉回原坡）
    if high_key:
        a_ref = _compress_alpha_band(a_ref, _EDGE_TIGHT_BAND_COMPRESS)
        a_ref = np.clip(a_ref + (1.0 - a_ref) * core, 0.0, 1.0).astype(np.float32)
    return a_ref


def _core_weight(alpha: np.ndarray, feather_px: float) -> np.ndarray:
    """人物「内部核心区」的平滑权重（0=过渡带/背景，1=确定是人物内部）.

    二值前景 → 向内腐蚀 2 个羽化半径 → 高斯模糊做软过渡。
    腐蚀量取羽化半径的 2 倍：保证核心区边界落在真实轮廓**内侧**，
    核心区的权重变化不会碰到发丝/衣领的真实半透明像素。
    """
    import cv2

    core = (alpha > 0.5).astype(np.float32)
    er = max(2, int(round(feather_px * 2.0)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (er * 2 + 1,) * 2)
    core = cv2.erode(core, k, iterations=1)
    if core.max() <= 0.0:
        return core
    core = cv2.GaussianBlur(core, (0, 0), max(1.0, er * 0.5))
    return np.clip(core, 0.0, 1.0).astype(np.float32)


def _old_bg_field(rgb: np.ndarray, alpha: np.ndarray) -> tuple:
    """估计「原背景色场」（空间可变的旧底色）、可信度，以及「旧底是否纯色」.

    【为什么需要它】换底不是「把背景涂成新色」这么简单。过渡带里的每个像素
    都是**合成结果**，而不是纯前景：

        C = F·α + B_old·(1-α)

    其中 F 是真实前景（头发丝、衣领），B_old 是旧背景（本例是蓝底）。
    直接把 C 贴到新背景上，等于把旧蓝底一起搬过去 —— 白底上就会看到一圈
    蓝灰色脏边（实测 B-R = +17.5，肉眼非常明显）。这就是「白底换不干净」。

    正确做法是先反解出 F（color decontamination / unpremultiply），
    再用 F 去和新背景合成。

    【为什么用归一化卷积而不是取一个全局色】旧照片的背景可能是渐变棚拍底
    （上浅下深），全局单一色会在渐变区域估错。这里只在 α≈0 的「确定是背景」
    像素上采样，再做高斯归一化卷积，得到一个平滑的、随位置变化的底色场。

    返回 (bg_field, confidence, is_flat)：
        bg_field   (H,W,3) 旧背景色估计
        confidence (H,W)   该位置估计的可信度（附近有背景像素→1，远处→0）
        is_flat    bool    旧背景是否为「纯色底」（std<6）

    【is_flat 为什么重要】去色污的前提是「旧底是一个可解的纯色」。
    实测两组输入的背景标准差差了一个数量级：
        蓝底证件照  std=2.1   红底 std=1.6   白底 std=3.0   → 纯色底，可解
        户外实拍原片 std=73.1  （树、天空、草地）          → 不可解
    对实拍原片套用「单一底色反解」会把树叶/天空的纹理当成污染抹掉，
    反而劣化（实测 B-R 由 +3.4 恶化到 +7.4）。所以只在纯色底上启用。
    """
    import cv2

    known = (alpha < 0.02).astype(np.float32)
    h, w = alpha.shape[:2]
    if known.sum() < 16:
        # 几乎没有背景像素（满幅人像）：退回全图均值，可信度 0，不做去污
        fallback = rgb.reshape(-1, 3).mean(axis=0)
        return (np.broadcast_to(fallback.reshape(1, 1, 3), (h, w, 3)).astype(np.float32),
                np.zeros((h, w), dtype=np.float32),
                False)

    # 旧底是否纯色：只看确定是背景的像素
    bg_px = rgb[known > 0]
    is_flat = bool(bg_px.std(axis=0).mean() < _FLAT_BG_STD)

    sigma = max(2.0, min(h, w) * 0.02)
    # 归一化卷积：分子/分母都做高斯，等价于「只用已知背景像素做加权平均」
    num = cv2.GaussianBlur(rgb * known[..., None], (0, 0), sigma)
    den = cv2.GaussianBlur(known, (0, 0), sigma)
    bg_field = num / np.maximum(den, 1e-6)[..., None]
    # den 越小说明附近没有背景像素，估计不可信
    confidence = np.clip(den / max(float(known.mean()), 1e-6), 0.0, 1.0).astype(np.float32)
    return bg_field.astype(np.float32), confidence, is_flat


def _decontaminate(rgb: np.ndarray, alpha: np.ndarray, bg_field: np.ndarray,
                   confidence: np.ndarray) -> np.ndarray:
    """从过渡带像素里反解出「不含旧背景」的真实前景色（去色污）.

        F = (C - B_old·(1-α)) / α

    只在 α 处于中间段（真正发生混合的地方）才值得做：
      * α≈1（人物内部）：C 本来就是 F，(1-α)≈0，公式自然退化为 C，无需干预；
      * α≈0（纯背景）：分母趋 0，不可解，必须跳过。

    所以权重取 band = 4α(1-α)（在 α=0.5 处最大），再乘旧底色估计的可信度。
    α 下界夹到 0.05 防止除爆；结果裁剪回 0~255，避免反解溢出产生亮斑。
    """
    a = np.clip(alpha, 0.05, 1.0)[..., None]
    fg = (rgb - bg_field * (1.0 - alpha[..., None])) / a

    band = np.clip(alpha * (1.0 - alpha) * 4.0, 0.0, 1.0)
    wt = (band * confidence)[..., None]
    out = rgb * (1.0 - wt) + np.clip(fg, 0.0, 255.0) * wt
    return np.clip(out, 0.0, 255.0)


def _blend_ambient(rgb: np.ndarray, alpha: np.ndarray, target_rgb,
                   strength: Optional[float] = None) -> np.ndarray:
    """边缘环境光融合（PRD 二.衣物处理 / 三.环境光统一）.

    在 alpha 过渡带（0<α<1）混入目标背景色，模拟环境光在颈部/肩部/发丝
    边缘的微弱反射。既抑制旧背景残留的溢色（蓝底发白、红底发暗），
    又让人物不再是「贴在色块上的剪纸」。

    strength 由 _ambient_strength_for(target_rgb) 给出：纯白底为 0
    （白底混色会让深色头发描上白边），不传则用默认 _AMBIENT_STRENGTH。
    """
    s = _AMBIENT_STRENGTH if strength is None else float(strength)
    if s <= 0.0:
        return rgb.astype(np.float32)
    band = np.clip(alpha * (1.0 - alpha) * 4.0, 0.0, 1.0)[..., None]
    ambient = band * s
    target = np.array(target_rgb, dtype=np.float32).reshape(1, 1, 3)
    return rgb.astype(np.float32) * (1.0 - ambient) + target * ambient


def _is_pure_white(target_rgb) -> bool:
    """是否「证件照纯白底」：判定标准是三个通道都 ≥250.

    【为什么白底要单独走一套策略】证件照白底的审核是按像素卡阈值的
    （普遍要求背景 RGB ≥ (250,250,250)），而背景合成里有两个效果天生会
    把背景压暗、把边缘弄脏：

      1. 软投影：把背景乘以 (1-shadow)。sigma 随图幅缩放（1680px 短边时
         是 20px），灰雾从人物轮廓扩散到 120px 外，背景从 255 掉到 223。
         这在蓝/红底上是「空间纵深」，在白底上就是「一圈脏灰」。
      2. 环境光：把目标底色混进 alpha 过渡带。白底会把纯白混进深色头发，
         发丝边缘平均被提亮 R+25.8 —— 看起来像描了一圈白边（发白的光晕）。
         蓝底因为接近头发色调反而看不出来。

    所以白底走「干净优先」：不投影、不加环境光，背景严格保持 255。
    深色底（蓝/红/灰/绿）保留投影与环境光，观感更有立体感。
    """
    r, g, b = (float(c) for c in target_rgb)
    return r >= 250.0 and g >= 250.0 and b >= 250.0


def _shadow_strength_for(target_rgb) -> float:
    """按背景亮度决定投影强度：浅底要弱，深底才敢用满.

    【为什么必须按底色缩放】投影是把背景**压暗**（bg *= 1-shadow）。
    同一个不透明度 0.16，在不同底色上的绝对落差差一个数量级：

        白底 255 → 214（Δ=41 级，肉眼一眼可见的灰边）
        蓝底 (67,142,219) → 最暗 (56,119,184)（Δ≈11~35，柔和）
        红底 (213,43,30) → 最暗 (179,36,25)（Δ≈34，尚可）

    证件照白底要求「纯白干净、打印不透灰」，41 级的灰边就是用户说的
    「白色背景换不对」——看起来像没换干净、留了一圈脏影。
    所以按背景相对亮度线性缩放：白底约 0.28 倍（Δ≈11 级，仅作空间暗示），
    深底保持原强度。

    纯白底（三通道 ≥250）直接返回 0：审核要求 RGB≥250，任何压暗都可能踩线。
    浅灰底（#F0F0F0 这类高调底）压到 0.25 倍：Δ 从 13 级降到 3 级，
    刚好留一点「人站在纸上」的落点，但不会形成可见灰环。
    """
    if _is_pure_white(target_rgb):
        return 0.0
    r, g, b = (float(c) for c in target_rgb)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b      # 0~255
    # 亮底(255) → 0.28；暗底(<=90) → 1.0；中间线性过渡
    t = max(0.0, min(1.0, (luma - 90.0) / (255.0 - 90.0)))
    op = _SHADOW_OPACITY * (1.0 - 0.72 * t)
    if _is_high_key(target_rgb):
        op *= 0.25
    return op


def _ambient_strength_for(target_rgb) -> float:
    """按底色决定环境光强度：纯白底不混色，高调底很弱，其他底保持原强度.

    见 _is_pure_white 的说明：白底混色会让深色头发描上一圈白边。
    浅灰（#F0F0F0）也接近白，但它不是审核卡阈值的那种白，
    保持较弱的环境光即可（0.4 倍），仍能压掉旧背景残留的溢色。
    高调底的过渡带已经被 _refine_alpha 收紧，环境光再强就会在窄带里
    堆出「一圈亮描边」，所以统一压到 0.3 倍。
    """
    if _is_pure_white(target_rgb):
        return 0.0
    if _is_high_key(target_rgb):
        return _AMBIENT_STRENGTH * 0.3
    return _AMBIENT_STRENGTH


def _drop_shadow(alpha: np.ndarray, opacity: Optional[float] = None) -> np.ndarray:
    """人物背后的软投影（PRD 三.投影与层次）.

    复用人物 alpha 轮廓 → 大幅高斯模糊 → 下移几像素，
    得到贴合轮廓的自然投影（比硬椭圆真实得多），产生空间纵深。

    opacity 由 _shadow_strength_for(target_rgb) 按底色亮度给出，
    不传则用默认 _SHADOW_OPACITY（测试/兼容旧调用）。
    """
    import cv2

    h, w = alpha.shape[:2]
    short = min(h, w)
    blur = float(min(_SHADOW_BLUR_MAX, max(_SHADOW_BLUR_MIN, short * _SHADOW_BLUR_RATIO)))
    sh = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), blur)

    dy = int(round(short * _SHADOW_OFFSET_RATIO))
    if dy > 0:
        sh = np.roll(sh, dy, axis=0)
        sh[:dy, :] = 0.0          # 清掉 roll 的回绕部分
    op = _SHADOW_OPACITY if opacity is None else float(opacity)
    return np.clip(sh * op, 0.0, 1.0)


def _audit_composition(out: np.ndarray, alpha: np.ndarray, target_rgb,
                       src_rgb: np.ndarray) -> dict:
    """出图自检：确认「背景是目标色」且「人物内部没被染色」.

    这两个指标正是用户唯一能一眼看出来的两件事：
      1. 背景偏色 —— 选红底却得到紫红/暗红（alpha 没到 1，旧背景透出来）
      2. 人物染色 —— 脸和衣服跟着泛红（人物内部 alpha 不到 1，背景色掺进去）

    实现成纯统计，不抛异常（出图不能因为自检失败就中断），
    结果写进日志；测试里直接断言返回的 dict。

    【核心区怎么取】不能只用 `alpha > 0.98` —— 恰恰在「染色 bug」发生的场景下，
    人物内部 alpha 只有 0.73，`alpha>0.98` 会是空集，自检直接失明（分母为 0 就报 0）。
    所以核心区改成**几何法**：α>0.5 的二值前景向内腐蚀若干像素，
    这样无论 alpha 是否到 1，都能框住「离轮廓足够远的内部像素」，
    再把该区域的实际出图与源图对比，染色无处可藏。
    """
    import cv2

    h, w = alpha.shape[:2]
    corner = max(2, min(h, w) // 40)
    patches = [
        out[:corner, :corner], out[:corner, -corner:],
        out[-corner:, :corner], out[-corner:, -corner:],
    ]
    bg_meas = np.mean([p.reshape(-1, 3).mean(axis=0) for p in patches], axis=0)
    bg_delta = float(np.max(np.abs(bg_meas - np.array(target_rgb, dtype=np.float32))))

    # 几何核心区：二值前景 → 向内腐蚀 2% 短边（足够远离羽化带）
    fg = (alpha > 0.5).astype(np.uint8)
    er = max(2, int(round(min(h, w) * 0.02)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (er * 2 + 1,) * 2)
    core = cv2.erode(fg, k, iterations=1) > 0
    core_px = int(core.sum())
    if core_px > 0:
        # 人物内部相对源图的偏移：正常应≈0（只有压缩/量化噪声）
        shift = out[core].astype(np.float32) - src_rgb[core]
        core_shift = float(np.mean(np.abs(shift)))
        core_bias = np.mean(shift, axis=0).round(1).tolist()
    else:
        core_shift = 0.0
        core_bias = [0.0, 0.0, 0.0]

    return {
        "bg_measured": bg_meas.round(1).tolist(),
        "bg_target": list(target_rgb),
        "bg_delta": round(bg_delta, 1),
        "core_px": core_px,
        "core_shift": round(core_shift, 2),
        "core_bias": core_bias,
    }


def replace_background(
    image: Image.Image,
    bg_color: str,
    *,
    refine_edge: bool = True,
    ambient: bool = True,
    shadow: bool = True,
    decontaminate: bool = True,
) -> Image.Image:
    """AI 抠图后合成纯色背景（BiRefNet + 边缘精修 + 光影融合）.

    处理链（对应 PRD 二/三）:
        BiRefNet → 概率图归一化 → alpha 引导滤波 → 腐蚀去污染带 → 羽化发丝过渡
        → 内部不透明化 → **去色污（反解旧背景）** → 环境光融合（去色晕/防漂浮）
        → 人物软投影 → 纯色背景合成 → 出图自检（背景色准 / 人物零染色）

    - bg_color: 'keep' 跳过（保持原背景）
    - 其他值从 BG_COLOR_MAP 取色
    - 抠图引擎由 settings.BG_ENGINE 决定（auto 时按可用内存自动降级，见 resolve_engine）
    返回合成后的 RGB 图片.
    """
    if bg_color == "keep":
        logger.debug("Background kept as-is.")
        return image.convert("RGB") if image.mode != "RGB" else image

    engine = resolve_engine()
    if engine == "off":
        logger.warning(f"BG_ENGINE=off：跳过 AI 抠图，保留原背景（bg_color={bg_color}）")
        return image.convert("RGB") if image.mode != "RGB" else image

    target_rgb = parse_bg_color(bg_color)
    if target_rgb is None:  # keep（防御：开头的 keep 分支已拦截，这里再兜一次）
        return image.convert("RGB") if image.mode != "RGB" else image

    alpha = _infer_alpha(image, engine)
    rgb = np.array(image.convert("RGB"), dtype=np.float32)

    # 高调底（白/浅灰）与深底走两套边缘策略，见 _is_high_key：
    # 深底的软边是景深，高调底的软边是「白雾/发光」。判定只看目标底色，
    # 与图幅、肤色无关，所以放在最前面算一次。
    high_key = _is_high_key(target_rgb)

    # 1) 边缘精修：去锯齿、去污染带、发丝级羽化、内部不透明化
    if refine_edge:
        alpha = _refine_alpha(alpha, rgb, high_key=high_key)

    # 1.5) 去色污：过渡带像素是「前景×α + 旧背景×(1-α)」的混合结果，
    #      不反解出纯前景就直接贴新底，会把旧底色一起搬过去 ——
    #      白底上表现为一圈蓝灰脏边（实测 B-R=+17.5），即「白底换不干净」。
    if decontaminate:
        bg_field, conf, is_flat = _old_bg_field(rgb, alpha)
        if is_flat:
            fg_clean = _decontaminate(rgb, alpha, bg_field, conf)
            dc_note = "on"
        else:
            # 旧底不是纯色（户外实拍/复杂场景）：单一底色反解会把树叶天空的
            # 纹理当污染抹掉，反而劣化，所以跳过。
            fg_clean = rgb
            dc_note = "skip(非纯色底)"
    else:
        fg_clean = rgb
        dc_note = "off"

    # 2) 环境光融合：过渡带混入背景色（纯白底强度为 0，见 _ambient_strength_for）
    ambient_op = _ambient_strength_for(target_rgb)
    fg = (_blend_ambient(fg_clean, alpha, target_rgb, ambient_op)
          if ambient else fg_clean)

    # 3) 纯色背景
    h, w = alpha.shape[:2]
    bg = np.empty((h, w, 3), dtype=np.float32)
    bg[...] = np.array(target_rgb, dtype=np.float32)

    # 4) 软投影先落在背景上（随后被人物遮住大部分，仅在底部/两侧露出）
    #    强度按底色亮度缩放；纯白底为 0 —— 证件照白底要求 RGB≥250，
    #    任何压暗都可能踩审核线，且视觉上会变成「一圈脏灰」。
    shadow_op = _shadow_strength_for(target_rgb)
    if shadow and shadow_op > 0.0:
        sa = _drop_shadow(alpha, shadow_op)[..., None]
        bg = bg * (1.0 - sa)

    # 5) 合成
    a3 = alpha[..., None]
    out = np.clip(fg * a3 + bg * (1.0 - a3), 0.0, 255.0).astype(np.uint8)

    edge_px = int(np.sum((alpha > 0.02) & (alpha < 0.98)))
    band_px = int(np.sum((alpha > 0.01) & (alpha < 0.5)))
    audit = _audit_composition(out, alpha, target_rgb, rgb)
    logger.info(
        f"Background replaced (engine={engine} + edge refine): {bg_color} RGB={target_rgb} "
        f"high_key={high_key} edge_px={edge_px} halo_px={band_px} "
        f"ambient={ambient} shadow={shadow} decon={dc_note} "
        f"shadow_op={shadow_op:.3f} ambient_op={ambient_op:.3f} | "
        f"背景实测={audit['bg_measured']} Δ={audit['bg_delta']} "
        f"人物核心={audit['core_px']}px 偏移={audit['core_shift']}"
    )
    # 自检不合格只在日志里点名，不阻断出图：这正是旧版本「红底出紫图」能被发现的地方
    if audit["bg_delta"] > 12.0:
        logger.warning(
            f"背景色偏差过大 Δ={audit['bg_delta']}：实测 {audit['bg_measured']} "
            f"目标 {audit['bg_target']} —— alpha 未覆盖全背景，旧背景透出来了"
        )
    if audit["core_shift"] > 8.0:
        logger.warning(
            f"人物内部被背景色污染 偏移={audit['core_shift']} 偏置={audit['core_bias']} "
            f"—— 人物内部 alpha 不足 1，换底会让人脸/衣服整体染色"
        )
    # 白底专项自检：审核普遍要求背景 RGB ≥ 250，这里按真实出图统计
    if _is_pure_white(target_rgb):
        pure = alpha < 0.001
        if pure.any():
            worst = float(out[pure].min())
            if worst < 250:
                logger.warning(
                    f"白底最暗像素 {worst} < 250，可能不满足审核要求"
                    f"（应无投影/无环境光，请检查 shadow_op/ambient_op）"
                )
    return Image.fromarray(out)
