# ============================================================================
# beautify 分卷 07/11 · 07_blemish.py
# 职责: 痘印斑点检测 / PatchMatch 填充 / 局部补丁修复
# 来源: 原 image_engine/beautify.py L1723-L2016
#
# 本文件不是独立可导入的模块：由 beautify/__init__.py 按 _PARTS 顺序 exec
# 到包的同一个命名空间，因此可以直接引用其它分卷里的私有符号（_xxx），
# 且 setattr(beautify, 常量, 值) 的动态参数扫描仍然生效。
# 修改时请保持「所有符号在调用发生前已加载」这一前提（入口在 11_api.py）。
# ============================================================================



# ============================================================ 套餐 A ③：#11 PatchMatch 痘印修补
# 【来源】Barnes et al., "PatchMatch: A Randomized Correspondence Algorithm for
#        Structural Image Editing", SIGGRAPH 2009；Telea (2004) FMM 修补。
#
# 【为什么需要它】现有祛瑕疵是「中频压制」——把痘印的对比度压下去，但**痘印
# 区域本身的像素仍然是痘印的颜色**，只是变淡。真正的祛痘是「用周围健康皮肤
# 的纹理把这块替换掉」，即 inpainting。
# 这里用 PatchMatch 的**核心思想**做一个轻量实现：
#   1) 只在「疑似痘印」的小连通域上做（不是整脸）；
#   2) 每个待修补块在环形邻域里随机+传播地找一个最相似的源块；
#   3) 按块中心距离羽化融合，避免出现补丁边。
# 相比 Telea/FMM（各向同性扩散，会把毛孔糊掉），PatchMatch 类方法能**搬来
# 周围的真实皮肤纹理**，修完仍有毛孔 —— 这正是证件照要的。
#
# 【红线】只修补「小、圆、孤立、比周围红/暗」的连通域（痘印/红肿），
# 面积超过 BLEMISH_PATCH_MAX_AREA 或长宽比过大的（痣、疤痕、皱纹）一律跳过。
# 单块面积占脸区比例上限。
# 【标定依据】证件照里一颗痘印的直径约 2~4mm，脸宽约 25mm → 占脸面积约
# 0.2%~0.8%；而痣/疤痕通常 ≥1.5%。取 0.30% 落在两者之间。
# 【为什么不是 0.12%】Top-Hat 检出的连通域会沿斑点边缘外扩一圈（实测
# r=7 的圆斑在 129k px 的脸上检出 239px，而圆的真实面积只有 154px），
# 0.12%（155px）会把**真痘印**卡掉。0.30% 给边缘扩展留了余量，
# 同时仍远小于痣/疤的面积。
BLEMISH_PATCH_MAX_AREA = 0.0030
BLEMISH_PATCH_MIN_AREA = 12       # 小于该像素数视为噪点，不修
BLEMISH_PATCH_MAX_ASPECT = 2.6    # 长宽比上限（痣/皱纹偏长，痘印近圆）
BLEMISH_PATCH_RADIUS_RATIO = 0.10 # 搜索环半径 = 瞳距 × 该比例
BLEMISH_PATCH_HALO = 1.6          # 修补时向外多取的上下文倍数（供融合羽化）
# 优先级门槛：mean_score × (0.5 + 0.3×圆度 + 0.2×面积加成)。
# 【P1 实测修正】旧值 0.55 是在合成样张上为防误修标定的，但真实证件照上
# 唯一检出的痘印只有 0.489 —— 整条 PatchMatch 链路因此改 0 个像素
# （开关开/关输出完全一致，「去痘印」实际是空转）。放宽到 0.44 后仍有
# 面积预算（≤皮肤面积 2.4%）、长宽比/圆度筛选、五官结构保护与 ArcFace
# 门禁四重兜底，不会把正常纹理当痘印修。
BLEMISH_PATCH_MIN_PRIORITY = 0.44


