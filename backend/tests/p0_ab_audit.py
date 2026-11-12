"""P0 改动前后 A/B 验收脚本（生产口径 / 同几何基线 / 同一张样张）。

用法（在 photo-zhijianzhao/ 根目录执行）：
    .venv/Scripts/python.exe backend/tests/p0_ab_audit.py --tag before --params old
    .venv/Scripts/python.exe backend/tests/p0_ab_audit.py --tag after  --params both

输出：p0_ab_out/<tag>_<params>.json  与  p0_ab_out/<tag>_<params>_pair.jpg

【为什么需要它】现有 23 项回归全是「单算子 + 合成图」断言，量的是算子而不是
用户看到的整张出图；评测脚本还把全身照缩到 400px 高（脸约 35px），任何合规
改动都天然看不见。这个脚本固定走 process_image / render_baseline 真实链路，
用**同几何基线**做控制变量，量脸部区域的 ΔL/Δb/色差/高频比，并单独探测
「美颜执行时脸有多大、关键点是否检出」这个前置条件。

【Haar 路径】本地开发目录含中文，cv2.data.haarcascades 会让 CascadeClassifier
加载失败（empty=True → detect_faces 恒为空）。为把「中文路径」这个环境变量
从架构 A/B 里隔离掉，这里统一把级联 XML 复制到 ASCII 临时目录再加载，
等价于生产 Linux 的正常路径。中文路径问题本身另有修复，不参与本对比。
"""

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

import cv2  # noqa: E402

# ---- 统一 Haar 级联到 ASCII 路径（隔离本地中文路径变量）----
_CASC_DIR = tempfile.mkdtemp(prefix="p0_casc_")
shutil.copyfile(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml",
    os.path.join(_CASC_DIR, "haarcascade_frontalface_default.xml"),
)
cv2.data.haarcascades = _CASC_DIR + os.sep

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from loguru import logger  # noqa: E402


def _cjk_font(size: int = 13):
    """标注用中文字体：PIL 默认字体不含中文，会渲染成方块."""
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/System/Library/Fonts/PingFang.ttc"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001
                pass
    return ImageFont.load_default()


logger.remove()
logger.add(sys.stderr, level="WARNING")

from app.core.image_engine.pipeline import (  # noqa: E402
    ProcessParams, process_image, render_baseline,
)
from app.core.image_engine import beautify as B  # noqa: E402
# 【2026-11 P1 口径修正】审计脚本自己也曾直接用 cv2.data.haarcascades，
# 在中文安装路径下 CascadeClassifier 为 empty → 脸部选区静默退化成「画面中心椭圆」，
# 于是 ΔL / 区域色差 / 色斑指标量的是错的区域。生产侧已修，这里必须同口径。
from app.core.image_engine.cascade import frontal_face_cascade  # noqa: E402

SAMPLE = os.path.join(PROJECT, "tests", "test_photo.jpg")
OUT_DIR = os.path.join(PROJECT, "p0_ab_out")

# 两组参数：old = 当前线上默认（强度 0.7 / 不对齐），new = P0 目标默认（1.0 / 对齐）
PARAM_SETS = {
    "old": dict(retouch_strength=0.7, id_photo_align=False),
    "new": dict(retouch_strength=1.0, id_photo_align=True),
}


