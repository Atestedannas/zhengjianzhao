<template>
  <div class="compare-view">
    <!-- 对比方式切换：默认「左右对照」，左原图 / 右效果图，一眼可比 -->
    <div class="compare-toolbar">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="side">左右对照</el-radio-button>
        <el-radio-button value="hold">按住看原图</el-radio-button>
        <el-radio-button value="slider">滑动对比</el-radio-button>
      </el-radio-group>
      <span class="align-badge" :class="{ warn: !aligned }">
        <el-icon v-if="aligned"><CircleCheck /></el-icon>
        <el-icon v-else><Warning /></el-icon>
        {{ aligned ? '两图已严格对齐（同裁剪·同缩放·同像素）' : '基线缺失，几何可能不一致' }}
      </span>
    </div>

    <!-- ① 左右对照（默认）：左 = 原图，右 = 效果图.
         两个格子宽度完全相等 + 图片统一 object-fit: contain，
         所以同一张脸在两侧的缩放比例、中心点必然一致。 -->
    <div v-if="mode === 'side'" class="side-stage">
      <div class="side-cell">
        <div class="side-label">原图</div>
        <div class="side-frame"><AuthImage :src="baseSrc" alt="原图" class="stage-img" /></div>
      </div>
      <div class="side-cell">
        <div class="side-label">效果图</div>
        <div class="side-frame"><AuthImage :src="resultSrc" alt="效果图" class="stage-img" /></div>
      </div>
    </div>

    <!-- ② 按住看原图：两张图完全叠放，按下切原图、松开切效果图 -->
    <div
      v-else-if="mode === 'hold'"
      class="hold-stage"
      @pointerdown="startHold"
      @pointerup="endHold"
      @pointerleave="endHold"
      @pointercancel="endHold"
      @contextmenu.prevent
    >
      <AuthImage :src="resultSrc" alt="效果图" class="stage-img" :class="{ hidden: holding }" />
      <AuthImage :src="baseSrc" alt="原图" class="stage-img" :class="{ hidden: !holding }" />
      <div class="stage-tag">{{ holding ? '原图' : '效果图' }}</div>
      <div class="hold-hint" v-if="!holding">
        <el-icon><Pointer /></el-icon>
        按住图片查看原图
      </div>
    </div>

    <!-- ③ 滑动对比：用 clip-path 裁切，元素本身始终占满容器 -->
    <div v-else class="slider-stage">
      <AuthImage :src="baseSrc" alt="原图" class="stage-img" />
      <div class="slider-clip" :style="{ clipPath: `inset(0 ${100 - sliderRatio}% 0 0)` }">
        <AuthImage :src="resultSrc" alt="效果图" class="stage-img" />
      </div>
      <div class="slider-divider" :style="{ left: `${sliderRatio}%` }"></div>
      <input
        v-model.number="sliderRatio"
        class="slider-range"
        type="range"
        min="0"
        max="100"
        aria-label="原图与效果图对比"
      />
      <span class="stage-tag tag-left">原图</span>
      <span class="stage-tag tag-right">效果图</span>
    </div>

    <p class="compare-note">{{ note }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 原图 / 效果图对比组件.
 *
 * 【为什么必须是这两张图】左边放「用户上传的原片」、右边放「裁剪+对齐+缩放后的
 * 结果」时，两张图比例、中心点、缩放全都不一样 —— 用户看到的第一眼永远是
 * 「右边脸怎么这么大」，根本没法判断美颜效果。所以两张图都来自后端：
 * 结果图 + **同几何基线图**（baseline_url，同裁剪/同对齐/同像素），逐像素对齐。
 *
 * 【三种形态】
 *   side   左右对照（默认）：左原图 / 右效果图，最直观
 *   hold   按住看原图：两张图叠放，按下切原图，松开切效果图
 *   slider 滑动对比：用 clip-path 裁切，元素尺寸不变（旧实现用 width% + overflow
 *          会把子图压扁，产生撕裂感）
 * 基线缺失时（老记录 / 未开美颜）回退到上传原片，并明确提示几何可能不一致。
 */
import { computed, ref } from 'vue'
import { Pointer, CircleCheck, Warning } from '@element-plus/icons-vue'
import AuthImage from '@/components/AuthImage.vue'

const props = defineProps<{
  /** 效果图地址（受保护路径） */
  resultSrc: string
  /** 同几何基线图地址；为空时回退到 fallbackSrc */
  baselineSrc?: string | null
  /** 兜底原图（上传的 dataURL），仅在基线缺失时使用 */
  fallbackSrc?: string | null
}>()

type CompareMode = 'side' | 'hold' | 'slider'

/** 默认左右对照：左原图、右效果图，用户第一眼就能比 */
const mode = ref<CompareMode>('side')
const holding = ref(false)
const sliderRatio = ref(50)

/** 基线是否可用：可用时两张图严格对齐 */
const aligned = computed(() => !!props.baselineSrc)

/** 原图那一侧实际使用的图 */
const baseSrc = computed(() => props.baselineSrc || props.fallbackSrc || '')

const note = computed(() => {
  if (!aligned.value) {
    return '当前记录没有生成对比基线（老记录或未开启美颜），左图为上传原片，'
      + '缩放与裁切可能与效果图不同，请谨慎判断差异。'
  }
  const common =
    '两张图使用完全相同的裁剪窗口、缩放比例与输出像素，位置逐像素对齐；'
    + '原图侧保留原始背景（未换底），对比时请以肤色、皮肤纹理、五官清晰度为准。'
  if (mode.value === 'side') {
    return `左侧原图、右侧效果图，等宽同比例并排。${common}`
  }
  if (mode.value === 'hold') {
    return `按住图片显示原图，松开显示效果图。${common}`
  }
  return `拖动滑块左右对照，任何位置都不会出现缩放跳变。${common}`
})

function startHold() {
  holding.value = true
}

function endHold() {
  holding.value = false
}
</script>

<style scoped>
.compare-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.compare-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.align-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #67c23a;
}