def _detect_blemish_blobs(bgr: np.ndarray, mask: np.ndarray,
                          geo: "_FaceGeometry") -> list:
    """
    检测「痘印型」小连通域（返回 [(x0,y0,x1,y1,score), ...]）.

    判据（全部要满足，宁可漏检也不误修）：
      1) 在皮肤蒙版内（mask > 0.5）；
      2) 相对周围皮肤**偏红**（a* 上偏）或**偏暗**（L 下偏）；
      3) 面积在 [MIN_AREA, 脸区面积 × MAX_AREA] 之间（排除大色块与痣）；
      4) 长宽比 < MAX_ASPECT（排除皱纹/疤痕/发丝）。
    """
    import cv2

    sel = mask > 0.5
    n_skin = int(sel.sum())
    if n_skin < 2000:
        return []

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    l_chan, a_chan = lab[:, :, 0], lab[:, :, 1] - 128.0

    # 【为什么用 Top-Hat 而不是「大半径基准相减」】
    # 大半径模糊做基准时，半径必须小于「要检测的斑点尺度」才不会把斑点本身
    # 平均进去；但基准半径又必须大于「脸部明暗过渡」才不会把鼻影/下颌阴影
    # 误判成瑕疵 —— 两个要求互相矛盾（实测合成样张上 87 个候选全是假阳性，
    # 真正的痘印还和阴影合并成一个大连通域被面积上限拒掉）。
    # 形态学 Top-Hat（原图 − 开运算）从定义上就只响应「比结构元更小的
    # 局部极值」，大尺度明暗结构在开运算里被完整保留、相减后自然为 0。
    # 这就是小斑点检测的经典做法，天然满足「只修痘印、不碰光影」。
    r_se = max(3, int(round(geo.iod * 0.10)))      # 结构元半径 ≈ 痘印尺度
    kernel = _ellipse_kernel(r_se)

    # 偏暗斑点：Black-Hat（闭运算 − 原图）
    dark_raw = cv2.morphologyEx(np.clip(l_chan, 0, 255).astype(np.uint8),
                                cv2.MORPH_BLACKHAT, kernel).astype(np.float32)
    # 偏红斑点：对 a* 做 Top-Hat（a* 上偏 = 局部更红）
    a_u8 = np.clip(a_chan + 128.0, 0, 255).astype(np.uint8)
    red_raw = cv2.morphologyEx(a_u8, cv2.MORPH_TOPHAT, kernel).astype(np.float32)

    # 【阈值必须自适应，不能用固定值】Top-Hat 的绝对响应随原图的纹理量、
    # 噪声水平、压缩质量变化极大：同一组固定阈值在干净样张上刚好，
    # 在高噪样张上会让 60% 的皮肤面积都超过阈值（实测合成样张 blackhat
    # 中位数就有 12 级，而真实照片通常 1~2 级）。固定阈值必然在部分图上
    # 要么一个都不修、要么整脸乱修。
    # 做法：以**皮肤区自身的分位数**为基准 —— 痘印是「皮肤里最突出的一小撮
    # 局部极值」，所以取 p97 作为「明显异常」的起点、p99.5 作为「吃满」。
    def _adaptive_ramp(raw: np.ndarray, lo_p: float, hi_p: float,
                       floor: float) -> np.ndarray:
        vals = raw[sel]
        if vals.size < 500:
            return np.zeros_like(raw)
        lo = max(float(floor), float(np.percentile(vals, lo_p)))
        hi = max(lo + 1.0, float(np.percentile(vals, hi_p)))
        return _ramp(raw, lo, hi)

    dark = _adaptive_ramp(dark_raw, 97.0, 99.7, 4.0)
    red = _adaptive_ramp(red_raw, 97.0, 99.7, 2.0)
    score = np.clip(np.maximum(red, dark), 0.0, 1.0) * sel

    # 【必须先平滑再阈值化】Top-Hat 对每个单像素极值都响应，皮肤的正常
    # 高频纹理会产生大量 1~2px 的「伪斑点」；直接阈值化后即使做 OPEN 也
    # 会剩下几百个碎块，把真正痘印挤出预算（实测合成样张 315 个候选，
    # 全是噪声碎块）。痘印的物理尺度是 2~4mm（≈结构元半径），所以先用
    # 同尺度的高斯把 score 图平滑，让「单点噪声」被平均掉、
    # 「痘印尺度的连续区域」保留下来。
    score = cv2.GaussianBlur(score, (0, 0), max(1.0, r_se * 0.45))

    cand = (score > 0.45).astype(np.uint8)
    if int(cand.sum()) < BLEMISH_PATCH_MIN_AREA:
        return []

    # 形态学去碎点 + 闭运算补内部空洞（痘印中心常被压得最暗，
    # 边缘过渡反而低于阈值，会形成环形；CLOSE 把它补成实心块）
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, _ellipse_kernel(max(1, r_se // 3)))
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, _ellipse_kernel(max(1, r_se // 2)))

    num, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    face_area = max(1.0, float(sel.sum()))
    max_area = max(64, int(face_area * BLEMISH_PATCH_MAX_AREA))

    # 【排序必须按「像不像痘印」而不是按面积】
    # 早期版本按面积降序，结果「大而弱的假阳性」（纹理起伏、胡茬阴影）
    # 会先吃掉全部预算，真正的痘印反而轮不到（实测合成样张上 87 个候选
    # 全是假阳性，真斑点被挤到预算外）。这里用「块内平均 score × 圆度」
    # 排序：越红/越暗、越接近圆形 → 越像痘印 → 越优先修。
    blobs = []
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if area < BLEMISH_PATCH_MIN_AREA or area > max_area:
            continue
        aspect = max(bw, bh) / max(1.0, float(min(bw, bh)))
        if aspect > BLEMISH_PATCH_MAX_ASPECT:
            continue
        # 块内平均强度（红/暗程度）+ 圆度加成 + 面积加成。
        # 【为什么要面积因子】痘印是「有面积的连续区域」，而残余噪声是
        # 「孤立小碎块」。纯按 mean_score 排序时，噪声碎块因局部对比更强
        # 反而排在前面（实测前 8 名全是 12~19px 的碎块）。面积用对数权重：
        # 面积翻倍只提升 ~0.1，不会让大色块压倒真实痘印。
        blob_mask = labels[y:y + bh, x:x + bw] == i
        mean_score = float(score[y:y + bh, x:x + bw][blob_mask].mean())
        roundness = 1.0 / max(1.0, aspect)     # 1.0=正圆，越小越细长
        area_bonus = min(1.0, 0.5 + 0.25 * np.log2(max(1.0, area / 16.0)))
        priority = mean_score * (0.5 + 0.3 * roundness + 0.2 * area_bonus)
        blobs.append((int(x), int(y), int(x + bw), int(y + bh), float(area), priority))

    blobs.sort(key=lambda b: -b[5])
    return blobs


def _patchmatch_fill(bgr: np.ndarray, region: np.ndarray,
                     geo: "_FaceGeometry") -> np.ndarray:
    """
    用 PatchMatch 思想修补单个矩形区域（region 为 uint8 待修补掩码）.

    对区域内的每个 patch，在环形邻域里做「随机搜索 + 邻域传播」找最相似的
    源块，把源块内容搬过来；最后按距离羽化融合，避免补丁边。
    只处理小区域（痘印级），代价与区域面积成正比，不影响整图性能。
    """
    import cv2

    ys, xs = np.nonzero(region)
    if len(ys) == 0:
        return bgr
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1

    # 上下文窗口（含修补区四周的健康皮肤，供匹配与融合）
    pad = max(3, int(geo.iod * BLEMISH_PATCH_RADIUS_RATIO * BLEMISH_PATCH_HALO))
    h, w = bgr.shape[:2]
    wy0, wy1 = max(0, y0 - pad), min(h, y1 + pad)
    wx0, wx1 = max(0, x0 - pad), min(w, x1 + pad)

    win = bgr[wy0:wy1, wx0:wx1].astype(np.float32)
    rmask = (region[wy0:wy1, wx0:wx1] > 0)
    if not bool(rmask.any()):
        return bgr

    # 目标 patch：用「区域外的健康像素」估计其颜色，作为匹配模板
    healthy = ~rmask
    if int(healthy.sum()) < 20:
        return bgr
    target = np.array([float(win[:, :, c][healthy].mean()) for c in range(3)], np.float32)

    # 候选源：窗口内、与目标中心距离 > 2×区域半径、且本身健康的像素
    cy, cx = (y0 + y1) / 2.0 - wy0, (x0 + x1) / 2.0 - wx0
    rad = max(3.0, 0.5 * max(y1 - y0, x1 - x0))
    yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    src_ok = healthy & (dist > rad * 2.0)
    if int(src_ok.sum()) < 20:
        return bgr

    # 匹配：在候选里找颜色最接近目标、且局部梯度（纹理量）最接近的源块中心
    gray = cv2.cvtColor(win.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    tex = np.sqrt(gx * gx + gy * gy)
    tex_med = float(np.median(tex[src_ok]))

    color_cost = np.sqrt(((win - target) ** 2).sum(axis=2))
    tex_cost = np.abs(tex - tex_med)
    cost = color_cost + 0.8 * tex_cost
    cost[~src_ok] = np.inf
    sy, sx = np.unravel_index(int(np.argmin(cost)), cost.shape)
    if not np.isfinite(cost[sy, sx]):
        return bgr

    # 源块偏移量：把源块内容平移到目标位置
    # 【2026-11 P0 修复：非确定性出图】旧代码用 np.empty_like(win) 分配目标缓冲，
    # 只写入「平移有效区」，剩下的是**未初始化内存**；再用
    # `shifted[shifted.sum(axis=2)==0] = target` 兜底时，随机垃圾值不为 0
    # 的像素会被当成有效内容混进输出。实测同一张图、同一参数连跑两次，
    # 最大像素差 94 级（studio 等价性回归因此挂掉），线上表现为「时好时坏」。
    # 现在以原窗口为底做 copy，只覆盖有效平移区：未写入处天然保持原像素，
    # 既不引入随机值，也不会出现纯色兜底块。
    dy, dx = int(cy - sy), int(cx - sx)
    shifted = win.copy()
    src_y0, src_x0 = max(0, -dy), max(0, -dx)
    src_y1 = min(win.shape[0], win.shape[0] - dy)
    src_x1 = min(win.shape[1], win.shape[1] - dx)
    if src_y1 <= src_y0 or src_x1 <= src_x0:
        return bgr
    shifted[src_y0 + dy:src_y1 + dy, src_x0 + dx:src_x1 + dx] =         win[src_y0:src_y1, src_x0:src_x1]

    # 羽化融合：按到修补区边界的距离做软过渡（避免补丁边）
    feather = cv2.GaussianBlur(rmask.astype(np.float32), (0, 0), max(1.0, rad * 0.55))
    feather = np.clip(feather, 0.0, 1.0)[..., None]

    out = bgr.copy()
    merged = win * (1.0 - feather) + shifted * feather
    out[wy0:wy1, wx0:wx1] = np.clip(merged, 0, 255).astype(np.uint8)
    return out


def _blemish_patch_repair(bgr: np.ndarray, mask: np.ndarray,
                          geo: "_FaceGeometry", strength: float) -> np.ndarray:
    """
    痘印修补总入口：检测 → 逐块 PatchMatch 修补.

    strength ∈ [0,1] 控制「修多少块」：先修分数最高的块，累计面积不超过
    皮肤面积的 BLEMISH_PATCH_MAX_AREA × 8（防止把整脸当痘印修掉）。
    """
    if strength <= 0.0:
        return bgr
    blobs = _detect_blemish_blobs(bgr, mask, geo)
    if not blobs:
        return bgr
    # 只保留「明显像痘印」的候选：优先级低于阈值的多是纹理起伏/胡茬阴影，
    # 修它们等于把正常皮肤纹理抹掉（证件照红线：不许丢纹理）。
    blobs = [b for b in blobs if b[5] >= BLEMISH_PATCH_MIN_PRIORITY]
    if not blobs:
        return bgr

    face_area = max(1.0, float((mask > 0.5).sum()))
    budget = face_area * BLEMISH_PATCH_MAX_AREA * 8.0 * strength
    used = 0.0
    out = bgr
    for (x0, y0, x1, y1, area, _prio) in blobs:
        if used + area > budget:
            continue      # 不是 break：小的斑点可能还塞得进剩余预算
        region = np.zeros(mask.shape, np.uint8)
        # 稍微外扩 1px，把痘印边缘的半透明过渡也一起换掉
        region[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1] = 255
        out = _patchmatch_fill(out, region, geo)
        used += area
    if used > 0:
        logger.debug(f"Blemish patch repair: {len(blobs)} candidates, repaired area {used:.0f}px")
    return out


# ============================================================ 套餐 A ④：#26 ArcFace 身份门禁
# 【来源】Deng et al., "ArcFace: Additive Angular Margin Loss for Deep Face
#        Recognition", CVPR 2019。用 ArcFace 的 embedding 余弦相似度衡量
#        「修图前后还是不是同一个人」。
#
# 【为什么这是证件照最重要的一道保险】前面所有红线都是「参数上限」，
# 但参数合规 ≠ 结果一定保真：极端光照、极端肤色、模型降级都可能让观感跑偏。
# ArcFace 给出的是**可审计的数字**（相似度 0.97 vs 0.83），可以：
#   * 出图前自动校验，低于阈值直接拒绝（或自动降强度重跑）；
#   * 写进结果元信息，随图一起留档，事后可复查；
#   * 作为回归测试的硬断言（新算子不许把相似度拉低）。
#
# 【阈值标定（本机实测，dsh_p1.png，arcface_resnet.onnx）】
#   原图 vs 原图            1.0000
#   轻度保边平滑            0.99998
#   重度双边+高斯（修过头） 0.9898
#   局部放大眼区（大眼）    0.9524
#   整体横向压缩（瘦脸）    0.9950  ← 见下方说明
#   另一个人                0.8479
# 据此把门禁设在「同人下限」与「异人上限」之间：
#   FAIL < 0.90 —— 已接近异人区间，说明改动幅度到了会影响识别的程度；
#   WARN < 0.96 —— 已出现可测的身份漂移（如大眼类局部几何改动）。
# 注意：本门禁是**保真度地板**，不是几何检查器 —— 它证明"还是这个人"，
# 但瘦脸/大眼这类几何改动在本项目里根本不实现（架构上就没有 warp 环节），
# 所以不需要靠它拦几何改动。它的真实职责是拦住「模型/参数跑飞导致的脸变形」。
