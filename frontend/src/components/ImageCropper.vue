<template>
  <view class="cropper-container">
    <view class="cropper-header">
      <text class="cropper-title">拖动/缩放图片以调整裁剪区域</text>
    </view>

    <view class="cropper-canvas-area">
      <!-- movable-area: 底层可拖动图片 -->
      <movable-area class="movable-area" :style="areaStyle">
        <movable-view
          class="movable-view"
          direction="all"
          :x="imgX"
          :y="imgY"
          @change="onImageMove"
          :scale="true"
          :scale-min="1"
          :scale-max="3"
        >
          <image
            class="crop-image"
            :src="src"
            mode="widthFix"
            :style="imgStyle"
          />
        </movable-view>
      </movable-area>

      <!-- 顶层蒙版 + 镂空裁剪框 -->
      <view class="crop-mask">
        <!-- 上蒙版 -->
        <view class="mask-top" :style="maskTopStyle" />
        <!-- 中间行：左蒙版 + 裁剪框 + 右蒙版 -->
        <view class="mask-row" :style="maskRowStyle">
          <view class="mask-left" :style="maskLeftStyle" />
          <view class="crop-box" :style="cropBoxStyle">
            <!-- 四角标记 -->
            <view class="corner tl" />
            <view class="corner tr" />
            <view class="corner bl" />
            <view class="corner br" />
            <!-- 比例提示 -->
            <text class="ratio-tip">{{ ratioLabel }}</text>
          </view>
          <view class="mask-right" :style="maskRightStyle" />
        </view>
        <!-- 下蒙版 -->
        <view class="mask-bottom" :style="maskBottomStyle" />
      </view>
    </view>

    <!-- 底部操作栏 -->
    <view class="cropper-actions">
      <u-button type="info" plain size="small" @click="handleCancel">
        重新选择
      </u-button>
      <u-button type="primary" size="small" @click="confirmCrop">
        确认裁剪
      </u-button>
    </view>
  </view>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const props = defineProps({
  src: { type: String, required: true },
  ratioW: { type: Number, default: 3 },
  ratioH: { type: Number, default: 4 },
})

const emit = defineEmits(['confirm', 'cancel'])

const windowWidth = ref(375)
const windowHeight = ref(667)

// 裁剪框尺寸（基于屏幕宽度和比例计算）
const cropWidth = computed(() => Math.min(windowWidth.value - 60, 300))
const cropHeight = computed(() => Math.round(cropWidth.value * (props.ratioH / props.ratioW)))

// 图片显示尺寸（固定宽度 = 裁剪框宽度 * 2，保证可拖动空间）
const imgWidth = computed(() => cropWidth.value * 2)
const imgHeight = computed(() => Math.round(imgWidth.value * (props.ratioH / props.ratioW)))

// movable 区域 = 裁剪框可见区域
const areaWidth = computed(() => cropWidth.value)
const areaHeight = computed(() => cropHeight.value)

// movable-view 初始位置（居中）
const imgX = ref(0)
const imgY = ref(0)

// 初始化位置
onMounted(() => {
  try {
    const sysInfo = uni.getSystemInfoSync()
    windowWidth.value = sysInfo.windowWidth
    windowHeight.value = sysInfo.windowHeight
  } catch { /* 使用默认值 */ }

  // 图片初始位置让图片居中在裁剪框内
  imgX.value = -(imgWidth.value - areaWidth.value) / 2
  imgY.value = -(imgHeight.value - areaHeight.value) / 2
})

const areaStyle = computed(() => ({
  width: areaWidth.value + 'px',
  height: areaHeight.value + 'px',
}))

const imgStyle = computed(() => ({
  width: imgWidth.value + 'px',
  height: imgHeight.value + 'px',
}))

// 蒙版样式
const maskTopStyle = computed(() => ({
  height: `calc((100% - ${cropHeight.value}px) / 2)`,
}))

const maskBottomStyle = computed(() => ({
  height: `calc((100% - ${cropHeight.value}px) / 2)`,
}))

const maskRowStyle = computed(() => ({
  height: cropHeight.value + 'px',
}))

const maskLeftStyle = computed(() => ({
  width: `calc((100% - ${cropWidth.value}px) / 2)`,
}))

const maskRightStyle = computed(() => ({
  width: `calc((100% - ${cropWidth.value}px) / 2)`,
}))

const cropBoxStyle = computed(() => ({
  width: cropWidth.value + 'px',
  height: cropHeight.value + 'px',
}))

const ratioLabel = computed(() => `${props.ratioW}:${props.ratioH}`)

function onImageMove(e) {
  imgX.value = e.detail.x
  imgY.value = e.detail.y
}

function confirmCrop() {
  // 将 movable 坐标转换为裁剪参数
  const scale = 1 // 由于 movable-view scale 模式，这里简化为原始比例
  const cropInfo = {
    src: props.src,
    x: Math.abs(imgX.value),
    y: Math.abs(imgY.value),
    width: areaWidth.value,
    height: areaHeight.value,
    ratioW: props.ratioW,
    ratioH: props.ratioH,
  }
  emit('confirm', cropInfo)
}

function handleCancel() {
  emit('cancel')
}
</script>

<style lang="scss" scoped>
.cropper-container {
  background: #000;
  border-radius: $radius-md;
  overflow: hidden;
}

.cropper-header {
  padding: $spacing-sm $spacing-md;
  background: #1c1c1e;
}

.cropper-title {
  color: #fff;
  font-size: 24rpx;
  text-align: center;
  display: block;
}

.cropper-canvas-area {
  position: relative;
  width: 100%;
  overflow: hidden;
  background: #000;
  display: flex;
  justify-content: center;
  align-items: center;
}

.movable-area {
  overflow: hidden;
}

.movable-view {
  display: flex;
  align-items: center;
  justify-content: center;
}

.crop-image {
  display: block;
}

/* 蒙版层 */
.crop-mask {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 10;
}

.mask-top,
.mask-bottom {
  width: 100%;
  background: $crop-mask-bg;
}

.mask-row {
  display: flex;
  flex-direction: row;
}

.mask-left,
.mask-right {
  background: $crop-mask-bg;
}

.crop-box {
  position: relative;
  border: 2rpx dashed $crop-border-color;
  box-sizing: border-box;
}

.corner {
  position: absolute;
  width: 24rpx;
  height: 24rpx;
  border-color: $crop-border-color;
  border-style: solid;
  &.tl {
    top: -2rpx;
    left: -2rpx;
    border-width: 4rpx 0 0 4rpx;
    border-radius: 4rpx 0 0 0;
  }
  &.tr {
    top: -2rpx;
    right: -2rpx;
    border-width: 4rpx 4rpx 0 0;
    border-radius: 0 4rpx 0 0;
  }
  &.bl {
    bottom: -2rpx;
    left: -2rpx;
    border-width: 0 0 4rpx 4rpx;
    border-radius: 0 0 0 4rpx;
  }
  &.br {
    bottom: -2rpx;
    right: -2rpx;
    border-width: 0 4rpx 4rpx 0;
    border-radius: 0 0 4rpx 0;
  }
}

.ratio-tip {
  position: absolute;
  bottom: 8rpx;
  left: 50%;
  transform: translateX(-50%);
  color: rgba(255, 255, 255, 0.8);
  font-size: 20rpx;
  background: rgba(0, 0, 0, 0.5);
  padding: 4rpx 16rpx;
  border-radius: 20rpx;
}

.cropper-actions {
  display: flex;
  justify-content: space-between;
  padding: $spacing-md;
  background: #1c1c1e;
  gap: $spacing-md;
}
</style>
