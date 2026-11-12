"""OpenCV Haar 级联加载工具（跨中文/非 ASCII 安装路径可用）.

【为什么要单独一个模块】cv2.data.haarcascades 指向的是 OpenCV 包自身目录。
在 Windows 中文安装路径下（本机 D:\\workpace\\博客\\...），OpenCV 4.x 的
FileStorage 打不开含非 ASCII 字符的路径：

    [ERROR] Can't open file 'D:\\...\\博客\\...\\haarcascade_frontalface_default.xml'
    CascadeClassifier.empty() == True

于是 detect_faces() 恒返回空 → 证件照对齐被静默跳过、crop 的人脸辅助定位
退化成几何中心裁切；更下游的「美颜要求脸足够大」前置条件随之中断。这就是
「同一份代码在服务器正常、在本地完全没效果」的直接原因。

修法：优先按原路径加载；失败时把 XML 复制到系统临时目录（纯 ASCII）再加载。
一次加载后缓存，运行期零额外开销。
"""

import os
import shutil
import tempfile

import cv2
from loguru import logger

_CASCADE_NAME = "haarcascade_frontalface_default.xml"
_cached = None


def frontal_face_cascade():
    """返回可用的正面人脸 Haar 级联；彻底不可用时抛 RuntimeError（不静默）.

    不静默是刻意的：过去加载失败表现为「0 张脸」，调用方无法区分
    「照片里没人」与「模型没加载」，只能一路降级出图。
    """
    global _cached
    if _cached is not None and not _cached.empty():
        return _cached

    src = cv2.data.haarcascades + _CASCADE_NAME
    cascade = cv2.CascadeClassifier(src)
    if not cascade.empty():
        _cached = cascade
        return cascade

    # 非 ASCII 路径兜底：系统临时目录一定是 ASCII
    tmp_dir = os.path.join(tempfile.gettempdir(), "idphoto_cv2_cascades")
    os.makedirs(tmp_dir, exist_ok=True)
    dst = os.path.join(tmp_dir, _CASCADE_NAME)
    if not os.path.exists(dst):
        if not os.path.exists(src):
            raise RuntimeError(f"Haar cascade not found: {src}")
        shutil.copyfile(src, dst)
    cascade = cv2.CascadeClassifier(dst)
    if cascade.empty():
        raise RuntimeError(f"Haar cascade failed to load even from ASCII path: {dst}")
    logger.warning(
        f"Haar cascade reloaded from ASCII fallback ({dst}); "
        f"original cv2.data path contains non-ASCII characters: {src}"
    )
    _cached = cascade
    return cascade
