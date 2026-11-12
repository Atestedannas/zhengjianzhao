# ============================================================================
# beautify 分卷 03/11 · 03_detection.py
# 职责: 算子与模型配置 / ONNX 解析 / Haar 人脸框 / MediaPipe 关键点
# 来源: 原 image_engine/beautify.py L493-L860
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================



# 引导滤波的方差阈值（guide 取值 0~255，阈值 ≈ (可平滑的亮度差 std)^2）
_EPS_FINE = 90.0     # ≈ std 9.5，小于它的起伏（毛孔/噪点）会被抹平
_EPS_MID = 520.0     # ≈ std 22.8（原 320）：阈值越大越保留鼻影/颧骨等中频明暗，
                     # 光影立体感直接由此决定；磨皮只压斑点半径内的起伏
_EPS_CLARITY = 500.0

# ---- ① 磨皮基底算子 ----
# surface = 表面模糊（保边、不做局部线性拟合，磨皮后仍保留面部体积感）；
# guided  = 旧引导滤波，保留以便 A/B 对比与一键回滚。
SMOOTH_OPERATOR = "surface"
# 【2026-10 关键修复】sigma_color 曾为 10.0，导致磨皮整条链路空转。
#
# 先把磨皮的代数关系摆清楚（已用代码验算，max|diff|<2e-5）：
#     high = L - base_fine ;  mid = base_fine - base_mid
#     l_out = base_mid + high*atten + mid  ≡  base_fine + (L-base_fine)*atten
# 即：**磨皮上限完全由 base_fine 决定**，atten 只是「用多少」。
#     atten=1 → 原图；atten=0 → 完全取 base_fine。
# 实测不同 base_fine 能力（295×413 证件照，皮肤区高频压缩率）：
#     sigma=10  r=2  →   2.7%   ← 旧值，等于没磨皮
#     sigma=45  r=2  →  29.3%   ← 上一版修复，仍偏弱
#     sigma=90  r=5  →  71.9%   ← 本次采用，接近 Photoshop 表面模糊的观感
#     Gaussian s=3   →  86.7%   ← 过强，毛孔会被抹平
# 选 sigma=90 / r=5：能真正抹平痘印与粗糙感，同时保住毛孔尺度纹理。
# 半径随图幅自适应（小图 r=5 已覆盖 10px 尺度，大图按比例放大）。
_SURFACE_SIGMA_FINE = 90.0   # 高频 range 阈值（毛孔/痘印）
_SURFACE_SIGMA_MID = 22.0    # 中频 range 阈值（斑点/色块，阈值越大越保结构）
_SURFACE_MAX_WORK_R = 12     # 降采样后的工作半径上限：把代价锁在 d≤25 的邻域内
_SURFACE_MAX_STEP = 4        # 降采样上限 1/4（原 1/8）：降得越深保边性被抹得越平；
                             # 中频基底已改走全分辨率引导滤波（见 _retouch），此处只是兜底
# 细频磨皮半径比例（相对瞳距）。旧值 0.032×iod 在 295×413 上只有 2px，
# 覆盖不到痘印尺度（3~6px）；提到 0.055×iod（小图≈4px，3000px 大图≈27px），
# 与 sigma=90 配合才能真正压掉粗糙感。
_FINE_RADIUS_RATIO = 0.055
_FINE_RADIUS_MIN = 3

# 【磨皮门控阈值】gate = _ramp(|high|, GATE_LO, GATE_HI)
# 低于 GATE_LO 的起伏视为「毛孔/噪点」，全额压制；高于 GATE_HI 的视为
# 「褶皱/轮廓/痣」，原样保留。
#
# 【标定过程（记录踩过的坑）】
# (2.5, 14.0) 按 3000px 大图标定：大图皮肤高频 std 达 8~15 级，合理；
#   但证件照主路径是 295×413，缩图后高频 std 仅 1.3 级 → gate 恒为 0，
#   磨皮彻底失效（实测 removed 仅 0.066 级）。
# (0.8, 8.0)：sigma 提到 90、r_fine 提到 4px 后 high 的 std 涨到 7.4，
#   又顶到 GATE_HI=8 附近，gate 只剩 0.233，磨皮再次被挡住。
# (1.0, 22.0)：发现 GATE_LO=1 是硬伤 —— 8-bit L 通道最小非零差就是 1，
#   实测 |high| 中位数恰好 =1.000，而 _ramp 在 x<=lo 时返回 0，
#   于是**一半像素 gate 恒为 0**。
#
# 现在改为**按图像实际高频尺度自适应**（见 _smooth_gate），不再写死绝对阈值：
# 因为「毛孔多大」本身随图幅、压缩、肤质变化 —— 295×413 证件照的毛孔
# 高频只有 1~3 级，而合成测试图或 3000px 原片能到 7~15 级。
# 固定阈值必然在某一类图上失效，这正是过去三次标定都失败的根本原因。
_SMOOTH_GATE_LO = 0.0      # 保留字段：自适应时不再使用（见 _smooth_gate）
_SMOOTH_GATE_HI = 22.0     # 同上
# 自适应门控：以皮肤区 |high| 的 P90 作为「结构」基准，
# GATE_HI = P90 × 该系数（>1 保证真结构不被压），GATE_LO = 0。
_SMOOTH_GATE_P90_RATIO = 1.15
# 兜底：P90 极小时（纯色皮肤）用这个绝对下限，避免 gate 除零或全压
_SMOOTH_GATE_HI_MIN = 4.0

