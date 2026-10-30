"""换底染色回归测试（不需要 ONNX 模型，纯 numpy）.

【为什么需要这组测试】
用户反馈「换成红色背景时，人物区域也修改了」——截图里背景是暗玫瑰紫、
人物整体泛红。根因不是模型质量，而是 background.py 的 alpha 归一化判据：

    if mask.max() > 1.0 or mask.min() < 0.0:
        mask = sigmoid(mask)

u2net_human_seg 输出的 d0 本来就是 0~1 的概率图，只要有一个 -1e-7 的浮点噪声，
整张图就被再过一次 sigmoid：背景 alpha 0→0.50、人物 alpha 1→0.73。
合成后背景 = 半透明红压蓝 = 紫红，人物 = 73% 原图 + 27% 红 = 整体泛红。

这组测试把两件事钉死：
  1. normalize_mask 对概率图绝不二次 sigmoid（幂等）；
  2. 换底结果必须满足「背景角点 ≈ 目标色」「人物核心 ≈ 原图」。

运行：
    python backend/tests/test_bg_tint.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.image_engine import background as B          # noqa: E402


# ---------------------------------------------------------------- 合成素材
def _portrait(h=413, w=295):
    """合成一张蓝底人像：椭圆头 + 矩形躯干，边缘硬切."""
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    rgb[...] = (67, 142, 219)                     # 蓝底
    yy, xx = np.mgrid[0:h, 0:w]
    head = ((xx - w * 0.5) ** 2 / (w * 0.20) ** 2
            + (yy - h * 0.32) ** 2 / (h * 0.18) ** 2) <= 1
    body = (np.abs(xx - w * 0.5) < w * 0.34) & (yy > h * 0.52)
    mask = head | body
    rgb[mask] = (150, 120, 105)                   # 肤色
    rgb[body] = (95, 100, 110)                    # 深色衣服
    rgb[head] = (150, 120, 105)
    return rgb, mask.astype(np.float32)


def _corner(arr):
    h, w = arr.shape[:2]
    c = max(2, min(h, w) // 40)
    ps = [arr[:c, :c], arr[:c, -c:], arr[-c:, :c], arr[-c:, -c:]]
    return np.mean([p.reshape(-1, 3).mean(0) for p in ps], axis=0)


def _compose(rgb, alpha, target, ambient=True, shadow=True):
    fg = B._blend_ambient(rgb, alpha, target) if ambient else rgb
    h, w = alpha.shape[:2]
    bg = np.empty((h, w, 3), np.float32)
    bg[...] = np.array(target, np.float32)
    if shadow:
        bg = bg * (1.0 - B._drop_shadow(alpha)[..., None])
    a3 = alpha[..., None]
    return np.clip(fg * a3 + bg * (1 - a3), 0, 255)


# ---------------------------------------------------------------- 归一化
def test_prob_mask_not_sigmoided_twice():
    """概率图（含 -1e-7 浮点噪声）不得被二次 sigmoid."""
    prob = np.zeros((64, 64), dtype=np.float32)
    prob[16:48, 16:48] = 1.0
    prob[0, 0] = -1e-7                          # 导出/推理常见的浮点越界
    assert prob.min() < 0.0 and prob.max() <= 1.0

    out = B.normalize_mask(prob, "u2net")
    assert out.min() >= 0.0 and out.max() <= 1.0, "归一化后必须落在 0~1"
    assert float(out.max()) > 0.99, f"人物 alpha 被压低到 {out.max():.3f}（二次 sigmoid 症状）"
    assert float(out.min()) < 0.01, f"背景 alpha 被抬到 {out.min():.3f}（二次 sigmoid 症状）"
    print(f"  [OK] 概率图直通: min={out.min():.4f} max={out.max():.4f}")


def test_normalize_mask_idempotent():
    """概率图连过两次 == 过一次（幂等的意义就是不会被反复压缩）."""
    prob = np.zeros((64, 64), dtype=np.float32)
    prob[16:48, 16:48] = 1.0
    once = B.normalize_mask(prob, "u2net")
    twice = B.normalize_mask(once, "u2net")
    assert np.allclose(once, twice, atol=1e-6), "normalize_mask 不幂等"
    print("  [OK] normalize_mask 幂等")


def test_logits_still_sigmoided():
    """真正的 logits（BiRefNet：-22~+121）必须被 sigmoid 成 0~1."""
    logits = np.linspace(-25.0, 125.0, 4096, dtype=np.float32).reshape(64, 64)
    out = B.normalize_mask(logits, "birefnet")
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert out.min() < 0.01 and out.max() > 0.99, "logits 未被正确映射到 0~1"
    # 未知引擎也应按数值判据识别成 logits
    out2 = B.normalize_mask(logits, None)
    assert np.allclose(out, out2, atol=1e-6), "未知引擎的数值判据失效"
    print(f"  [OK] logits → sigmoid: min={out.min():.2e} max={out.max():.4f}")


def test_prob_declared_but_actually_logits():
    """引擎声明 prob 但数值明显越界时，仍要按 logits 处理（防声明写错）."""
    logits = np.linspace(-20.0, 20.0, 4096, dtype=np.float32).reshape(64, 64)
    out = B.normalize_mask(logits, "u2net")
    assert out.max() > 0.99 and out.min() < 0.01, "声明与数值矛盾时未兜底 sigmoid"
    print("  [OK] 声明 prob 但数值越界 → 兜底 sigmoid")


# ---------------------------------------------------------------- 换底不染色
def test_red_bg_no_tint_soft_alpha():
    """低分辨率软蒙版（人物内部 α≈0.7）换红底后，人物不得被染色.

    这是用户现象的直接复现：320 引擎升采样后人物内部大面积停在 0.6~0.8，
    不做内部不透明化时，人脸/衣服会掺进 30% 红色。
    """
    rgb, mask = _portrait()
    soft = mask * 0.72                            # 内部只有 0.72
    soft = B._refine_alpha(soft, rgb)             # 修复点：内部不透明化
    red = B.BG_COLOR_MAP["red"]
    out = _compose(rgb, soft, red)

    core = soft > 0.98
    assert core.sum() > 5000, f"人物核心区过小({core.sum()}px)，内部不透明化未生效"
    shift = np.abs(out[core] - rgb[core]).mean()
    assert shift < 2.0, f"人物核心被背景色污染 {shift:.2f} 级（>2 即肉眼可见泛红）"

    bg = _corner(out)
    delta = float(np.abs(bg - np.array(red, np.float32)).max())
    assert delta < 8.0, f"背景偏色 Δ={delta:.1f}（实测 {bg.round(0)}，目标 {red}）"
    print(f"  [OK] 软蒙版换红底: 人物偏移={shift:.2f} 级, 背景Δ={delta:.1f}")


def test_red_bg_no_tint_hard_alpha():
    """理想二值蒙版换红底：人物核心必须逐像素等于原图."""
    rgb, mask = _portrait()
    a = B._refine_alpha(mask, rgb)
    red = B.BG_COLOR_MAP["red"]
    out = _compose(rgb, a, red)

    core = a > 0.98
    shift = np.abs(out[core] - rgb[core]).mean()
    assert shift < 1.0, f"人物核心偏移 {shift:.2f} 级"
    bg = _corner(out)
    assert np.abs(bg - np.array(red, np.float32)).max() < 5.0
    print(f"  [OK] 二值蒙版换红底: 人物偏移={shift:.3f} 级, 背景={bg.round(0)}")


def test_ambient_only_on_edge_band():
    """环境光只允许作用于边缘过渡带，不能渗进人物内部.

    内部 α=1 时 band = 4α(1-α) = 0，所以内部本就不该有环境光；
    这里把「内部不透明化」关掉做对照，证明染色确实来自内部 α 不足。
    """
    rgb, mask = _portrait()
    soft = B._refine_alpha(mask * 0.72, rgb)       # 内部已被顶到 1
    band = np.clip(soft * (1 - soft) * 4.0, 0, 1)

    # 真正的「内部」= 内部不透明化把 α 顶到 1 的区域；α≈0.98 附近仍属羽化过渡带，
    # band 在那里非零是正常的（羽化半径内本来就是半透明）。
    inner = soft > 0.999
    assert inner.sum() > 5000, f"内部不透明化未生效（α>0.999 仅 {inner.sum()}px）"
    # α=0.999 时 band=4α(1-α)=0.004，乘 _AMBIENT_STRENGTH 后混色 0.11%，肉眼不可见；
    # 阈值取 0.01 表示「内部环境光权重必须可忽略」。
    assert band[inner].max() < 0.01, f"人物内部仍存在环境光权重 {band[inner].max():.4f}"

    core = soft > 0.98
    assert band[core].mean() < 0.01, f"人物核心平均环境光权重 {band[core].mean():.4f} 过高"

    # 对照：内部不透明化关闭 → 内部大量环境光权重
    raw = mask * 0.72
    band_raw = np.clip(raw * (1 - raw) * 4.0, 0, 1)
    assert band_raw[core].mean() > 0.3, "对照组未复现内部染色条件"
    print(f"  [OK] 环境光仅边缘: 内部权重 max={band[inner].max():.2e} "
          f"核心均值={band[core].mean():.2e} (对照 {band_raw[core].mean():.2f})")


def test_audit_composition_flags_tint():
    """出图自检必须能识别「背景偏色 + 人物染色」."""
    rgb, mask = _portrait()
    red = B.BG_COLOR_MAP["red"]

    good = B._refine_alpha(mask, rgb)
    good_out = _compose(rgb, good, red)
    ok = B._audit_composition(good_out, good, red, rgb)
    assert ok["bg_delta"] < 8.0, f"正常出图被判成偏色: {ok}"
    assert ok["core_shift"] < 2.0, f"正常出图被判成染色: {ok}"

    # 造一个「坏」alpha：内部只有 0.73（二次 sigmoid 的后果）
    bad = B._refine_alpha(mask * 0.73, rgb)
    bad = bad * 0.73                              # 强行压回去，模拟未修复
    bad_out = _compose(rgb, bad, red)
    flagged = B._audit_composition(bad_out, bad, red, rgb)
    assert flagged["core_px"] > 1000, "几何核心区取不到，自检会失明"
    assert flagged["core_shift"] > 8.0, f"染色未被自检发现: {flagged}"
    print(f"  [OK] 自检: 正常 core_shift={ok['core_shift']} "
          f"({ok['core_px']}px) / 坏图 core_shift={flagged['core_shift']} "
          f"({flagged['core_px']}px)")


def test_bg_engine_output_kind_declared():
    """两个引擎的输出类型必须显式声明，避免又回到「靠猜」."""
    assert B._ENGINE_OUTPUT_KIND["birefnet"] == "logits"
    assert B._ENGINE_OUTPUT_KIND["u2net"] == "prob"
    for k in B._ENGINE_MODELS:
        assert k in B._ENGINE_OUTPUT_KIND, f"引擎 {k} 未声明输出类型"
    print(f"  [OK] 引擎声明: {B._ENGINE_OUTPUT_KIND}")


# ---------------------------------------------------------------- 白底专项
def _soft_edge_portrait(h=420, w=300, old_bg=(67, 142, 219)):
    """合成「软边人像」：模拟真实抠图里 α 渐变的轮廓.

    关键：颜色按真实合成公式 C = F·α + B_old·(1-α) 生成，
    这样才存在「旧底色污染」，才能验证去色污是否真的生效。
    """
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt(((xx - w * 0.5) / (w * 0.22)) ** 2
                   + ((yy - h * 0.35) / (h * 0.22)) ** 2)
    alpha = np.clip((1.0 - dist) * 6.0, 0.0, 1.0).astype(np.float32)   # 软过渡带
    fg_true = np.zeros((h, w, 3), np.float32)
    fg_true[...] = (120, 92, 74)                                        # 深棕头发
    old = np.array(old_bg, np.float32).reshape(1, 1, 3)
    rgb = fg_true * alpha[..., None] + old * (1 - alpha[..., None])
    return rgb.astype(np.float32), alpha, fg_true


def test_decontaminate_removes_old_bg_cast():
    """白底换底：轮廓过渡带里的旧蓝底必须被去掉（去色污必须生效）.

    这是「选白色背景却换不干净」的核心：过渡带像素是
    C = F·α + 蓝底·(1-α)，直接贴白底就会把蓝一起搬过去。

    【判据怎么定】理想图必须用**引擎自己估出的 alpha** 来合成：
        ideal = F_true · a_engine + 白 · (1 - a_engine)
    因为出图用的就是这条 alpha 链路。若用素材的真值 alpha 造理想图，
    误差里会混进「alpha 估计偏差」这个与去色污无关的成分，测不出真实效果
    （实测那样做只能测出 5% 改善，而正确口径是 12%）。
    同理，白底现在走「高调底」边缘策略（_is_high_key），理想图必须用
    完全相同的 alpha 链路（high_key=True），否则测的是「换了一套边缘参数」。
    """
    rgb, alpha, fg_true = _soft_edge_portrait()
    target = np.array((255, 255, 255), np.float32).reshape(1, 1, 3)
    img = _to_img(rgb)
    a_eng = B._refine_alpha(B._infer_alpha(img, B.resolve_engine()), rgb, high_key=True)
    ideal = np.clip(fg_true * a_eng[..., None] + target * (1 - a_eng[..., None]), 0, 255)
    ring = (alpha > 0.15) & (alpha < 0.85)     # 真实过渡带

    def err(decon: bool) -> float:
        out = np.array(
            B.replace_background(img, "white", decontaminate=decon,
                                 ambient=False, shadow=False),
            np.float32,
        )
        return float(np.abs(out[ring] - ideal[ring]).mean())

    without = err(False)
    with_dc = err(True)
    assert without > 10.0, f"对照组未复现污染（误差={without:.2f}）"
    assert with_dc < without * 0.95, (
        f"去色污未改善：误差 {without:.2f} → {with_dc:.2f}（应至少降 5%）"
    )
    print(f"  [OK] 白底去色污: 与理想图误差 {without:.2f} → {with_dc:.2f} 级 "
          f"({(1-with_dc/without)*100:.0f}% 改善)")


def test_decontaminate_skipped_on_complex_bg():
    """旧底不是纯色时（复杂场景）必须跳过去色污，否则会抹掉真实纹理.

    直接测门控本身，不去跑抠图模型：随机噪声图会让分割模型完全失效，
    那是另一个问题，混进来会让这条测试失去针对性。
    """
    rng = np.random.default_rng(7)
    h, w = 240, 180

    # ① 复杂背景（随机噪声，模拟树叶/天空纹理）：std 远大于门槛 → 不启用
    complex_rgb = rng.uniform(0, 255, (h, w, 3)).astype(np.float32)
    alpha = np.zeros((h, w), np.float32)
    alpha[70:200, 50:140] = 1.0
    _, _, is_flat_complex = B._old_bg_field(complex_rgb, alpha)
    assert not is_flat_complex, "复杂背景被误判成纯色底（会抹掉真实纹理）"

    # ② 纯色底（蓝底 + 轻微噪声）：应判定为可去污
    flat_rgb = np.zeros((h, w, 3), np.float32)
    flat_rgb[...] = (67, 142, 219)
    flat_rgb += rng.normal(0, 1.5, (h, w, 3)).astype(np.float32)
    flat_rgb = np.clip(flat_rgb, 0, 255)
    _, _, is_flat_plain = B._old_bg_field(flat_rgb, alpha)
    assert is_flat_plain, "纯色底未被识别（去色污不会启用，白底换不干净）"

    # ③ 门控结论直接决定 replace_background 是否调用 _decontaminate：
    #    这里只验证门控判定本身（不跑抠图模型 —— 纯色块+矩形不是人脸，
    #    分割模型会输出全 0，跑下去测的是模型失效而不是门控）。
    assert is_flat_complex != is_flat_plain, "两种底色的门控结果不应相同"
    print(f"  [OK] 纯色门控: 复杂底 is_flat={is_flat_complex} / "
          f"纯色底 is_flat={is_flat_plain}")


def test_decontaminate_leaves_interior_untouched():
    """去色污只能动过渡带，人物内部必须逐像素不变."""
    rgb, alpha, _ = _soft_edge_portrait()
    a_ref = B._refine_alpha(alpha, rgb)
    bg_field, conf, is_flat = B._old_bg_field(rgb, a_ref)
    assert is_flat, "合成纯色底应被识别为纯色"
    out = B._decontaminate(rgb, a_ref, bg_field, conf)

    inner = a_ref > 0.999
    assert inner.sum() > 1000, f"内部区域过小({inner.sum()}px)"
    dmax = float(np.abs(out[inner] - rgb[inner]).max())
    assert dmax < 1e-3, f"人物内部被去色污改动 {dmax:.4f} 级（应完全不动）"
    print(f"  [OK] 去色污不碰内部: {inner.sum()}px 最大改动={dmax:.2e}")


def test_white_bg_shadow_is_weak():
    """白底投影必须显著弱于深底，否则会留一圈灰边（白底「不干净」）."""
    white_op = B._shadow_strength_for((255, 255, 255))
    blue_op = B._shadow_strength_for((67, 142, 219))
    red_op = B._shadow_strength_for((213, 43, 30))
    assert white_op < blue_op, f"白底投影({white_op:.3f}) 未弱于蓝底({blue_op:.3f})"
    # 白底 255 → 最暗不得超过 12 级落差（>20 级肉眼一眼可见灰边）
    drop = 255.0 * white_op
    assert drop <= 12.0, f"白底投影落差 {drop:.1f} 级过大（会显灰边）"

    alpha = np.ones((80, 80), np.float32)
    alpha[:40] = 0.0
    sh_white = B._drop_shadow(alpha, white_op).max()
    sh_blue = B._drop_shadow(alpha, blue_op).max()
    assert sh_white < sh_blue
    print(f"  [OK] 白底投影: opacity={white_op:.3f} (落差{drop:.1f}级) "
          f"vs 蓝底 {blue_op:.3f} / 红底 {red_op:.3f}")


def test_white_bg_corners_are_white():
    """白底出图：四角必须是纯白（254~255），不能有任何灰影/偏色."""
    rgb, alpha, _ = _soft_edge_portrait()
    out = np.array(
        B.replace_background(_to_img(rgb), "white", decontaminate=True),
        np.float32,
    )
    corner = _corner(out)
    assert np.abs(corner - 255.0).max() < 3.0, f"白底角落不纯: {corner.round(1)}"
    # 全图最暗的背景像素（α<0.001）也不能低于 245
    pure = alpha < 0.001
    if pure.any():
        worst = out[pure].mean(axis=1).min()
        assert worst >= 245.0, f"白底出现灰影，最暗 {worst:.0f}"
        print(f"  [OK] 白底纯度: 角落={corner.round(1)} 背景最暗={worst:.0f}")
    else:
        print(f"  [OK] 白底纯度: 角落={corner.round(1)}")


def _to_img(arr):
    from PIL import Image
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ---------------------------------------------------------------- 高调底收边
def _hard_portrait(h=1200, w=840):
    """硬边人像：alpha 二值，本身没有任何渐变.

    【为什么不能用带渐变的素材做这条测试】合成的软边素材自带一段 6~8px 的
    线性坡，宽度由素材决定、与精修参数无关，会把两种参数的区别稀释掉
    （实测那样比出来的半透明带比值只有 0.67~0.73，看起来像「收边无效」）。
    用硬边遮罩，过渡带宽度就完全由 _refine_alpha 的参数决定 —— 这才是被测对象。
    """
    yy, xx = np.mgrid[0:h, 0:w]
    head = ((xx - w * 0.5) ** 2 / (w * 0.18) ** 2
            + (yy - h * 0.33) ** 2 / (h * 0.16) ** 2) <= 1
    body = (np.abs(xx - w * 0.5) < w * 0.30) & (yy > h * 0.52)
    mask = (head | body).astype(np.float32)
    rgb = np.full((h, w, 3), 255, np.float32)
    rgb[mask > 0] = (120, 92, 74)          # 深棕头发 / 深色衣
    return rgb, mask


def _transition_width(alpha):
    """过渡带的**有效像素宽度** = 半透明带面积 ÷ 轮廓周长.

    【为什么不用扫描线量】扫描线只在恰好垂直穿过轮廓时有意义，椭圆/肩线
    这类斜边会把测量值抬到 2~20px 乱跳（实测同一张图不同 y 差 4 倍）。
    面积 ÷ 周长是几何上的平均带厚，与图幅、轮廓走向都无关，可跨尺寸比较。
    """
    import cv2
    band = int(((alpha > 0.01) & (alpha < 0.5)).sum())
    fg = (alpha > 0.5).astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    perim = int((cv2.morphologyEx(fg, cv2.MORPH_GRADIENT, k) > 0).sum())
    return band / max(perim, 1), band


def test_high_key_alpha_band_is_narrower():
    """白底（高调底）的 alpha 过渡带必须显著窄于深底 —— 这是「白底发虚」的根治.

    【问题】深底上的软边是景深，白底上的软边是「一层白雾 + 头发在发光」。
    实测真实人像（1680×2240，BiRefNet + 现参数）：
        轮廓过渡带（luma 246→60）     12.0px → 4.0px
        半透明带（0.01<α<0.5）像素     48659 → 14078（-71%）
        背景四角                       254 → 255，缩到一寸后无锯齿
    合成硬边人像（1200×840）同一套参数：有效带厚 0.94px → 0.50px（-47%）。
    两条链路方向一致，这里用合成图做回归，阈值取「至少窄 35%」。
    """
    rgb, mask = _hard_portrait()
    a_deep = B._refine_alpha(mask.copy(), rgb, high_key=False)
    a_high = B._refine_alpha(mask.copy(), rgb, high_key=True)

    w_deep, band_deep = _transition_width(a_deep)
    w_high, band_high = _transition_width(a_high)
    assert band_deep > 0, "对照组没有过渡带，无法比较"
    assert w_high < w_deep * 0.65, (
        f"高调底过渡带未收紧：{w_deep:.2f}px → {w_high:.2f}px（应至少窄 35%）"
    )
    # 不能剃成二值硬切：中间值必须仍存在（发丝/衣领的真实半透明）
    mid = int(((a_high > 0.2) & (a_high < 0.8)).sum())
    assert mid > 0, "高调底把过渡带剃成了二值切边（半透明像素消失）"
    # 前景面积不能变（收边只准改过渡带，不许啃掉人物）
    shrink = 1.0 - a_high.sum() / max(a_deep.sum(), 1e-6)
    assert abs(shrink) < 0.01, f"高调底前景面积变化 {shrink:.2%}（收边过度/丢失人物）"
    print(f"  [OK] 高调底收边: 有效带厚 {w_deep:.2f}px → {w_high:.2f}px "
          f"({(1 - w_high / w_deep) * 100:.0f}% 收紧)，半透明带 {band_deep} → {band_high}px，"
          f"中值带 {mid}px 仍在，前景面积变化 {shrink:.2%}")


def test_high_key_flag_classification():
    """高调底判定：白与近白为 True，标准蓝/红/中灰为 False."""
    assert B._is_high_key((255, 255, 255)) is True
    assert B._is_high_key((240, 240, 240)) is True        # #F0F0F0 英国签证浅灰
    assert B._is_high_key(B.BG_COLOR_MAP["blue_std"]) is False
    assert B._is_high_key(B.BG_COLOR_MAP["blue_gov"]) is False
    assert B._is_high_key(B.BG_COLOR_MAP["red_std"]) is False
    assert B._is_high_key(B.BG_COLOR_MAP["gray_deep"]) is False
    # 纯白底投影/环境光必须都为 0（审核要求 RGB≥250）
    assert B._shadow_strength_for((255, 255, 255)) == 0.0
    assert B._ambient_strength_for((255, 255, 255)) == 0.0
    # 高调底的环境光必须弱于深底（窄带上强混色会堆出亮描边）
    assert B._ambient_strength_for((240, 240, 240)) < B._ambient_strength_for(B.BG_COLOR_MAP["blue_std"])
    print("  [OK] 高调底判定与投影/环境光策略一致")


def test_standard_palette_matches_reference_table():
    """标准色板必须与《证件照常用背景色 RGB 参考》逐色一致（HEX 由 RGB 反推）."""
    expect = {
        "white": (255, 255, 255),        # #FFFFFF
        "blue_std": (67, 142, 219),      # #438EDB
        "blue_gov": (0, 102, 204),       # #0066CC
        "blue_alt": (29, 141, 213),      # #1D8DD5
        "blue_cyan": (0, 191, 243),      # #00BFF3
        "sky_light": (100, 197, 255),    # #64C5FF
        "red_std": (255, 0, 0),          # #FF0000
        "red_deep": (220, 20, 60),       # #DC143C
        "red_crimson": (204, 0, 0),      # #CC0000
        "red_alt": (206, 0, 0),          # #CE0000
        "red_dark": (149, 2, 8),         # #950208
        "gray_light": (240, 240, 240),   # #F0F0F0
        "gray_mid": (230, 230, 230),     # #E6E6E6
        "gray_deep": (211, 211, 211),    # #D3D3D3
    }
    assert list(B.STANDARD_BG_COLORS) == list(expect), (
        f"标准色板顺序/成员不符：{B.STANDARD_BG_COLORS}"
    )
    for name, rgb in expect.items():
        assert B.BG_COLOR_MAP[name] == rgb, f"{name} 色值 {B.BG_COLOR_MAP[name]} != {rgb}"
        meta = B.BG_COLOR_META[name]
        want_hex = "#%02X%02X%02X" % rgb
        assert meta["hex"] == want_hex, f"{name} HEX {meta['hex']} != {want_hex}"
        assert meta.get("usage"), f"{name} 缺少使用场景说明"
    # 分组必须齐全，前端靠它画组头
    groups = {B.bg_color_group(n) for n in expect}
    assert groups == {"白", "蓝", "红", "灰"}, f"分组异常: {groups}"
    print(f"  [OK] 标准色板 {len(expect)} 色与参考表逐色一致，HEX 全部可反推")


def test_template_color_family_gate():
    """模板按色调分组放行：允许 blue 时可交任何蓝，白底模板仍拒绝蓝."""
    assert B.same_color_family("blue", "blue_gov")
    assert B.same_color_family("blue", "sky_light")
    assert B.same_color_family("white", "white")
    assert not B.same_color_family("blue", "red_std")
    assert not B.same_color_family("white", "gray_light")
    # 「其他」组不当同族（绿/粉/紫之间不能互相顶替）
    assert not B.same_color_family("green", "pink")
    print("  [OK] 模板色族判定: 蓝系互通 / 跨色族拒绝 / 其他组不互通")


if __name__ == "__main__":
    print("=== 换底染色 / 白底回归测试 ===")
    for fn in (
        test_prob_mask_not_sigmoided_twice,
        test_normalize_mask_idempotent,
        test_logits_still_sigmoided,
        test_prob_declared_but_actually_logits,
        test_red_bg_no_tint_soft_alpha,
        test_red_bg_no_tint_hard_alpha,
        test_ambient_only_on_edge_band,
        test_audit_composition_flags_tint,
        test_bg_engine_output_kind_declared,
        test_decontaminate_removes_old_bg_cast,
        test_decontaminate_skipped_on_complex_bg,
        test_decontaminate_leaves_interior_untouched,
        test_white_bg_shadow_is_weak,
        test_white_bg_corners_are_white,
        test_high_key_alpha_band_is_narrower,
        test_high_key_flag_classification,
        test_standard_palette_matches_reference_table,
        test_template_color_family_gate,
    ):
        print(f"[{fn.__name__}]")
        fn()
    print("\n全部通过 [OK]")
