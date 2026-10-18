# 证件照图像质量标准（出图验收清单）

> 本文件是**代码级验收依据**。任何改动图像处理链路的 PR，
> 都必须逐条核对本文档，并在描述中附上对应样本的前后对比。
>
> 代码锚点：
> - 美颜红线 → `backend/app/core/image_engine/beautify.py`
> - 抠图/边缘/光影 → `backend/app/core/image_engine/background.py`
> - 构图红线 → `backend/app/core/image_engine/face_detect.py`
> - 出图质量提示 → `backend/app/core/image_engine/pipeline.py`

---

## 一、美颜：只允许「自然微调」，禁止改变识别特征

原则：**修旧如旧，保留特征**。证件照美化的唯一目的是「去临时瑕疵、匀肤色」，
绝不能改变人脸识别特征。

### 1.1 绝对禁止（红线）

| 禁止项 | 说明 |
|--------|------|
| 瘦脸 / 大眼 / 改眼距 | 任何改变五官几何关系的操作 |
| 改鼻翼大小 / 改下颌角 | 任何改变骨骼轮廓的操作 |
| 过度磨皮致毛孔、痣、疤痕消失 | 关键识别特征必须可辨 |
| 改变五官立体光影 | 禁止二次元化 / AI 脸化 |
| 涂抹感 | 皮肤必须有原生纹理，不能像硅胶 |

### 1.2 允许的微调（上限由代码强制）

| 项目 | 上限 | 代码常量 |
|------|------|----------|
| 磨皮力度 | **≤ 20%** | `COMPLIANT_MAX_SMOOTH = 0.20` |
| 原生纹理保留 | **≥ 85%** | `COMPLIANT_MIN_FINE_KEEP = 0.85` |
| 去瑕疵（仅临时痘印/红肿） | ≤ 20% | `COMPLIANT_MAX_BLEMISH = 0.20` |
| 肤色均匀化（色度） | ≤ 35% | `COMPLIANT_MAX_CHROMA = 0.35`（蒙版加权平滑，防发际线渗色） |
| 提亮 | ≤ 15% | `COMPLIANT_MAX_BRIGHTEN = 0.15` |
| 去油光 | ≤ 12% | `COMPLIANT_MAX_SHINE = 0.12` |
| 通透感回填 | ≤ 10% | `COMPLIANT_MAX_CLARITY = 0.10`（原 6% 被量化噪声淹没，观感等于没做） |
| 眼部微锐化 | ≤ 8% | `COMPLIANT_MAX_SHARPEN = 0.08` |
| 眼神光 | 可轻微提亮眼白/瞳孔 | 不得改瞳孔颜色、不得放大 |
| 发型修正 | 仅修补边缘碎发 | 不得改变发型轮廓 |

> **两条磨皮红线互相约束**：20% 的压制只剩 80% 纹理，已经越过「保留 ≥85%」。
> 所以**实际生效上限是 15%**（`COMPLIANT_MAX_FINE_REMOVE = 1 - COMPLIANT_MIN_FINE_KEEP`），
> `COMPLIANT_MAX_SMOOTH = 0.20` 只是不可越过的硬顶；出图档 `smooth` 取 0.15，
> 自适应强度再在 9%~15% 之间浮动。

## 1.2.1 局部美化的「单位口径」（必须按通道单位给预算）

LAB 的 L/a/b 都是 **0~255 尺度**（1 单位 ≈ 1 个 CIELAB 单位）。用上表那种
「比例」常量去乘通道值，只会得到 0.1 级的改动——**被 8bit 往返量化噪声淹没，
观感上等于没做**（实测整脸平均改动 0.2~0.3 级）。因此唇色/牙齿/眼神光
单独用通道单位给预算：

| 项目 | 上限 | 代码常量 |
|------|------|----------|
| 唇色红润度 | +6 a 单位 | `COMPLIANT_MAX_LIP_A = 6.0` |
| 唇色减黄 | −3 b 单位 | `COMPLIANT_MAX_LIP_B = 3.0` |
| 牙齿去饱和（去黄） | ≤ 45%（相对值） | `COMPLIANT_MAX_TEETH_DESAT = 0.45` |
| 牙齿提亮 | ≤ 8 级 L | `COMPLIANT_MAX_TEETH_L = 8.0` |
| 角膜反光提亮 | ≤ 4 级 L（仅眼区最亮 3%） | `EYE_CATCHLIGHT_LIFT = 4.0` |

