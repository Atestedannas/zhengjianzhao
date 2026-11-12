# ============================================================================
# beautify 分卷 05/11 · 05_masks.py
# 职责: 人脸几何 / 皮肤概率 / 区域蒙版 / 五官带 / 结构保护
# 来源: 原 image_engine/beautify.py L1026-L1446
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================

# ============================================================ 人脸几何

@dataclass
class _FaceGeometry:
    """用于把比例换算成像素尺度的人脸几何量."""
    cx: float
    cy: float
    width: float
    height: float
    iod: float  # 瞳距（尺度基准）


def _geometry_from_landmarks(landmarks, w: int, h: int) -> _FaceGeometry:
    oval = _pts(landmarks, _FACE_OVAL, w, h)
    if len(oval) < 3:
        return _FaceGeometry(w / 2.0, h / 2.0, w * 0.3, h * 0.3, max(8.0, w * 0.1))

    x0, y0 = oval.min(axis=0)
    x1, y1 = oval.max(axis=0)

    left_eye = _pts(landmarks, _LEFT_EYE, w, h)
    right_eye = _pts(landmarks, _RIGHT_EYE, w, h)
    iod = 0.0
    if len(left_eye) and len(right_eye):
        iod = float(np.linalg.norm(left_eye.mean(axis=0) - right_eye.mean(axis=0)))
    if iod < 1.0:  # 兜底：瞳距 ≈ 脸宽 1/3
        iod = max(1.0, float(x1 - x0) * 0.33)

    return _FaceGeometry(
        cx=float((x0 + x1) / 2.0), cy=float((y0 + y1) / 2.0),
        width=float(x1 - x0), height=float(y1 - y0), iod=iod,
    )


def _large_geometry(geometries: Sequence[_FaceGeometry]) -> _FaceGeometry:
    return max(geometries, key=lambda g: g.width * g.height)


# ============================================================ 蒙版构建

def _skin_prob(bgr: np.ndarray) -> np.ndarray:
    """
    肤色概率图 (0~1)：YCbCr 软阈值.

    用软阈值而不是硬阈值，是为了让蒙版边缘本来就有过渡，
    再叠加引导滤波羽化后不会出现明显的“美颜边界”.
    """
    import cv2

    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    y = ycrcb[:, :, 0].astype(np.float32)
    cr = ycrcb[:, :, 1].astype(np.float32)
    cb = ycrcb[:, :, 2].astype(np.float32)

    p = _ramp(cr, 130.0, 141.0) * (1.0 - _ramp(cr, 172.0, 188.0))
    p = p * _ramp(cb, 72.0, 84.0) * (1.0 - _ramp(cb, 126.0, 140.0))
    p = p * _ramp(y, 30.0, 55.0)  # 过暗（头发/阴影）不算皮肤
    return p.astype(np.float32)


def _reference_skin_stats(lab: np.ndarray, region: np.ndarray,
                          color: np.ndarray) -> Tuple[float, float]:
    """统计人脸区域内的参考亮度/红度中位数（用于相对阈值）."""
    l_chan = lab[:, :, 0].astype(np.float32)
    a_chan = lab[:, :, 1].astype(np.float32)
    sel = (region > 0) & (color > 0.6)
    if int(sel.sum()) < 200:
        sel = region > 0
    if int(sel.sum()) < 50:
        return 140.0, 128.0
    return float(np.median(l_chan[sel])), float(np.median(a_chan[sel]))