.align-badge.warn {
  color: #e6a23c;
}

/* ===== ① 左右对照 ===== */
.side-stage {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  /* 用 max-width 约束整组宽度（而不是给单个 frame 加 max-height），
     这样两个格子的宽高比始终是 3:4 —— 一旦用 max-height 压高度，
     aspect-ratio 会被打破、图片多出一圈留白，虽然两侧仍对齐但观感变差 */
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
}

.side-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.side-label {
  font-size: 13px;
  font-weight: 500;
  color: #606266;
  text-align: center;
}

/* 两个格子等宽 + 同一个宽高比 → 两张图的缩放比例与中心点必然一致 */
.side-frame {
  position: relative;
  width: 100%;
  aspect-ratio: 3 / 4;
  border-radius: 10px;
  overflow: hidden;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
}

/* ===== ② 按住看原图 / ③ 滑动对比 共用盒子 ===== */
.hold-stage,
.slider-stage {
  position: relative;
  width: 100%;
  aspect-ratio: 3 / 4;
  max-height: 62vh;
  border-radius: 10px;
  overflow: hidden;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  touch-action: none;
  user-select: none;
  cursor: pointer;
}

.stage-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  transition: opacity 0.08s linear;
}

.stage-img.hidden {
  opacity: 0;
}

.stage-tag {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 2px 10px;
  font-size: 12px;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 10px;
  pointer-events: none;
}

.tag-right {
  left: auto;
  right: 8px;
}

.hold-hint {
  position: absolute;
  left: 50%;
  bottom: 10px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  font-size: 12px;
  color: #fff;
  background: rgba(64, 158, 255, 0.85);
  border-radius: 12px;
  pointer-events: none;
}

.slider-clip {
  position: absolute;
  inset: 0;
  /* clip-path 只裁可见区域，元素尺寸不变 → 不会像旧实现那样把图压扁 */
}

.slider-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #fff;
  box-shadow: 0 0 6px rgba(0, 0, 0, 0.35);
  transform: translateX(-1px);
  pointer-events: none;
}

.slider-range {
  position: absolute;
  left: 0;
  bottom: 8px;
  width: 100%;
  margin: 0;
  z-index: 5;
  cursor: ew-resize;
}

.compare-note {
  margin: 0;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

@media (max-width: 900px) {
  .side-stage {
    grid-template-columns: 1fr;
  }
}
</style>