唇色增益以**本图皮肤自身的红度中位数**为基准：比皮肤红 8 级以内的唇色吃满
预算，红过 26 级的深唇色不再加红（不改唇形、不改唇面积）。

### 1.3 工程保险

`_clamp_compliant()` 是**最后一道防线**：即使上游误传 `MEDIUM`/`HIGH`，
或 `_PARAMS` 表被改坏，出图参数仍必然落在上表红线内。
`apply_beautify(..., compliant=True)` 为**默认行为**，仅美学回归测试可传 `False`。

### 1.4 增强算子（既要效果，也要不出红线）

| 项 | 实现 | 约束 |
|----|------|------|
| ① 磨皮基底 | 细频层 `_surface_blur()` 表面模糊（阈值双边：不做局部线性拟合，保边且保体积感）；中频基底一律 `_guided_1()` **全分辨率**引导滤波 —— 降采样往返会把低频底座压软，是整图发糊的来源之一 | `SMOOTH_OPERATOR="surface"`，可切回 `"guided"` 做 A/B 与回滚；8bit + 降采样加速，工作半径 ≤12、降采样 ≤1/4，防止保边性被抹平 |
| ② 定向祛瑕疵 | 三层子蒙版相乘：`_blemish_zone_weight()`（额头/鼻唇/下颌降权）× `_structure_guard()`（强梯度保护）× `_structure_protect_landmarks()`（关键点锚定的鼻侧影/法令纹/泪沟/眉骨/下颌线） | 只降不升；两颊满权重，骨相区最多降 70% |
| ③ 唇色/牙齿 | LAB 的 a/b/L 微调；唇蒙版 = 外唇 − 内唇（扣除牙齿） | 预算见 1.2.1；唇色以本图皮肤红度为基准，红唇不再加红；不改唇形/面积 |
| ④ 眼神光 | 眼/眉区对比度加权锐化（boost 归一 ≤1）+ 角膜反光 ≤4 级提亮 | 锐化半径固定 0.8~1.8px（不随人脸尺寸放大）；有效强度峰值 = `COMPLIANT_MAX_SHARPEN`（≤8%） |
| ⑤ 自适应强度 | 按纹理/清晰度/红斑量/曝光/人脸占比**分轴**缩放参数 | 各轴单独夹下限（smooth ≥0.80、blemish ≥0.80，避免暗图/糊图/小脸连乘后磨皮只剩 9%）；缩放后**再过一次** `_clamp_compliant()` |
| 色度匀肤 | LAB a/b **蒙版加权**平滑（加权均值 → 平滑 → 权重归一），发际线/下颌缘过渡带只参考皮肤像素 | 裸 blur 会把头发/背景色度混进皮肤（边缘色晕、显脏）；强度 ≤ `COMPLIANT_MAX_CHROMA` |
| ⑥ 输出锐化 | `pipeline._output_sharpen()`：缩小/换底之后只锐 LAB 亮度通道（radius 0.7~1.0px、60%、threshold=0） | 仅 zoom<0.98 执行，与放大补偿锐化互斥；属输出端补偿，不改变识别特征 |
| 合成 | **分路贴蒙版**：磨皮/匀肤/提亮贴皮肤蒙版；眼神光贴眼/眉；唇色、牙齿各自贴自己的蒙版（`_local_lab_edit` 在包围盒内做 LAB 往返） | 皮肤蒙版天然抠掉五官，三类局部改动**不得**再走皮肤合成，否则会被乘成 0 |

> 复核口径：① 用 `tex_retain`（高频纹理保留率）；② 用分区权重/骨相保护图与两颊的对比；
> ③④ 用唇区 `|Δa|`、牙齿 `ΔL/Δb`、眼区拉普拉斯能量；⑤ 用暗图/亮图的提亮增益比。
> 离线断言在 `backend/tests/test_beautify_p1.py`；真机（mediapipe 关键点）指标
> 由 `dsh_verify_beautify.py` 输出到 `dsh_ab_out/`，可直接目视复核。

### 1.5 非证件照的艺术档（ARTISTIC = 4）

证件照流程**不得**使用本档。写真/社交图等非证件场景可显式选择
`BeautyLevel.ARTISTIC`（数值 4），由 `pipeline` 以 `compliant=False` 调用，
**不经** `_clamp_compliant()` 钳制：

