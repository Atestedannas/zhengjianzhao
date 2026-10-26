"""白底模板覆盖回归测试（纯逻辑，不需要数据库/HTTP）.

【问题】用户选「白底」，实际出的是蓝底/红底。

根因在 backend/app/api/v1/process.py 的模板参数覆盖：

    bg_color: str = Form("white")                    # 默认值就是 "white"
    ...
    bg_color = colors[0] if bg_color == "white" else bg_color

默认值 "white" 让代码无法区分「用户主动选了白底」和「用户没传这个字段」，
于是把两种都当成「没指定」，一律替换成模板的 colors[0]：
    教师招聘2寸  allowed=[blue, white]      → 选白底却出蓝底
    征兵照片     allowed=[red, white, blue] → 选白底却出红底

修复：默认值改为空串 ""，用空串表达「没指定」；"white" 作为合法选择原样保留。

运行：
    python backend/tests/test_bg_white_template.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_PROCESS_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "api", "v1", "process.py",
)


def _read_src() -> str:
    with open(_PROCESS_PY, "r", encoding="utf-8") as f:
        return f.read()


def _resolve(requested: str, colors: list) -> str:
    """复刻 process.py 里的背景色决策（保持与源码同语义）."""
    req = (requested or "").strip()
    if req.lower() in ("", "default", "auto"):
        req = ""
    if req and not req.startswith("custom:") and req not in colors:
        raise ValueError(f"unsupported: {req}")
    return req or colors[0]


def test_form_default_is_empty():
    """Form 默认值必须是空串，不能用 "white"（否则无法区分「没传」和「选了白」）."""
    src = _read_src()
    m = re.search(r'bg_color:\s*str\s*=\s*Form\(\s*"([^"]*)"\s*\)', src)
    assert m, "没找到 bg_color 的 Form 声明"
    default = m.group(1)
    assert default == "", (
        f'bg_color 的 Form 默认值是 "{default}"，必须为空串。'
        f'默认 "white" 会导致用户明确选白底时被模板 colors[0] 覆盖。'
    )
    print(f'  [OK] Form 默认值: bg_color = Form("{default}")')


def _code_only() -> str:
    """去掉注释行，只留真实代码（注释里会引用旧写法做说明）."""
    lines = []
    for line in _read_src().splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        # 去掉行尾注释（只处理简单情况：字符串外的 #）
        if "#" in line:
            in_str = False
            quote = ""
            cut = len(line)
            for i, ch in enumerate(line):
                if in_str:
                    if ch == quote:
                        in_str = False
                elif ch in ("'", '"'):
                    in_str = True
                    quote = ch
                elif ch == "#":
                    cut = i
                    break
            line = line[:cut]
        lines.append(line)
    return "\n".join(lines)


def test_no_white_special_casing():
    """不得再出现 `colors[0] if bg_color == "white"` 这类把白底当未指定的写法."""
    src = _code_only()
    bad = re.search(r'colors\[0\]\s*if\s*bg_color\s*==\s*"white"', src)
    assert bad is None, f"仍存在把白底当未指定的写法: {bad.group(0) if bad else ''}"
    # 正确写法应当是 requested_bg or colors[0]
    assert re.search(r'bg_color\s*=\s*requested_bg\s+or\s+colors\[0\]', src), (
        "未找到正确写法 `bg_color = requested_bg or colors[0]`"
    )
    print("  [OK] 无白底特判，改用 requested_bg or colors[0]")


def test_white_is_preserved_under_template():
    """模板模式下，用户明确选 white 必须原样保留（这是本次 bug 的核心）."""
    cases = [
        ("教师招聘2寸", ["blue", "white"], "white", "white"),
        ("征兵照片", ["red", "white", "blue"], "white", "white"),
        ("公务员1寸", ["white", "blue"], "white", "white"),
        ("教师资格证", ["white", "blue"], "white", "white"),
        # 其他色也必须保留
        ("教师招聘2寸", ["blue", "white"], "blue", "blue"),
        ("征兵照片", ["red", "white", "blue"], "red", "red"),
        ("征兵照片", ["red", "white", "blue"], "blue", "blue"),
    ]
    for name, colors, req, expect in cases:
        got = _resolve(req, colors)
        assert got == expect, f"{name}: 选 {req} 得到 {got}（应为 {expect}）"
        print(f"  [OK] {name:10s} allowed={colors} 选 {req:6s} → {got}")


def test_unspecified_falls_back_to_template_default():
    """没指定时仍然用模板首色（保持旧行为，不能把模板默认值改坏）."""
    cases = [
        (["blue", "white"], "blue"),
        (["red", "white", "blue"], "red"),
        (["white", "blue"], "white"),
    ]
    for colors, expect in cases:
        for req in ("", "  ", "default", "auto", "DEFAULT"):
            got = _resolve(req, colors)
            assert got == expect, f"未指定({req!r}) 时得到 {got}，应为模板首色 {expect}"
        print(f"  [OK] allowed={colors} 未指定 → {expect}（模板首色）")


def test_unsupported_color_rejected():
    """模板不允许的色名仍要拒绝（不能因为放宽白底就把校验一起放过）."""
    try:
        _resolve("green", ["blue", "white"])
    except ValueError:
        print("  [OK] 模板不允许的色仍被拒绝: green")
        return
    raise AssertionError("模板不允许的颜色未被拒绝")


def test_custom_color_allowed():
    """custom:#RRGGBB 不受模板白名单限制（取色器场景）."""
    got = _resolve("custom:#123456", ["blue", "white"])
    assert got == "custom:#123456"
    print("  [OK] 自定义色放行: custom:#123456")


if __name__ == "__main__":
    print("=== 白底模板覆盖回归测试 ===")
    for fn in (
        test_form_default_is_empty,
        test_no_white_special_casing,
        test_white_is_preserved_under_template,
        test_unspecified_falls_back_to_template_default,
        test_unsupported_color_rejected,
        test_custom_color_allowed,
    ):
        print(f"[{fn.__name__}]")
        fn()
    print("\n全部通过 [OK]")