def _refine_skin_mask(bgr: np.ndarray, region: np.ndarray,
                      geo: _FaceGeometry, lab: np.ndarray,
                      parse_skin: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """
    把人脸区域精炼成“只有皮肤”的羽化浮点蒙版 (0~1).

    优先用 parse_skin（19 类人脸解析得到的精确皮肤）∩ 几何人脸区域；
    解析不可用时降级为：外沿收缩 → ∩肤色概率 → 抠暗部（眼/眉/鼻孔/头发）
    → 抠唇部。两条路径最后都做形态学去碎点补空洞 + 引导滤波羽化.
    """
    import cv2

    if region is None or int(region.max()) == 0:
        return None

    if parse_skin is not None:
        # 解析路径：精确皮肤 ∩ 几何人脸区域（不再依赖肤色/亮度/唇部启发式）
        soft = (region.astype(np.float32) / 255.0) * parse_skin
    else:
        # 外沿收缩：避开头发与面部轮廓外
        region = cv2.erode(region, _ellipse_kernel(_scale(geo.iod, 0.06, 1)))

        color = _skin_prob(bgr)
        med_l, med_a = _reference_skin_stats(lab, region, color)

        l_chan = lab[:, :, 0].astype(np.float32)
        a_chan = lab[:, :, 1].astype(np.float32)

        # 相对亮度：明显暗于皮肤中位数的像素（眉毛/睫毛/眼窝/头发）排除
        lit = _ramp(l_chan, 0.34 * med_l, 0.55 * med_l)
        # 相对红度：明显偏红的像素（嘴唇/唇线）排除
        non_lip = 1.0 - _ramp(a_chan - med_a, 18.0, 38.0)

        soft = (region.astype(np.float32) / 255.0) * color * lit * non_lip

    m = (soft * 255.0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, _ellipse_kernel(_scale(geo.iod, 0.06, 1)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, _ellipse_kernel(_scale(geo.iod, 0.02, 1)))
    m = m.astype(np.float32) / 255.0

    if float(m.max()) < 0.05:
        return None

    # 按图像梯度羽化：过渡带贴着头皮/眉毛的真实边缘，而不是几何多边形的硬边
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    # 蒙版羽化的半径很小（0.12×iod），Fast 版会自动退回精确路径，这里直接调用即可
    m = _guided_1_fast(gray, m, _scale(geo.iod, 0.12, 2), 4e-4)
    return np.clip(m, 0.0, 1.0).astype(np.float32)


def _neck_polygon(landmarks, w: int, h: int, geo: _FaceGeometry) -> Optional[np.ndarray]:
    """下颌 → 脖子梯形区域（用肤色蒙版兜底，避免脸/脖子色差接缝）."""
    if not _ENABLE_NECK or len(landmarks) <= max(_JAW_LEFT, _JAW_RIGHT, _CHIN):
        return None

    chin = np.array([landmarks[_CHIN].x * w, landmarks[_CHIN].y * h], dtype=np.float32)
    jaw_l = np.array([landmarks[_JAW_LEFT].x * w, landmarks[_JAW_LEFT].y * h], dtype=np.float32)
    jaw_r = np.array([landmarks[_JAW_RIGHT].x * w, landmarks[_JAW_RIGHT].y * h], dtype=np.float32)

    jaw_w = float(np.linalg.norm(jaw_r - jaw_l))
    depth = min(h - 1.0 - chin[1], geo.iod * 1.7)
    if jaw_w < 4.0 or depth < geo.iod * 0.25:
        return None

    cx = float((jaw_l[0] + jaw_r[0]) / 2.0)
    half_top = jaw_w * 0.35
    half_bot = half_top * 1.15
    y0 = chin[1] - geo.iod * 0.06      # 与下巴重叠，避免出现未处理的细缝
    y1 = chin[1] + depth
    return np.array([
        [cx - half_top, y0], [cx + half_top, y0],
        [cx + half_bot, y1], [cx - half_bot, y1],
    ], dtype=np.int32)


def _landmark_region_mask(bgr: np.ndarray, faces: List[list],
                          parse_skin: Optional[np.ndarray] = None) -> Tuple[Optional[np.ndarray], _FaceGeometry]:
    """由关键点构建“待美颜区域”（含脖子）并抠除五官."""
    import cv2

    h, w = bgr.shape[:2]
    geometries = [_geometry_from_landmarks(lm, w, h) for lm in faces]
    main = _large_geometry(geometries)

    region = np.zeros((h, w), dtype=np.uint8)
    for lm, geo in zip(faces, geometries):
        oval = _pts(lm, _FACE_OVAL, w, h)
        if len(oval) >= 3:
            cv2.fillPoly(region, [oval.astype(np.int32)], 255)
        neck = _neck_polygon(lm, w, h, geo)
        if neck is not None:
            cv2.fillPoly(region, [neck], 255)

    # 五官保护：眼 / 眉 / 唇 / 鼻孔（整体膨胀，避免锯齿边缘被磨到）
    feat = np.zeros((h, w), dtype=np.uint8)
    for lm in faces:
        for idx in (_LEFT_EYE, _RIGHT_EYE, _LEFT_EYEBROW, _RIGHT_EYEBROW, _LIPS):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(feat, [pts.astype(np.int32)], 255)
    nostril_r = _scale(main.iod, 0.05, 2)
    for lm in faces:
        for i in _NOSTRILS:
            if i < len(lm):
                cv2.circle(feat, (int(lm[i].x * w), int(lm[i].y * h)), nostril_r, 255, -1)
    feat = cv2.dilate(feat, _ellipse_kernel(_scale(main.iod, 0.10, 2)))
    region[feat > 0] = 0

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    return _refine_skin_mask(bgr, region, main, lab, parse_skin), main


def _under_eye_mask(bgr: np.ndarray, faces: List[list],
                    main: _FaceGeometry) -> Optional[np.ndarray]:
    """
    眼袋 / 泪沟带蒙版 (0~1)：下眼睑下方一条弧形带（不是整块眼周）.

    【为什么单独做一条带】眼袋和泪沟是证件照里「显疲惫、显老」的头号来源，
    但它们既不是皮肤高频噪声（磨皮压不掉），也不是色度斑块（匀肤改不动）——
    它们是**局部偏暗的凹陷阴影**，必须用「局部提亮 + 轻微去青」单独处理。
    整块眼周提亮会把卧蚕和眼型一起抹平（观感=假眼），所以只取
    「下眼睑轮廓向下 0.10~0.22×iod」这一条窄带，并按梯度保护卧蚕亮部。
    """
    import cv2

    h, w = bgr.shape[:2]
    iod = float(main.iod)
    m = np.zeros((h, w), dtype=np.uint8)

    for lm in faces:
        for idx in (_LEFT_LOWER_LID, _RIGHT_LOWER_LID):
            pts = _pts(lm, idx, w, h)
            if len(pts) < 3:
                continue
            # 把下眼睑轮廓整体下移，形成「带」，而不是填满眼下整块
            shifted = pts + np.array([0.0, iod * 0.16], dtype=np.float32)
            band = np.vstack([pts, shifted[::-1]]).astype(np.int32)
            cv2.fillPoly(m, [band], 255)

    if int(m.max()) == 0:
        return None
    # 轻微羽化：硬边会在眼下出现「两块亮斑」
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, iod * 0.03))
    return np.clip(m.astype(np.float32) / 255.0, 0.0, 1.0)