_MAX_FACES = 4
_ENABLE_NECK = True  # 是否把下颌→脖子也纳入美颜（避免脸白脖子黄的接缝）


# ============================================================ 人脸解析 (SegFormer)
# 19 类人脸解析（SegFormer-mit-b5，CelebAMask-HQ 微调，ONNX 量化版，约 85MB）。
# 相比 BiSeNet：同是 19 类人脸解析，但 HuggingFace 上有现成 ONNX 可直接加载，
# 无需本地装 torch 转换；精度（mit-b5 骨干）也优于 BiSeNet-resnet18。
# 类别顺序与 zllrunning/BiSeNet 不同，见下方 _PARSE_SKIN_CLASSES。

_PARSE_MODEL_FILE = "face_parsing_segformer.onnx"
_PARSE_INPUT = 512                                   # 整图缩放到 512x512 送网络（几何见 _parse_skin_mask）
_PARSE_SKIN_CLASSES = (1, 2, 16, 17)                 # skin / nose / neck_l / neck
_PARSE_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_PARSE_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_PARSE_DISABLED = False                              # 模型缺失/加载失败后永久降级


def _parse_model_path() -> str:
    """返回 face_parsing_segformer.onnx 的绝对路径（backend/models/ 下）."""
    global _MODELS_DIR
    if _MODELS_DIR is None:
        _MODELS_DIR = os.path.join(
            str(_BACKEND_DIR),
            "models",
        )
    return os.path.join(_MODELS_DIR, _PARSE_MODEL_FILE)


@lru_cache(maxsize=1)
def _get_parse_session():
    """人脸解析 ONNX 会话单例（onnxruntime session 可并发复用）."""
    import onnxruntime as ort

    path = _parse_model_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"face parsing model not found at {path}; beautify falls back to "
            f"geometric + skin-color mask."
        )
    available = set(ort.get_available_providers())
    providers = [p for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider", "CPUExecutionProvider")
                 if p in available]
    if not providers:
        providers = ["CPUExecutionProvider"]
    sess = ort.InferenceSession(path, providers=providers)
    _get_parse_session.input_name = sess.get_inputs()[0].name
    _get_parse_session.output_name = sess.get_outputs()[0].name
    logger.info(f"Face parsing model ready (SegFormer-mit-b5, 19 classes): {path}")
    return sess