| 项 | 值 | 说明 |
|----|----|------|
| smooth / fine_keep | 0.25 / 0.80 | 磨皮可见，纹理保留仍 ≥80% |
| blemish / chroma | 0.30 / 0.40 | 更强的祛瑕疵与匀肤（各轴严格强于合规档） |
| brighten / gamma | 0.20 / 0.94 | 更亮的暗部 + 轻 S 曲线 |
| clarity / sharpen | 0.14 / 0.12 | 通透感与眼神光加强（强于合规档 0.10 / 0.08） |

约束：不改骨相、不改五官几何；前端仅在非证件照对齐流程暴露（证件照对齐开启时禁用）；
后端 `beautify_level` 校验放宽为 0-4。误传 `compliant=True` 时会被红线压回自然微调。

---

## 二、抠图与边缘融合（消除「剪纸感」）

| 要求 | 实现 | 验收方法 |
|------|------|----------|
| 发丝级抠图 | BiRefNet + 引导滤波 | 发丝边缘必须是渐变 Alpha，非二值切边 |
| 无锯齿 | `_refine_alpha` 高斯羽化 1~3px | 放大 400% 边缘无阶梯状像素 |
| 无白边 / 黑边 | `_refine_alpha` 形态学腐蚀 1~4px 割污染带 | 边缘 3px 带内无色偏尖峰 |
| 颈部肩部过渡 | 环境光融合 + 羽化 | 颈/肩与背景交界无生硬切断 |
| 衣物轮廓保留 | Alpha 保留衣物 | 白底必须保留衣物轮廓 |
| 抑制溢色（色晕） | `_blend_ambient` | 蓝底不发白、红底不发暗 |

---

## 三、背景色彩与光影（消除「漂浮感」）

### 3.1 标准色值

| 背景 | 色值 | 代码键 |
|------|------|--------|
| 白底 | `#FFFFFF` | `white` |
| 标准红 | `#D52B1E` | `red` |
| 标准蓝 | `#438EDB` | `blue` |
| 深蓝（护照蓝） | `#193780` | `dark_blue` |

> 若具体证件要求 `#FF0000` 纯红或 `#2D64B3` 深蓝，在
> `BG_COLOR_MAP` 中新增键值，不要改动既有键（会破坏历史订单复现）。

### 3.2 投影与层次

- 人物背后必须有**非常轻微且自然**的软投影：`_drop_shadow()`，
  复用人物 Alpha 轮廓 → 高斯模糊 → 下移，非硬椭圆。
- 默认不透明度 16%（`_SHADOW_OPACITY = 0.16`），过重会显脏。

### 3.3 环境光统一

- 人物面部色温必须与背景一致。
- 实现：`_blend_ambient()` 在 Alpha 过渡带混入目标背景色（强度 0.28），
  模拟环境光在颈/肩/发丝边缘的微弱反射。
- 蓝底：面部侧面应有**极微弱冷色反光**，而非完全偏黄的独立光。

---

## 四、尺寸与排版输出

### 4.1 分辨率

- 输出 DPI **不得低于 300**：`MIN_OUTPUT_DPI = 300`
- 低于 300 时 `_quality_warnings()` 会提示「可能影响打印效果」

### 4.2 标准像素

| 规格 | 像素 | 物理尺寸 | 代码键 |
|------|------|----------|--------|
| 一寸 | 295 × 413 | 25mm × 35mm | `一寸` |
| 二寸 | 413 × 579 | 35mm × 49mm | `二寸` |
| 小一寸 | 260 × 378 | 22mm × 32mm | `小一寸` |
| 大一寸 | 390 × 567 | 33mm × 48mm | `大一寸` |
| 小二寸 | 413 × 531 | 35mm × 45mm | `小二寸` |

### 4.3 构图红线（人脸识别算法友好）

| 指标 | 要求 | 代码常量 |
|------|------|----------|
| 头顶留白 | 总高度 **5% ~ 10%** | `COMPOSE_TOP_MARGIN = 0.08` |
| 眼睛位置 | 总高度 **50% ~ 60%** | `COMPOSE_EYE_LINE = 0.56` |
| 脸部宽度 | 照片宽度 **60% ~ 70%** | `COMPOSE_FACE_WIDTH = 0.65` |
| 双肩 | 水平且对称 | 由对齐缩放保证 |
| 居中 | 人物水平居中 | `align_face` 以脸中心为锚 |

`align_face()` 每次调用都会把**实际达成的三个比例写进日志**，便于抽检：

```
Face aligned: ... | 头顶留白=8.1%(需5-10%) 眼位=55.8%(需50-60%) 脸宽占比=64.3%(需60-70%)
```

---

## 五、系统交互语言

