"""裁剪模块：按目标比例中心裁剪 + OpenCV 人脸检测辅助定位."""

from typing import Tuple

from PIL import Image
from loguru import logger

from app.core.image_engine.cascade import frontal_face_cascade


def _detect_face_center(image: Image.Image) -> Tuple[int, int]:
    """
    使用 OpenCV 人脸检测定位人脸中心.
    返回 (cx, cy)，若未检测到返回图像中心.
    """
    import cv2
    import numpy as np

    img_cv = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)

    # 中文安装路径下 CascadeClassifier 会静默 empty()；统一用带 ASCII 兜底的加载器
    face_cascade = frontal_face_cascade()

    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # minSize 随图像尺寸动态计算（小图/小脸不漏检），与 face_detect / beautify 保持同一方案
    h, w = gray.shape[:2]
    min_size = max(30, min(w, h) // 15)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size))

    if len(faces) > 0:
        # 取最大的脸
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        cx, cy = x + w // 2, y + h // 2
        logger.debug(f"Face detected at ({cx}, {cy}) size ({w}x{h})")
        return cx, cy

    logger.debug("No face detected, using center crop.")
    return image.width // 2, image.height // 2


def resize_crop(
    image: Image.Image, target_w: int, target_h: int,
    use_face_detection: bool = True,
) -> Image.Image:
    """
    按目标比例裁剪并 resize 到精确像素.
    - 使用目标宽高比作为裁剪比例
    - 若启用人脸检测且检测到人脸，裁剪区域优先包含人脸居中
    - 否则按图像几何中心裁剪
    返回裁剪并 resize 后的 PIL.Image (RGB).
    """
    target_ratio = target_w / target_h
    img_w, img_h = image.size
    img_ratio = img_w / img_h

    # 确定裁剪区域
    if img_ratio > target_ratio:
        # 原图更宽 → 裁剪左右
        crop_h = img_h
        crop_w = int(crop_h * target_ratio)
    else:
        # 原图更高 → 裁剪上下
        crop_w = img_w
        crop_h = int(crop_w / target_ratio)

    # 确定人脸或中心位置
    if use_face_detection:
        try:
            face_cx, face_cy = _detect_face_center(image)
        except Exception as e:
            logger.warning(f"Face detection failed: {e}, fallback to center.")
            face_cx, face_cy = img_w // 2, img_h // 2
    else:
        face_cx, face_cy = img_w // 2, img_h // 2

    # 裁剪框左/上边界
    left = max(0, min(face_cx - crop_w // 2, img_w - crop_w))
    top = max(0, min(face_cy - crop_h // 2, img_h - crop_h))
    right, bottom = left + crop_w, top + crop_h

    cropped = image.crop((left, top, right, bottom))
    result = cropped.resize((target_w, target_h), Image.LANCZOS)

    logger.info(f"Crop: {img_w}x{img_h} -> ({left},{top},{right},{bottom}) -> resize {target_w}x{target_h}")
    return result


def face_aware_crop(
    image: Image.Image, target_w: int, target_h: int,
    face_width_ratio: float = 0.65,
    top_margin_ratio: float = 0.08,
) -> Image.Image:
    """按人脸比例构造裁剪框，输出目标像素（保持目标宽高比，绝不拉伸）.

    【为什么需要它】resize_crop 只做「按目标比例居中裁切」，不保证脸在输出里
    足够大。用户上传的全身/半身照里脸可能只占 8%，缩到 295×413 后瞳距不到
    20px —— MediaPipe 关键点检不出、皮肤解析模型（512 人脸裁图训练）也失效，
    美颜只能走 fallback，效果近乎为零。这是「时好时坏」的几何根因。

    【做法】用 Haar 人脸框反推裁剪框：让脸宽占输出宽度 face_width_ratio
    （默认 65%，与证件照构图红线一致），头顶留白 top_margin_ratio（默认 8%）。
    裁剪框超界时只做平移、绝不缩放窗口（缩放窗口会改变脸宽比例并拉伸像素）。
    检测不到人脸时退回 resize_crop，保证行为不退化。

    注意：本函数输出的是**工作分辨率**的图（由调用方给定 target_w/target_h），
    供美颜在原生尺度上执行；之后再统一缩到真正的证件照像素。
    """
    # 局部 import：face_detect.align_face 内部也会 import crop，顶层互相 import 会成环
    from app.core.image_engine.face_detect import detect_faces

    try:
        faces = detect_faces(image)
    except Exception as e:  # noqa: BLE001 —— 检测失败不能阻断出图
        logger.warning(f"face_aware_crop: detection failed ({e}), fallback to resize_crop.")
        return resize_crop(image, target_w, target_h)

    if not faces:
        logger.info("face_aware_crop: no face, fallback to resize_crop (center).")
        return resize_crop(image, target_w, target_h)

    f = max(faces, key=lambda t: t.width * t.height)
    img_w, img_h = image.size
    aspect = float(target_w) / float(target_h)

    crop_w = float(f.width) / max(0.30, min(0.80, face_width_ratio))
    crop_h = crop_w / aspect
    # 窗口不得大于原图能提供的范围（否则会拉伸/凭空外扩）
    fit = min(img_w / crop_w, img_h / crop_h, 1.0)
    crop_w, crop_h = crop_w * fit, crop_h * fit

    head_top = max(0.0, f.y - 0.55 * f.height)
    cx = f.x + f.width / 2.0
    left = cx - crop_w / 2.0
    top = head_top - top_margin_ratio * crop_h

    # 只做平移把窗口收回图内
    left = max(0.0, min(left, img_w - crop_w))
    top = max(0.0, min(top, img_h - crop_h))
    box = (
        int(round(left)), int(round(top)),
        int(round(left + crop_w)), int(round(top + crop_h)),
    )
    box = (box[0], box[1], min(img_w, box[2]), min(img_h, box[3]))
    if box[2] - box[0] < 32 or box[3] - box[1] < 32:
        return resize_crop(image, target_w, target_h)

    result = image.crop(box).resize((target_w, target_h), Image.LANCZOS)
    logger.info(
        f"Face-aware crop: face {f.width}x{f.height}@{f.x},{f.y} -> {box} "
        f"-> {target_w}x{target_h} (face width ~{face_width_ratio:.0%})"
    )
    return result