def _parse_skin_mask(bgr: np.ndarray,
                     face_boxes: Optional[Sequence[Tuple[int, int, int, int]]] = None
                     ) -> Optional[np.ndarray]:
    """
    用 19 类人脸解析得到精确皮肤蒙版 (0~1，与原图同尺寸).

    皮肤 = skin/nose/neck 类；唇/眼/眉/发/衣服天然不在集合内，
    因此能比「几何多边形 ∩ 肤色概率」更精确地保护唇色与五官边缘。

    【2026-11 关键修复：必须先裁到人脸再解析】
    旧实现把**整图**缩放到 512×512 送进网络。证件照场景下人脸通常只占画面
    8%~20%（1680×2240 原片里脸只有 199×264），缩到 512 后脸只剩 60×80，
    解析网络（在 CelebAMask-HQ 的 512 人脸裁图上训练）根本认不出五官级结构：
    实测整图解析在「脸型多边形内」的皮肤覆盖率只有 **0.24**，
    而先裁到人脸框（外扩 0.55×脸尺寸）再解析，覆盖率 **0.78** —— 差 3 倍多。
    覆盖率低的直接后果：皮肤蒙版大面积缺失 → 磨皮/匀肤/提亮全都被
    「蒙版 × 0」乘掉，用户看到的就是「美颜没效果」。这是历史上反复
    「加参数但不生效」的结构性根因，与算子强度无关。

    做法：按传入的 `face_boxes`（或内部检测的关键点框）裁人脸区域 →
    解析 → 把 128×128 标签图双线性放大回裁切尺寸 → 贴回整图坐标。
    `face_boxes` 为 None 且检测不到人脸时，退化为「整图解析」（旧行为）。

    任何失败（缺模型 / 缺 onnxruntime / 推理异常）返回 None，调用方降级到旧蒙版.
    """
    import cv2

    global _PARSE_DISABLED
    if _PARSE_DISABLED:
        return None

    try:
        sess = _get_parse_session()
    except Exception as e:
        _PARSE_DISABLED = True
        logger.warning(f"Face parsing disabled ({e}); using skin-color mask fallback.")
        return None

    h, w = bgr.shape[:2]

    def _run_parse(rgb_region: np.ndarray) -> np.ndarray:
        """对一块 RGB 区域做解析，返回同尺寸的皮肤概率图 (0~1)."""
        x_img = cv2.resize(rgb_region, (_PARSE_INPUT, _PARSE_INPUT),
                           interpolation=cv2.INTER_LINEAR).astype(np.float32)
        x = (x_img / 255.0 - _PARSE_MEAN) / _PARSE_STD
        x = np.transpose(x, (2, 0, 1))[None].astype(np.float32)
        out = sess.run([_get_parse_session.output_name],
                       {_get_parse_session.input_name: x})[0]   # 1,19,h,w
        labels = np.argmax(out, axis=1)[0]                       # h,w
        skin = np.isin(labels, _PARSE_SKIN_CLASSES).astype(np.float32)
        return cv2.resize(skin, (rgb_region.shape[1], rgb_region.shape[0]),
                          interpolation=cv2.INTER_LINEAR)

    try:
        rgb = bgr[:, :, ::-1]

        if face_boxes is None:
            face_boxes = _detect_face_boxes(bgr)

        if not face_boxes:
            # 退化路径：整图解析（几何正确，但小脸场景精度有限）
            mask = _run_parse(rgb)
            return np.clip(mask, 0.0, 1.0).astype(np.float32)

        mask = np.zeros((h, w), dtype=np.float32)
        for (bx0, by0, bx1, by1) in face_boxes:
            # 裁切框做边界夹紧，并保证最小边长（太小解析无意义）
            cx0, cy0 = max(0, int(bx0)), max(0, int(by0))
            cx1, cy1 = min(w, int(bx1)), min(h, int(by1))
            if cx1 - cx0 < 16 or cy1 - cy0 < 16:
                continue
            sub_skin = _run_parse(rgb[cy0:cy1, cx0:cx1])
            # 多张脸重叠时取最大值（不是相加，避免蒙版 >1）
            np.maximum(mask[cy0:cy1, cx0:cx1], sub_skin, out=mask[cy0:cy1, cx0:cx1])

        if float(mask.max()) < 0.05:
            logger.debug("Face-crop parsing produced empty skin mask; retry whole image.")
            mask = _run_parse(rgb)
        return np.clip(mask, 0.0, 1.0).astype(np.float32)
    except Exception as e:
        logger.warning(f"Face parsing inference failed ({e}); using skin-color mask fallback.")
        return None


# 人脸裁切框的外扩比例（相对人脸长边）。
# 【为什么是 0.55】实测 0.35/0.55/0.80 三档的解析质量几乎相同
# （脸型内皮肤覆盖率 0.779/0.776/0.779），说明外扩只要「够把脸和一点脖子
# 包进去」就够；再放大只是让 512 分辨率摊到更多无用像素上（脸反而变小）。
# 取 0.55 兼顾「多脸同框」与「脸贴边」两种极端构图。
_PARSE_CROP_PAD = 0.55


def _box_from_landmarks(lm, w: int, h: int) -> Tuple[int, int, int, int]:
    """单张脸的关键点包络 → 解析裁切框（含外扩，见 _PARSE_CROP_PAD）."""
    xs = [p.x for p in lm]
    ys = [p.y for p in lm]
    fx0, fx1 = min(xs) * w, max(xs) * w
    fy0, fy1 = min(ys) * h, max(ys) * h
    pad = max(fx1 - fx0, fy1 - fy0) * _PARSE_CROP_PAD
    return (int(max(0, fx0 - pad)), int(max(0, fy0 - pad)),
            int(min(w, fx1 + pad)), int(min(h, fy1 + pad * 1.35)))