| 要求 | 实现位置 |
|------|----------|
| 不叫「美颜」，改叫「**自然微调**」 | `Editor.vue` / `Home.vue` / `AppFooter.vue` / `Profile.vue` |
| 提供「原图 / 效果图」对比滑块 | `Editor.vue` → `.compare-slider-wrap` |
| 明确列出「允许的微调项」与「已锁定的禁止项」 | `Editor.vue` → `.compliance-alert` / `.compliance-note` |
| 低分辨率/非标准尺寸醒目提示 | `pipeline._quality_warnings()`，随结果 `warnings` 透传 |

---

## 六、出图质量提示文案（透传到下载页）

由 `pipeline._quality_warnings()` 生成，随 `ProcessResult.warnings` 返回：

1. `当前输出 DPI={n}，低于印刷标准 300dpi，可能影响打印效果`
2. `未指定证件照标准尺寸，已按原图尺寸输出；如需打印请选择「一寸 295×413」…`
3. `当前尺寸 {w}×{h}px 非标准证件照像素，标准值：一寸 295×413 / 二寸 413×579 …`
4. `当前生成分辨率较低（{w}×{h}px），建议使用高清原图以保证打印质量`

---

## 七、回归测试建议（新增增强算子已自动化，黄金样本集待补）

黄金样本集：20 张（不同肤色 / 发型 / 角度 / 原图分辨率）。

已落地的离线回归（不需要 mediapipe / ONNX，可直接跑）：

```bash
python backend/tests/test_edge_quality.py     # 羽化 / 环境光 / 投影 / 红线
python backend/tests/test_beautify_p1.py      # ①~⑤ 增强算子、分路合成、红线
```

真机（关键点链路）验证需要一个带 mediapipe 的环境。本机没有全局 Python
依赖，按 Dockerfile 同版本（3.10）建隔离 venv：

```powershell
py -3.10 -m venv ..\.venv-beautify
..\.venv-beautify\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple `
    numpy "opencv-python-headless==4.10.0.84" "Pillow==10.4.0" loguru onnxruntime pydantic-settings
..\.venv-beautify\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --no-deps mediapipe
..\.venv-beautify\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple `
    "absl-py~=2.3" "flatbuffers~=25.9" sounddevice certifi matplotlib
```

> `--no-deps` 是刻意的：mediapipe 会拉 `opencv-contrib-python`，与项目钉死的
> `opencv-python-headless==4.10.0.84` 争同一个 `cv2` 模块（5.x 起不再分发
> haarcascades，会让人脸检测全线失效）。

```powershell
$env:PYTHONIOENCODING="utf-8"
..\.venv-beautify\Scripts\python.exe dsh_verify_beautify.py   # 逐区域带符号指标 + 蒙版图
..\.venv-beautify\Scripts\python.exe dsh_mp_bench.py          # 关键点/耗时
..\.venv-beautify\Scripts\python.exe dsh_p1_ab.py             # guided/surface × adaptive A/B
..\.venv-beautify\Scripts\python.exe dsh_retouch_bench.py     # 1200x1600 下的 _retouch 耗时
```

**判读口径（重要）**：真实小样张（<400px）上，整脸平均改动会落在 8bit
往返量化噪声地板（±0.5 级）附近，**不要**用它判断磨皮强度；磨皮幅度要在
`test_beautify_p1.py::test_skin_smoothing_reaches_redline` 的合成样张上量，
或者看 1200px 以上的真实样张。

| 指标 | 阈值 |
|------|------|
| 边缘锐度（拉普拉斯方差） | > 1200 |
| 边缘 3px 带色偏 | < 15 |
| 皮肤高频纹理保留率 | 0.85 ~ 0.93（红线 ≥0.85） |
| 唇色 `|Δa|`（真机） | ≥ 1.0，且符号为正 |
| 牙齿 `ΔL` / `Δb`（真机） | ≥ +2.0 / ≤ −2.0 |
| 眼区高频能量比（真机） | ≥ 1.02 |
| 骨相保护区 vs 两颊的高频保留差 | 保护区内更低 |
| 背景色 ΔE | < 2.0 |
| 输出 DPI | ≥ 300 |
| 构图三比例 | 全部落红线内 |

---

## 八、研发方向（正确的处理顺序）

```
高清人脸检测
  → 轻度瑕疵修复（不改变轮廓）
  → 高精度发丝抠图
  → 专业影棚级打光合成（环境光 + 阴影）
  → 标准化裁剪排版
```

**不要**再依赖简单的「美颜滤镜 SDK」直接套用在证件照上。
