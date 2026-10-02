"""裁剪模块：按目标比例中心裁剪 + OpenCV 人脸检测辅助定位."""

from typing import Tuple

from PIL import Image
from loguru import logger


def _detect_face_center(image: Image.Image) -> Tuple[int, int]:
    """
    使用 OpenCV 人脸检测定位人脸中心.
    返回 (cx, cy)，若未检测到返回图像中心.
    """
    import cv2
    import numpy as np

    img_cv = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)

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