def _detect_face_boxes(bgr: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """
    检测人脸裁切框（含外扩），供人脸解析使用.

    【为什么优先关键点而不是 Haar】Haar 的框比真实脸小一圈且常偏上；
    关键点包络更准，且下游本来就要跑关键点（结果复用，不额外耗时）。
    返回 [(x0, y0, x1, y1), ...]，坐标已夹紧在图内。
    """
    import cv2

    h, w = bgr.shape[:2]
    boxes: List[Tuple[int, int, int, int]] = []

    for lm in _detect_all_landmarks(bgr):
        boxes.append(_box_from_landmarks(lm, w, h))

    if boxes:
        return boxes

    # 降级：Haar 人脸框（比关键点粗糙，但总比整图解析好）
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    cascade_path = os.path.join(cv2.data.haarcascades,
                                "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return []
    min_size = max(24, min(h, w) // 20)
    found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                     minSize=(min_size, min_size))
    for (x, y, bw, bh) in found[:_MAX_FACES]:
        pad = max(bw, bh) * _PARSE_CROP_PAD
        boxes.append((int(max(0, x - pad)), int(max(0, y - pad)),
                      int(min(w, x + bw + pad)), int(min(h, y + bh + pad * 1.35))))
    return boxes


# ============================================================ FaceLandmarker

_LANDMARKER = None
_LANDMARKER_DISABLED = False
_DETECT_LOCK = threading.Lock()
_MODELS_DIR: Optional[str] = None


def _model_path() -> str:
    """返回 face_landmarker.task 的绝对路径（backend/models/ 下）."""
    global _MODELS_DIR
    if _MODELS_DIR is None:
        _MODELS_DIR = os.path.join(
            str(_BACKEND_DIR),
            "models",
        )
    return os.path.join(_MODELS_DIR, "face_landmarker.task")


def _get_landmarker():
    """模块级单例 FaceLandmarker；不可用时只告警一次并永久降级."""
    global _LANDMARKER, _LANDMARKER_DISABLED
    if _LANDMARKER is not None:
        return _LANDMARKER
    if _LANDMARKER_DISABLED:
        return None
    try:
        import mediapipe as mp  # noqa: F401
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        path = _model_path()
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"face_landmarker.task not found at {path}. "
                f"Please place the model in backend/models/."
            )
        # 用内存缓冲加载（MediaPipe C++ 后端无法打开含非 ASCII 字符的路径）
        with open(path, "rb") as f:
            model_buffer = f.read()
        base_options = mp_python.BaseOptions(model_asset_buffer=model_buffer)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_faces=_MAX_FACES,
            min_face_detection_confidence=0.4,
            min_face_presence_confidence=0.4,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        _LANDMARKER = vision.FaceLandmarker.create_from_options(options)
        logger.info("MediaPipe FaceLandmarker ready (478 landmarks).")
    except Exception as e:  # 缺包 / 缺模型 / 版本不兼容 → 永久降级
        _LANDMARKER_DISABLED = True
        logger.warning(
            f"MediaPipe FaceLandmarker unavailable ({e}); "
            f"beautify degrades to Haar face box + skin-color mask."
        )
    return _LANDMARKER


# MediaPipe FaceMesh 关键点索引（468 点体系，478 点模型额外含虹膜 468~477）
_FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365,
              379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93,
              234, 127, 162, 21, 54, 103, 67, 109]
_LEFT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
_RIGHT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
_LEFT_EYEBROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
_RIGHT_EYEBROW = [336, 296, 334, 293, 300, 276, 283, 282, 295, 285]
_LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
_NOSTRILS = [97, 328]          # 左右鼻孔中心
_CHIN = 152
_JAW_LEFT = 234
_JAW_RIGHT = 454


def _detect_all_landmarks(image: np.ndarray) -> List[list]:
    """
    检测所有人脸关键点（BGR 输入）.

    返回:
        按脸面积降序排列的关键点列表；无人脸或模型不可用返回 [].
    """
    landmarker = _get_landmarker()
    if landmarker is None:
        return []
    try:
        import mediapipe as mp

        rgb = np.ascontiguousarray(image[:, :, ::-1])
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        # FaceLandmarker 实例非线程安全，服务端并发请求需要串行化
        with _DETECT_LOCK:
            res = landmarker.detect(mp_image)
    except Exception as e:
        logger.warning(f"Face landmark detection failed: {e}")
        return []

    if not res.face_landmarks:
        return []
    faces = list(res.face_landmarks)
    faces.sort(key=_landmark_span, reverse=True)
    return faces[:_MAX_FACES]


def _landmark_span(landmarks) -> float:
    """关键点包络面积（用于挑最大脸）."""
    xs = [lm.x for lm in landmarks]
    ys = [lm.y for lm in landmarks]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


# ============================================================ 基础算子