def _face_selection(bgr: np.ndarray) -> np.ndarray:
    """在基线出图上定位脸部椭圆选区；检测不到时退化为构图中心区域."""
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    cascade = frontal_face_cascade()
    boxes = cascade.detectMultiScale(gray, 1.1, 4, minSize=(max(24, w // 8), max(24, w // 8)))
    if len(boxes):
        x, y, fw, fh = max(boxes, key=lambda t: t[2] * t[3])
        cx, cy = x + fw / 2.0, y + fh / 2.0
        rx, ry = fw * 0.46, fh * 0.50
    else:
        cx, cy, rx, ry = w * 0.50, h * 0.40, w * 0.26, h * 0.20
    yy, xx = np.mgrid[0:h, 0:w]
    sel = (((xx - cx) / max(rx, 1.0)) ** 2 + ((yy - cy) / max(ry, 1.0)) ** 2) < 1.0
    return sel


def _zone_spread(lab: np.ndarray, sel: np.ndarray, cx: float, cy: float,
                 rx: float, ry: float) -> float:
    """脸部 4x4 网格的 a/b 均值离散度 —— 低频「区域色差」指标."""
    h, w = lab.shape[:2]
    vals = []
    for i in range(4):
        for j in range(4):
            x0, x1 = int(cx - rx + 2 * rx * j / 4), int(cx - rx + 2 * rx * (j + 1) / 4)
            y0, y1 = int(cy - ry + 2 * ry * i / 4), int(cy - ry + 2 * ry * (i + 1) / 4)
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            sub = (sel[y0:y1, x0:x1] > 0)
            if sub.sum() < 20:
                continue
            vals.append((float(lab[y0:y1, x0:x1, 1][sub].mean()),
                         float(lab[y0:y1, x0:x1, 2][sub].mean())))
    if len(vals) < 4:
        return 0.0
    arr = np.array(vals, dtype=np.float32)
    return float(arr.std(axis=0).mean())


def _chroma_spot(lab: np.ndarray, rad: float = 5.0) -> np.ndarray:
    """a*/b* 小尺度高通能量 —— 痘印/红斑/黄斑的直接量尺.

    【为什么单独立这个指标】证件照里「看得见的瑕疵」几乎全是**色度现象**：
    实测 L 中频 |mid| p90 仅 0.71 级（8bit 量化直接吞掉），而 a*/b* 局部
    偏离 p90 有 2.3~3.2 级。只量 ΔL / 高频比会得出「修了但看不出来」，
    量这个才能判断「痘印到底有没有变淡」。
    """
    a = lab[:, :, 1].astype(np.float32)
    b = lab[:, :, 2].astype(np.float32)
    ha = a - cv2.GaussianBlur(a, (0, 0), rad)
    hb = b - cv2.GaussianBlur(b, (0, 0), rad)
    return np.sqrt(ha * ha + hb * hb)


def _metrics(base: Image.Image, out: Image.Image) -> dict:
    b = np.array(base.convert("RGB"))
    o = np.array(out.convert("RGB"))
    bgr_b = b[:, :, ::-1].copy()
    bgr_o = o[:, :, ::-1].copy()
    lab_b = cv2.cvtColor(bgr_b, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_o = cv2.cvtColor(bgr_o, cv2.COLOR_BGR2LAB).astype(np.float32)
    sel = _face_selection(bgr_b)
    # 只在「脸部椭圆 ∩ 肤色像素」上量色斑，避免眉毛/睫毛/衣领的高对比混入
    ycrcb = cv2.cvtColor(bgr_b, cv2.COLOR_BGR2YCrCb)
    skin = ((ycrcb[:, :, 1] >= 133) & (ycrcb[:, :, 1] <= 180)
            & (ycrcb[:, :, 2] >= 77) & (ycrcb[:, :, 2] <= 127)
            & (ycrcb[:, :, 0] > 60))
    ss = sel & skin
    if ss.sum() < 500:
        ss = sel
    sp_b, sp_o = _chroma_spot(lab_b), _chroma_spot(lab_o)
    spot_base = float(sp_b[ss].mean())
    spot_out = float(sp_o[ss].mean())
    d = np.abs(o.astype(np.float32) - b.astype(np.float32))[sel]
    g_b = cv2.Laplacian(cv2.cvtColor(bgr_b, cv2.COLOR_BGR2GRAY), cv2.CV_32F)[sel]
    g_o = cv2.Laplacian(cv2.cvtColor(bgr_o, cv2.COLOR_BGR2GRAY), cv2.CV_32F)[sel]
    hf = float(np.mean(np.abs(g_o)) / max(1e-6, np.mean(np.abs(g_b))))
    h, w = b.shape[:2]
    yy, xx = np.nonzero(sel)
    cx, cy = float(xx.mean()), float(yy.mean())
    rx, ry = float((xx.max() - xx.min()) / 2), float((yy.max() - yy.min()) / 2)
    zb = _zone_spread(lab_b, sel, cx, cy, rx, ry)
    zo = _zone_spread(lab_o, sel, cx, cy, rx, ry)
    return {
        "face_pixels": int(sel.sum()),
        "dL": round(float(np.mean(lab_o[:, :, 0][sel] - lab_b[:, :, 0][sel])), 3),
        "da": round(float(np.mean(lab_o[:, :, 1][sel] - lab_b[:, :, 1][sel])), 3),
        "db": round(float(np.mean(lab_o[:, :, 2][sel] - lab_b[:, :, 2][sel])), 3),
        "mean_abs": round(float(d.mean()), 3),
        "p95_abs": round(float(np.percentile(d, 95)), 3),
        "hf_ratio": round(hf, 4),
        "zone_spread_base": round(zb, 3),
        "zone_spread_out": round(zo, 3),
        "zone_spread_drop": round((zb - zo) / max(1e-6, zb), 4),
        # ---- P1 新增：色斑（痘印/红斑）可见度指标 ----
        "spot_contrast_base": round(spot_base, 3),
        "spot_contrast_out": round(spot_out, 3),
        "spot_drop": round((spot_base - spot_out) / max(1e-6, spot_base), 4),
        "strong_spot_base": round(float((sp_b[ss] > 5.0).mean()), 4),
        "strong_spot_out": round(float((sp_o[ss] > 5.0).mean()), 4),
        "spot_skin_px": int(ss.sum()),
    }


def _probe_precondition(work: Image.Image, strength: float) -> dict:
    """诊断「美颜执行那一刻」的前置条件：脸有多大、关键点是否检出."""
    bgr = B._pil_to_cv2(work)
    faces = B._detect_all_landmarks(bgr)
    boxes = [B._box_from_landmarks(lm, bgr.shape[1], bgr.shape[0]) for lm in faces]
    parse = B._parse_skin_mask(bgr, boxes or None)
    if faces:
        mask, geo = B._landmark_region_mask(bgr, faces, parse)
        mode = "landmark"
    else:
        mask, geo = B._fallback_region(bgr, parse)
        mode = "fallback"
    iod = float(geo.iod) if geo is not None else 0.0
    coverage = float((mask > 0.4).mean()) if mask is not None else 0.0
    return {
        "work_size": list(work.size),
        "landmark_faces": len(faces),
        "iod_px": round(iod, 2),
        "mode": mode,
        "parse": "on" if parse is not None else "off",
        "mask_coverage": round(coverage, 4),
        "r_fine_px": B._scale(iod, B._FINE_RADIUS_RATIO, B._FINE_RADIUS_MIN),
    }


def run_one(tag: str, pname: str, max_kb: int = 10000) -> dict:
    p = PARAM_SETS[pname]
    src = Image.open(SAMPLE).convert("RGB")
    params = ProcessParams(
        width=295, height=413, dpi=300,
        beautify_level=1, bg_color="keep",
        retouch_strength=p["retouch_strength"],
        id_photo_align=p["id_photo_align"],
        min_kb=0, max_kb=max_kb,
    )
    t0 = time.time()
    result = process_image(src, params)
    baseline = render_baseline(src, params)
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    base = Image.open(io.BytesIO(baseline.data)).convert("RGB")
    m = _metrics(base, out)
    m.update({
        "tag": tag, "params": pname, **p,
        "out_size": list(out.size),
        "result_kb": round(result.result_size_kb, 2),
        "warnings": list(result.warnings),
        "faces_detected": result.faces_detected,
        "elapsed_s": round(time.time() - t0, 2),
    })

    # 前置条件探测：优先用 pipeline 报告（新代码），否则复算（旧代码）
    report = getattr(result, "beautify_report", None) or {}
    if report:
        m["beautify_report"] = report
    else:
        # 旧代码：按旧的几何口径还原「美颜实际执行的那张图」
        from app.core.image_engine.crop import resize_crop
        from app.core.image_engine.face_detect import align_face
        if p["id_photo_align"]:
            try:
                work = align_face(src, 295, 413)
            except Exception:
                work = resize_crop(src, 295, 413)
        else:
            work = resize_crop(src, 295, 413)
        m["beautify_report"] = _probe_precondition(work, p["retouch_strength"])

    # 出图对比（上：同几何基线，下：结果；右侧 2x 脸部特写）
    face_box = _face_bbox(base)
    pair = Image.new("RGB", (base.width * 2 + 12, base.height + 30), "white")
    d = ImageDraw.Draw(pair)
    pair.paste(base, (0, 30))
    pair.paste(out, (base.width + 12, 30))
    d.text((5, 8), f"{tag}/{pname} baseline", fill="black")
    d.text((base.width + 17, 8), f"{tag}/{pname} result", fill="black")
    path = os.path.join(OUT_DIR, f"{tag}_{pname}_pair.jpg")
    pair.save(path, quality=95)

    zoom_base = base.crop(face_box).resize(
        ((face_box[2] - face_box[0]) * 2, (face_box[3] - face_box[1]) * 2), Image.LANCZOS)
    zoom_out = out.crop(face_box).resize(zoom_base.size, Image.LANCZOS)
    zoom = Image.new("RGB", (zoom_base.width * 2 + 10, zoom_base.height + 26), "white")
    zd = ImageDraw.Draw(zoom)
    zoom.paste(zoom_base, (0, 26))
    zoom.paste(zoom_out, (zoom_base.width + 10, 26))
    zd.text((5, 6), "baseline face 200%", fill="black")
    zd.text((zoom_base.width + 15, 6), "result face 200%", fill="black")
    zoom.save(os.path.join(OUT_DIR, f"{tag}_{pname}_face.jpg"), quality=95)

    with open(os.path.join(OUT_DIR, f"{tag}_{pname}.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return m


def _face_bbox(img: Image.Image):
    bgr = np.array(img.convert("RGB"))[:, :, ::-1].copy()
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    cascade = frontal_face_cascade()
    boxes = cascade.detectMultiScale(gray, 1.1, 4, minSize=(30, 30))
    w, h = img.size
    if len(boxes):
        x, y, fw, fh = max(boxes, key=lambda t: t[2] * t[3])
        pad = int(fw * 0.35)
        return (max(0, x - pad), max(0, y - pad), min(w, x + fw + pad), min(h, y + fh + pad))
    return (int(w * 0.22), int(h * 0.14), int(w * 0.78), int(h * 0.62))


def _load_pair(tag: str, pname: str):
    """从 pair 拼图里还原 baseline / result 两张 295×413 出图."""
    im = Image.open(os.path.join(OUT_DIR, f"{tag}_{pname}_pair.jpg")).convert("RGB")
    h = im.height - 30
    w = (im.width - 12) // 2
    return im.crop((0, 30, w, 30 + h)), im.crop((w + 12, 30, w + 12 + w, 30 + h))


def _skin_zone_metrics(base: Image.Image, out: Image.Image) -> dict:
    """在「脸部椭圆 ∩ 肤色像素」上重算高频比与区域色差.

    【为什么要补这个口径】整脸椭圆包含眉毛、睫毛、发际线，而输出锐化刻意
    只对这些非皮肤结构保持强度 —— 整脸 HF 因此恒 >1，看不出磨皮是否生效。
    皮肤区口径才是「磨皮有没有把皮肤修柔」的正确量尺。
    """
    b = np.array(base.convert("RGB"))
    o = np.array(out.convert("RGB"))
    bgr_b = b[:, :, ::-1].copy()
    bgr_o = o[:, :, ::-1].copy()
    sel = _face_selection(bgr_b)
    ycrcb = cv2.cvtColor(bgr_b, cv2.COLOR_BGR2YCrCb)
    skin = ((ycrcb[:, :, 1] >= 133) & (ycrcb[:, :, 1] <= 180)
            & (ycrcb[:, :, 2] >= 77) & (ycrcb[:, :, 2] <= 127)
            & (ycrcb[:, :, 0] > 60))
    ss = sel & skin
    if ss.sum() < 500:
        ss = sel
    g0 = cv2.Laplacian(cv2.cvtColor(bgr_b, cv2.COLOR_BGR2GRAY), cv2.CV_32F)
    g1 = cv2.Laplacian(cv2.cvtColor(bgr_o, cv2.COLOR_BGR2GRAY), cv2.CV_32F)
    lab_b = cv2.cvtColor(bgr_b, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_o = cv2.cvtColor(bgr_o, cv2.COLOR_BGR2LAB).astype(np.float32)
    yy, xx = np.nonzero(ss)
    cx, cy, rx, ry = float(xx.mean()), float(yy.mean()), \
        float((xx.max() - xx.min()) / 2), float((yy.max() - yy.min()) / 2)
    zb = _zone_spread(lab_b, ss, cx, cy, rx, ry)
    zo = _zone_spread(lab_o, ss, cx, cy, rx, ry)
    return {
        "hf_skin": round(float(np.mean(np.abs(g1[ss])) / max(1e-6, np.mean(np.abs(g0[ss])))), 4),
        "zone_drop_skin": round((zb - zo) / max(1e-6, zb), 4),
        "skin_px": int(ss.sum()),
    }


def summary():
    """汇总改动前后：统一口径重算 + 产出一张 2×2 脸部特写对比图."""
    keys = [("before", "old", "改动前 · 线上默认 (强度70% / 不对齐)"),
            ("after", "new", "改动后 · P0 默认 (强度100% / 对齐)")]
    rows = []
    for tag, pname, label in keys:
        with open(os.path.join(OUT_DIR, f"{tag}_{pname}.json"), encoding="utf-8") as f:
            meta = json.load(f)
        base, out = _load_pair(tag, pname)
        extra = _skin_zone_metrics(base, out)
        rep = meta.get("beautify_report", {})
        rows.append((label, meta, extra, rep, base, out))

    print("\n=== P0 改动前后 A/B（同一张样张 / 同几何基线 / 生产口径出图）===")
    hdr = f"{'指标':<26}{'改动前':>12}{'改动后':>12}{'变化':>12}"
    print(hdr)
    fields = [
        ("ΔL 脸部亮度", "dL", "+.2f"),
        ("Δb 去黄", "db", "+.2f"),
        ("平均改动 /255", "mean_abs", ".2f"),
        ("P95 改动 /255", "p95_abs", ".2f"),
        ("整脸高频比", "hf_ratio", ".3f"),
        ("区域色差下降", "zone_spread_drop", "+.1%"),
    ]
    for name, key, fmt in fields:
        a, b = rows[0][1][key], rows[1][1][key]
        if fmt.endswith("%"):
            print(f"{name:<26}{a*100:>11.1f}%{b*100:>11.1f}%{(b-a)*100:>+11.1f}pp")
        else:
            print(f"{name:<26}{a:>12.2f}{b:>12.2f}{b-a:>+12.2f}")
    for name, key in [("皮肤区高频比", "hf_skin"), ("皮肤区色差下降", "zone_drop_skin")]:
        a, b = rows[0][2][key], rows[1][2][key]
        print(f"{name:<26}{a:>12.3f}{b:>12.3f}{b-a:>+12.3f}")
    for name, key, fmt in [("瞳距 px", "iod_px", ".1f"), ("蒙版覆盖", "mask_coverage", ".1%"),
                           ("皮肤改动级", "skin_diff", ".2f"), ("身份相似度", "identity_sim", ".5f")]:
        a, b = rows[0][3].get(key), rows[1][3].get(key)
        if a is None or b is None:
            print(f"{name:<26}{'n/a':>12}{b if b is not None else 'n/a':>12}{'—':>12}")
        elif fmt.endswith("%"):
            print(f"{name:<26}{a*100:>11.1f}%{b*100:>11.1f}%{(b-a)*100:>+11.1f}pp")
        else:
            print(f"{name:<26}{a:>12}{b:>12}{b-a:>+12.3f}")

    # 2 行 × 2 列：每行是 baseline / result 的脸部特写（统一放大尺度）
    cell = 340
    canvas = Image.new("RGB", (cell * 2 + 30, cell * 2 + 90), "white")
    dr = ImageDraw.Draw(canvas)
    for ri, (label, meta, extra, rep, base, out) in enumerate(rows):
        for ci, (sub, img) in enumerate([("基线（未美颜）", base), ("出图（美颜后）", out)]):
            box = _face_bbox(img)
            face = img.crop(box).resize((cell, cell), Image.LANCZOS)
            x, y = 10 + ci * (cell + 10), 40 + ri * (cell + 20)
            canvas.paste(face, (x, y))
            dr.text((x + 4, y - 16), f"{label} / {sub}", fill="black", font=_cjk_font(13))
    dr.text((10, 8), "P0 A/B  ·  同一张样张  ·  脸部特写  ·  上=改动前 / 下=改动后",
            fill="black", font=_cjk_font(15))
    path = os.path.join(OUT_DIR, "p0_final_compare.jpg")
    canvas.save(path, quality=95)
    print(f"\n对比图已保存: {path}")


def remetrics(tags, pname="new"):
    """按当前口径从**已保存的 pair 图**重算指标，不重跑 pipeline.

    用途：口径（脸部选区、色斑定义）修正后，历史 P0/P1 出图仍是真实成片，
    只需要用同一把尺子重新量一遍，避免「新尺量 P1、旧尺量 P0」的假对比。
    报告、warnings、耗时等非指标字段原样保留。
    """
    for tag in tags:
        path = os.path.join(OUT_DIR, f"{tag}_{pname}.json")
        if not os.path.exists(path):
            print(f"[skip] 缺文件 {path}")
            continue
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        base, out = _load_pair(tag, pname)
        fresh = _metrics(base, out)
        meta.update(fresh)
        meta["metrics_caliber"] = "haar_ascii_fallback_v2"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"[{tag}/{pname}] 重算: face_px={fresh['face_pixels']} "
              f"ΔL={fresh['dL']:+.2f} 区域色差降={fresh['zone_spread_drop']:+.1%} "
              f"色斑降={fresh['spot_drop']:+.1%} 皮肤像素={fresh['spot_skin_px']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="before / after")
    ap.add_argument("--params", default="old", help="old / new / both")
    ap.add_argument("--summary", action="store_true", help="汇总四组结果并产出对比图")
    ap.add_argument("--remetrics", nargs="?", const="p0base,p1final", metavar="TAGS",
                    help="仅按当前口径重算指标（逗号分隔 tag，默认 p0base,p1final）")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    if args.remetrics:
        remetrics([t.strip() for t in args.remetrics.split(",") if t.strip()], args.params)
        return
    if args.summary:
        summary()
        return
    if not args.tag:
        ap.error("--tag is required unless --summary / --remetrics is used")
    names = ["old", "new"] if args.params == "both" else [args.params]
    for name in names:
        m = run_one(args.tag, name)
        r = m.get("beautify_report", {})
        print(f"[{args.tag}/{name}] "
              f"dL={m['dL']:+.2f} db={m['db']:+.2f} meanAbs={m['mean_abs']:.2f} "
              f"p95={m['p95_abs']:.2f} HF={m['hf_ratio']:.3f} "
              f"zoneDrop={m['zone_spread_drop']:+.1%} "
              f"| mode={r.get('mode')} faces={r.get('landmark_faces')} "
              f"iod={r.get('iod_px')} mask={r.get('mask_coverage')} "
              f"work={r.get('work_size')} t={m['elapsed_s']}s")
        if m["warnings"]:
            print("   warnings:", " | ".join(m["warnings"])[:300])


if __name__ == "__main__":
    main()