def _eye_region_mask(bgr: np.ndarray, faces: List[list], main: _FaceGeometry) -> Optional[np.ndarray]:
    """眼部（眼 + 眉）区域蒙版，用于单独锐化眼神."""
    import cv2

    h, w = bgr.shape[:2]
    m = np.zeros((h, w), dtype=np.uint8)
    for lm in faces:
        for idx in (_LEFT_EYE, _RIGHT_EYE, _LEFT_EYEBROW, _RIGHT_EYEBROW):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(m, [pts.astype(np.int32)], 255)

    if int(m.max()) == 0:
        return None
    m = cv2.dilate(m, _ellipse_kernel(_scale(main.iod, 0.05, 2)))
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, _scale(main.iod, 0.05, 2) * 0.6))
    return m.astype(np.float32) / 255.0


# 上排牙齿在嘴唇内的参考位置：嘴唇关键点中 0/13/14 是上下唇中点，
# 牙齿大致在上下唇之间的开口区；没有专门标注点，用嘴唇多边形向内收缩近似。
_INNER_LIPS = [78, 191, 80, 81, 82, 13, 312, 311, 310, 308, 95, 88, 178, 87, 14, 317, 402, 318, 324]


def _lips_region_mask(bgr: np.ndarray, faces: List[list], main: _FaceGeometry) -> Optional[np.ndarray]:
    """嘴唇（外缘羽化）蒙版，用于唇色提亮；牙齿区单独再给一个蒙版."""
    import cv2

    h, w = bgr.shape[:2]
    outer = np.zeros((h, w), dtype=np.uint8)
    inner = np.zeros((h, w), dtype=np.uint8)
    for lm in faces:
        pts = _pts(lm, _LIPS, w, h)
        if len(pts) >= 3:
            cv2.fillPoly(outer, [pts.astype(np.int32)], 255)
        ipts = _pts(lm, _INNER_LIPS, w, h)
        if len(ipts) >= 3:
            cv2.fillPoly(inner, [ipts.astype(np.int32)], 255)

    if int(outer.max()) == 0:
        return None

    # 嘴唇外缘羽化
    r = max(1.0, _scale(main.iod, 0.05, 2) * 0.8)
    outer_f = cv2.GaussianBlur(outer, (0, 0), r).astype(np.float32) / 255.0
    # 牙齿区 = 内唇多边形（张嘴时露牙的区域），羽化更轻
    teeth = cv2.GaussianBlur(inner, (0, 0), max(0.8, r * 0.6)).astype(np.float32) / 255.0
    teeth_f = np.clip(teeth, 0.0, 1.0).astype(np.float32)
    # 【修复】唇色蒙版必须扣除牙齿/口腔：否则牙齿会先被染红、再做美白，
    # 张口的照片容易出现粉牙。lips = 外唇 - 内唇（口裂）.
    lips_f = np.clip(outer_f - teeth_f, 0.0, 1.0).astype(np.float32)
    return lips_f, teeth_f


# ---- ② 分区定向祛瑕疵：把「整脸一律去瑕疵」拆成子蒙版，避免立体感被磨平 ----
# 依据：痘印/色块主要长在两颊与下巴；额头、鼻梁、鼻唇沟、下颌阴影属于
# 「立体结构」，一旦被中频压制就会显得脸平、法令纹消失。
_BLEMISH_ZONE_FOREHEAD = 0.68   # 额头（保护眉弓/抬头纹方向的结构）
_BLEMISH_ZONE_NOSE = 0.40       # 鼻梁 + 鼻唇沟 + 人中（立体感最强）
_BLEMISH_ZONE_JAW = 0.75        # 下颌/下巴阴影
_BLEMISH_ZONE_CHEEK = 1.0       # 两颊（瑕疵高发区）


def _blemish_zone_weight(mask: np.ndarray, geo: _FaceGeometry) -> np.ndarray:
    """
    生成「祛瑕疵强度」的分区权重图 (0~1)，几何化、不依赖关键点.

    以人脸框为坐标系，拆出额头 / 鼻唇中轴 / 下颌三个子蒙版并分别降权，
    其余（两颊）保持满权重；关键点路径与 Haar 降级路径都可用.
    """
    h, w = mask.shape[:2]
    # 用一维坐标广播代替 mgrid，避免大图上多占一整张 (h,w) 的坐标内存
    xs = (np.arange(w, dtype=np.float32) - float(geo.cx)) / max(float(geo.width), 1.0)
    ys = (np.arange(h, dtype=np.float32)
          - (float(geo.cy) - float(geo.height) / 2.0)) / max(float(geo.height), 1.0)
    nx = xs[None, :]
    ny = ys[:, None]

    weight = np.full((h, w), _BLEMISH_ZONE_CHEEK, dtype=np.float32)

    # 额头带：脸框上部（含眉弓）
    forehead = _ramp(ny, -0.18, 0.06) * (1.0 - _ramp(ny, 0.30, 0.44))
    weight = weight - (_BLEMISH_ZONE_CHEEK - _BLEMISH_ZONE_FOREHEAD) * forehead

    # 鼻梁—人中中轴带
    nose = (1.0 - _ramp(np.abs(nx), 0.10, 0.28)) \
        * _ramp(ny, 0.16, 0.32) * (1.0 - _ramp(ny, 0.72, 0.94))
    weight = weight - (_BLEMISH_ZONE_CHEEK - _BLEMISH_ZONE_NOSE) * nose

    # 下颌/下巴阴影带
    jaw = _ramp(ny, 0.80, 1.02)
    weight = weight - (_BLEMISH_ZONE_CHEEK - _BLEMISH_ZONE_JAW) * jaw

    return np.clip(weight, 0.15, 1.0).astype(np.float32)


def _structure_guard(bgr: np.ndarray, geo: _FaceGeometry) -> np.ndarray:
    """
    结构保护图 (0~1)：图像梯度越强（鼻翼、法令纹、下颌线、眉弓）权重越低.

    祛瑕疵作用在中频层，若不加约束会把「本应存在的明暗结构」一起压掉，
    表现为脸变平、法令纹消失。这里用平滑后的梯度幅值反向门控，
    只让平坦皮肤区域吃满祛瑕疵强度.
    """
    import cv2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    mag = _fast_blur(mag, _scale(geo.iod, 0.03, 2))
    return (1.0 - 0.80 * _ramp(mag, 8.0, 30.0)).astype(np.float32)


# ---- ② 的第二层子蒙版：关键点锚定的立体结构保护区 ----
# _structure_guard 只看梯度，保护得住鼻翼/下颌线的强边，却保护不了梯度并不
# 强、但同样承载立体感的**法令纹沟、泪沟、鼻侧影**。这几处用关键点直接点名保护。
# 索引均取自 MediaPipe 468 点体系。
_NOSE_RIDGE = [168, 6, 197, 195, 5, 4, 1, 2, 98, 327, 129, 358, 49, 279]
_LEFT_LOWER_LID = [33, 7, 163, 144, 145, 153, 154, 155, 133]
_RIGHT_LOWER_LID = [362, 382, 381, 380, 374, 373, 390, 249, 263]
_L_ALAR, _R_ALAR = 98, 327                    # 左右鼻翼
_L_MOUTH_CORNER, _R_MOUTH_CORNER = 61, 291    # 左右嘴角
_JAW_CONTOUR = [234, 93, 132, 58, 172, 136, 150, 149, 176, 148, 152,
                377, 400, 378, 379, 365, 397, 288, 361, 323, 454]
_LANDMARK_PROTECT_STRENGTH = 0.70   # 保护区内祛瑕疵强度最多再降 70%（不是归零）


def _structure_protect_landmarks(bgr: np.ndarray, faces: List[list],
                                 main: _FaceGeometry) -> Optional[np.ndarray]:
    """
    关键点锚定的立体结构保护区 (0~1)，用作 ② 分区祛瑕疵的第二层子蒙版.

    覆盖：鼻梁+鼻侧影（关键点凸包）、法令纹（鼻翼→嘴角连线带）、
    泪沟/卧蚕（下眼睑轮廓下移带）、眉骨阴影、下颌线（脸型轮廓线带）。

    与 `_structure_guard` 相乘后：骨相明暗不会被当成瑕疵抹平，
    但保护区内的临时痘印仍会被淡化（降权 ≠ 完全不动）。

    无关键点（Haar 降级路径）时返回 None，调用方自动退化为只用 `_structure_guard`.
    """
    import cv2

    h, w = bgr.shape[:2]
    iod = float(main.iod)
    m = np.zeros((h, w), dtype=np.uint8)

    for lm in faces:
        # 鼻梁 + 鼻侧影
        nose = _pts(lm, _NOSE_RIDGE, w, h)
        if len(nose) >= 3:
            cv2.fillConvexPoly(m, cv2.convexHull(nose.astype(np.int32)), 255)

        # 法令纹：鼻翼 → 嘴角
        thick = _scale(iod, 0.16, 3)
        for ai, ci in ((_L_ALAR, _L_MOUTH_CORNER), (_R_ALAR, _R_MOUTH_CORNER)):
            if ai < len(lm) and ci < len(lm):
                p0 = (int(lm[ai].x * w), int(lm[ai].y * h))
                p1 = (int(lm[ci].x * w), int(lm[ci].y * h))
                cv2.line(m, p0, p1, 255, thick)

        # 泪沟/卧蚕：下眼睑轮廓整体下移形成一条带
        shift = np.array([0.0, iod * 0.22], dtype=np.float32)
        for idx in (_LEFT_LOWER_LID, _RIGHT_LOWER_LID):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(m, [np.vstack([pts, (pts + shift)[::-1]]).astype(np.int32)], 255)

        # 眉骨阴影
        for idx in (_LEFT_EYEBROW, _RIGHT_EYEBROW):
            pts = _pts(lm, idx, w, h)
            if len(pts) >= 3:
                cv2.fillPoly(m, [pts.astype(np.int32)], 255)

        # 下颌线：脸型下半轮廓的描边带
        jaw = _pts(lm, _JAW_CONTOUR, w, h)
        if len(jaw) >= 3:
            cv2.polylines(m, [jaw.astype(np.int32)], False, 255, _scale(iod, 0.14, 3))

    if int(m.max()) == 0:
        return None

    m = cv2.dilate(m, _ellipse_kernel(_scale(iod, 0.06, 1)))
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, _scale(iod, 0.07, 1)))
    return np.clip(m.astype(np.float32) / 255.0, 0.0, 1.0)


# ============================================================ 分区域肤色修正（⑦）
# 【为什么必须分区域】磨皮压的是「高频斑点」，匀肤压的是「中频色块」，两者都
# 解决不了证件照最常见的脏感来源 —— **低频区域色差**：额头偏黄、两颊偏红、
# 下颌偏暗、脖子与脸不是一个色。这类偏移的尺度比整张脸还大，任何滤波算子都
# 只会把它一起抹平（结果是「脸糊了，色差还在」）。
# 正确做法是「分区域统计 → 把每个区域的低频色偏拉向整脸参考肤色 → 羽化融合」，
# 即 PS 里的「分区域曲线 / 局部色阶」，而不是再来一遍滤镜。
#
# 约束（证件照红线）：
#   * 只允许「同一张脸内更一致」，不允许整体改肤色 —— 参考值取**本图皮肤中位数**，
#     所以整脸偏移为 0 时修正量也为 0；
#   * 每个区域修正量硬夹在 COMPLIANT_MAX_TONE_SHIFT_* 内（L≤6 / a≤4 / b≤4）；
#   * 网格级统计 + 双线性上采样 + 柔化，避免出现「一块一块」的补丁感；
#   * 只作用于皮肤蒙版内，头发/背景/五官不受影响。

