<template>
  <view class="page-index">
    <!-- Hero 区域 -->
    <view class="hero-section" :style="{ paddingTop: statusBarHeight + 20 + 'px' }">
      <view class="hero-bg" />
      <view class="hero-content">
        <view class="hero-icon-wrap">
          <view class="hero-icon">
            <text class="hero-icon-text">📸</text>
          </view>
        </view>
        <text class="hero-title">证件照制作</text>
        <text class="hero-subtitle">智能裁剪 · 一键换背景 · 合规处理</text>
      </view>
    </view>

    <!-- 步骤指示器 -->
    <view class="steps-wrap">
      <view class="steps-bar">
        <view class="step" :class="{ active: currentStep >= 0, done: currentStep > 0 }">
          <view class="step-num">
            <text v-if="currentStep > 0">✓</text>
            <text v-else>1</text>
          </view>
          <text class="step-label">上传</text>
        </view>
        <view class="step-connector" :class="{ active: currentStep > 0 }">
          <view class="step-line-fill" :style="{ width: currentStep > 0 ? '100%' : '0%' }" />
        </view>
        <view class="step" :class="{ active: currentStep >= 1, done: currentStep > 1 }">
          <view class="step-num">
            <text v-if="currentStep > 1">✓</text>
            <text v-else>2</text>
          </view>
          <text class="step-label">裁剪</text>
        </view>
        <view class="step-connector" :class="{ active: currentStep > 1 }">
          <view class="step-line-fill" :style="{ width: currentStep > 1 ? '100%' : '0%' }" />
        </view>
        <view class="step" :class="{ active: currentStep >= 2, done: currentStep > 2 }">
          <view class="step-num">
            <text v-if="currentStep > 2">✓</text>
            <text v-else>3</text>
          </view>
          <text class="step-label">规格</text>
        </view>
        <view class="step-connector" :class="{ active: currentStep > 2 }">
          <view class="step-line-fill" :style="{ width: currentStep > 2 ? '100%' : '0%' }" />
        </view>
        <view class="step" :class="{ active: currentStep >= 3 }">
          <view class="step-num">
            <text>4</text>
          </view>
          <text class="step-label">生成</text>
        </view>
      </view>
    </view>

    <!-- 第一步：上传照片 -->
    <view v-if="currentStep === 0" class="step-content">
      <view class="upload-card" @click="handleUpload">
        <view class="upload-card-inner" v-if="!photoStore.originalPath">
          <view class="upload-icon-circle">
            <view class="upload-icon-dot" />
            <text class="upload-icon-symbol">+</text>
          </view>
          <text class="upload-title">点击上传照片</text>
          <text class="upload-desc">支持 JPG、PNG、WebP、BMP 格式</text>
          <view class="upload-tag">
            <text>最大 10MB</text>
          </view>
        </view>
        <image
          v-else
          class="upload-preview-img"
          :src="photoStore.originalPath"
          mode="widthFix"
        />
        <view v-if="photoStore.originalPath" class="upload-overlay">
          <text class="upload-overlay-text">点击重新选择</text>
        </view>
      </view>
      <view class="step-actions" v-if="photoStore.originalPath">
        <view class="btn-primary" @click="nextStep">
          <text>下一步：裁剪照片</text>
          <text class="btn-arrow">→</text>
        </view>
      </view>
    </view>

    <!-- 第二步：裁剪 -->
    <view v-if="currentStep === 1" class="step-content">
      <view class="cropper-card">
        <ImageCropper
          :src="photoStore.originalPath"
          :ratioW="ratioW"
          :ratioH="ratioH"
          @confirm="onCropConfirm"
          @cancel="onCropCancel"
        />
      </view>
      <view class="step-actions">
        <view class="btn-secondary" @click="currentStep = 0">
          <text>← 上一步</text>
        </view>
        <view class="btn-primary" :class="{ disabled: !photoStore.croppedPath }" @click="nextStep">
          <text>下一步：选择规格</text>
          <text class="btn-arrow">→</text>
        </view>
      </view>
    </view>

    <!-- 第三步：选择规格 -->
    <view v-if="currentStep === 2" class="step-content">
      <view class="template-card-wrap">
        <TemplateSelector @change="onTemplateChange" />
      </view>
      <view class="step-actions">
        <view class="btn-secondary" @click="currentStep = 1">
          <text>← 上一步</text>
        </view>
        <view class="btn-primary btn-generate" @click="handleGenerate" :class="{ loading: photoStore.isProcessing }">
          <text v-if="!photoStore.isProcessing">🎯 生成合规照片</text>
          <text v-else>处理中...</text>
        </view>
      </view>
    </view>

    <!-- 第四步：处理中 -->
    <view v-if="currentStep === 3 && photoStore.isProcessing" class="step-content">
      <view class="processing-card">
        <view class="processing-spinner">
          <view class="spinner-ring" />
          <view class="spinner-ring spinner-ring-2" />
          <view class="spinner-ring spinner-ring-3" />
        </view>
        <text class="processing-title">正在处理照片...</text>
        <text class="processing-desc">智能美颜 · 尺寸调整 · 背景替换 · 格式优化</text>
        <view class="processing-steps">
          <view class="proc-step done">
            <view class="proc-dot" />
            <text>上传完成</text>
          </view>
          <view class="proc-step done">
            <view class="proc-dot" />
            <text>裁剪完成</text>
          </view>
          <view class="proc-step active">
            <view class="proc-dot" />
            <text>AI 处理中</text>
          </view>
        </view>
      </view>
    </view>

    <!-- PayModal -->
    <PayModal
      :visible="payStore.showModal"
      :amount="payStore.amount"
      :orderNo="payStore.orderNo"
      @close="payStore.closePayModal()"
      @paid="onPaid"
      @cancel="payStore.closePayModal()"
    />
  </view>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { onShow, onUnload } from '@dcloudio/uni-app'
import ImageCropper from '@/components/ImageCropper.vue'
import TemplateSelector from '@/components/TemplateSelector.vue'
import PayModal from '@/components/PayModal.vue'
import { usePhotoStore } from '@/stores/photo'
import { useUserStore } from '@/stores/user'
import { usePaymentStore } from '@/stores/payment'

const photoStore = usePhotoStore()
const userStore = useUserStore()
const payStore = usePaymentStore()

const currentStep = ref(0)
const statusBarHeight = ref(0)
const tempTemplateParams = ref(null)

onMounted(() => {
  try {
    const sysInfo = uni.getSystemInfoSync()
    statusBarHeight.value = sysInfo.statusBarHeight || 20
  } catch { statusBarHeight.value = 20 }
})

// 页面显示时的静默登录
onShow(async () => {
  if (!userStore.isLogin) {
    await userStore.login()
  }
})

// 离开页面时停止轮询，避免定时器泄漏和后台重复请求
onUnload(() => {
  photoStore.cancelPolling()
})
onUnmounted(() => {
  photoStore.cancelPolling()
})

// 裁剪比例
const ratioW = computed(() => {
  if (tempTemplateParams.value?.mode === 'template') {
    return tempTemplateParams.value.template.width_px || 3
  }
  if (tempTemplateParams.value?.mode === 'custom' && tempTemplateParams.value.params.width) {
    return parseInt(tempTemplateParams.value.params.width) || 3
  }
  return 3
})

const ratioH = computed(() => {
  if (tempTemplateParams.value?.mode === 'template') {
    return tempTemplateParams.value.template.height_px || 4
  }
  if (tempTemplateParams.value?.mode === 'custom' && tempTemplateParams.value.params.height) {
    return parseInt(tempTemplateParams.value.params.height) || 4
  }
  return 4
})

function handleUpload() {
  // #ifdef MP-WEIXIN
  uni.chooseImage({
    count: 1,
    sizeType: ['original', 'compressed'],
    sourceType: ['album', 'camera'],
    success: (res) => {
      photoStore.originalPath = res.tempFilePaths[0]
    },
    fail: (err) => {
      if (err.errMsg !== 'chooseImage:fail cancel') {
        uni.showToast({ title: '选择图片失败', icon: 'none' })
      }
    },
  })
  // #endif

  // #ifdef H5
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/jpeg,image/png'
  input.onchange = (e) => {
    const file = e.target.files[0]
    if (file) {
      if (file.size > 10 * 1024 * 1024) {
        uni.showToast({ title: '文件大小不能超过 10MB', icon: 'none' })
        return
      }
      const reader = new FileReader()
      reader.onload = (ev) => {
        photoStore.originalPath = ev.target.result
      }
      reader.readAsDataURL(file)
    }
  }
  input.click()
  // #endif
}

function onCropConfirm(cropInfo) {
  photoStore.croppedPath = cropInfo.src
  uni.showToast({ title: '裁剪完成', icon: 'success' })
}

function onCropCancel() {
  photoStore.originalPath = ''
  photoStore.croppedPath = ''
  currentStep.value = 0
}

function onTemplateChange(data) {
  tempTemplateParams.value = data
  if (data.mode === 'template') {
    photoStore.selectedTemplate = data.template
    photoStore.customParams.bgColor = data.bgColor
    photoStore.customParams.beautifyLevel = data.beautifyLevel ?? 1
  } else {
    photoStore.selectedTemplate = null
    photoStore.customParams = {
      width: data.params.width || null,
      height: data.params.height || null,
      resizeMode: data.params.resizeMode || 'crop',
      upscale: data.params.upscale !== undefined ? data.params.upscale : true,
      dpi: parseInt(data.params.dpi) || 350,
      minKb: parseInt(data.params.minKb) || 0,
      maxKb: parseInt(data.params.maxKb) || 100,
      bgColor: data.params.bgColor || 'white',
      outputFormat: data.params.outputFormat || 'JPEG',
      beautifyLevel: data.params.beautifyLevel ?? 1,
      beautifySmooth: data.params.beautifySmooth !== undefined ? data.params.beautifySmooth : true,
      beautifyBrighten: data.params.beautifyBrighten !== undefined ? data.params.beautifyBrighten : true,
      beautifyBlemish: data.params.beautifyBlemish !== undefined ? data.params.beautifyBlemish : true,
      gender: data.params.gender || '',
      idPhotoAlign: data.params.idPhotoAlign || false,
    }
  }
}

async function handleGenerate() {
  // 防止重复提交：后端按次扣费，处理中不允许再次点击
  if (photoStore.isProcessing) return
  if (!photoStore.originalPath) {
    uni.showToast({ title: '请先上传照片', icon: 'none' })
    return
  }
  if (!userStore.isLogin) {
    await userStore.login()
    if (!userStore.isLogin) {
      uni.showToast({ title: '请先登录', icon: 'none' })
      return
    }
  }

  currentStep.value = 3
  const filePath = photoStore.croppedPath || photoStore.originalPath

  const result = await photoStore.processPhoto(filePath)

  if (result.cancelled) return

  if (result.needPay) {
    payStore.openPayModal({
      orderNo: result.orderNo,
      amount: result.amount,
    })
    return
  }

  if (result.success) {
    await userStore.fetchFreeCount()
    uni.navigateTo({ url: '/pages/result/index' })
    return
  }

  // 处理失败 / 超时：失败提示与次数已由 store 同步，这里退回规格页可重新提交
  currentStep.value = 2
}

async function onPaid() {
  payStore.closePayModal()
  if (photoStore.isProcessing) return
  const filePath = photoStore.croppedPath || photoStore.originalPath
  const result = await photoStore.processPhoto(filePath)

  if (result.cancelled) return

  if (result.success) {
    await userStore.fetchFreeCount()
    uni.navigateTo({ url: '/pages/result/index' })
    return
  }

  if (!result.needPay) {
    currentStep.value = 2
  }
}

function nextStep() {
  if (currentStep.value === 0 && !photoStore.originalPath) {
    uni.showToast({ title: '请先上传照片', icon: 'none' })
    return
  }
  if (currentStep.value === 1 && !photoStore.croppedPath) {
    uni.showToast({ title: '请先完成裁剪', icon: 'none' })
    return
  }
  currentStep.value++
}
</script>

<style lang="scss" scoped>
.page-index {
  min-height: 100vh;
  background: $page-bg;
  padding-bottom: env(safe-area-inset-bottom, 0);
}

/* ==================== Hero 区域 ==================== */
.hero-section {
  position: relative;
  overflow: hidden;
  padding: 0 $spacing-lg $spacing-xl;
}

.hero-bg {
  position: absolute;
  top: -60%;
  left: -20%;
  right: -20%;
  bottom: 0;
  background: $brand-gradient;
  border-radius: 0 0 50% 50%;
  transform: scaleX(1.5);
}

.hero-content {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: $spacing-md;
}

.hero-icon-wrap {
  margin-bottom: $spacing-md;
}

.hero-icon {
  width: 100rpx;
  height: 100rpx;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 28rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(10px);
  box-shadow: 0 8rpx 32rpx rgba(0, 0, 0, 0.12);
}

.hero-icon-text {
  font-size: 48rpx;
}

.hero-title {
  font-size: 44rpx;
  font-weight: 800;
  color: #fff;
  margin-bottom: $spacing-xs;
  letter-spacing: 2rpx;
}

.hero-subtitle {
  font-size: 26rpx;
  color: rgba(255, 255, 255, 0.8);
  letter-spacing: 1rpx;
}

/* ==================== 步骤指示器 ==================== */
.steps-wrap {
  padding: 0 $spacing-md;
  margin-top: -$spacing-md;
  position: relative;
  z-index: 2;
}

.steps-bar {
  display: flex;
  align-items: center;
  background: $card-bg;
  border-radius: $radius-lg;
  padding: $spacing-md $spacing-sm;
  box-shadow: $shadow-md;
}

.step {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6rpx;
  flex-shrink: 0;
  z-index: 1;
}

.step-num {
  width: 48rpx;
  height: 48rpx;
  border-radius: 50%;
  background: #E2E8F0;
  color: #94A3B8;
  font-size: 22rpx;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);

  .step.active & {
    background: $brand-gradient;
    color: #fff;
    transform: scale(1.1);
    box-shadow: 0 4rpx 12rpx rgba(79, 110, 247, 0.4);
  }

  .step.done & {
    background: $u-success;
    color: #fff;
  }
}

.step-label {
  font-size: 20rpx;
  color: #94A3B8;
  font-weight: 500;
  transition: color 0.3s;

  .step.active & { color: $brand-primary; font-weight: 700; }
  .step.done & { color: $u-success; }
}

.step-connector {
  flex: 1;
  height: 4rpx;
  background: #E2E8F0;
  margin: 0 2rpx;
  margin-bottom: 20rpx;
  border-radius: 2rpx;
  overflow: hidden;

  &.active {
    background: #E2E8F0;
  }
}

.step-line-fill {
  height: 100%;
  background: $brand-gradient;
  border-radius: 2rpx;
  transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

/* ==================== 步骤内容 ==================== */
.step-content {
  padding: $spacing-md;
}

/* ==================== 上传区域 ==================== */
.upload-card {
  background: $card-bg;
  border-radius: $radius-lg;
  overflow: hidden;
  box-shadow: $shadow-md;
  transition: transform 0.2s, box-shadow 0.2s;
  position: relative;

  &:active {
    transform: scale(0.98);
    box-shadow: $shadow-sm;
  }
}

.upload-card-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: $spacing-2xl $spacing-lg;
  gap: $spacing-sm;
  position: relative;
  overflow: hidden;

  &::before {
    content: '';
    position: absolute;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: radial-gradient(circle at center, rgba(79, 110, 247, 0.03) 0%, transparent 70%);
  }
}

.upload-icon-circle {
  width: 120rpx;
  height: 120rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(79, 110, 247, 0.08), rgba(123, 92, 247, 0.08));
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  border: 3rpx dashed rgba(79, 110, 247, 0.25);
  margin-bottom: $spacing-xs;
}

.upload-icon-dot {
  position: absolute;
  width: 16rpx;
  height: 16rpx;
  border-radius: 50%;
  background: $brand-gradient;
  opacity: 0.3;
  animation: uploadPulse 2s ease-in-out infinite;
}

@keyframes uploadPulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 0.6; transform: scale(1.2); }
}

.upload-icon-symbol {
  font-size: 56rpx;
  font-weight: 300;
  color: $brand-primary;
  position: relative;
  z-index: 1;
}

.upload-title {
  font-size: 32rpx;
  font-weight: 700;
  color: $text-primary;
  position: relative;
  z-index: 1;
}

.upload-desc {
  font-size: 24rpx;
  color: $text-hint;
  position: relative;
  z-index: 1;
}

.upload-tag {
  padding: 6rpx 20rpx;
  background: rgba(79, 110, 247, 0.08);
  border-radius: 20rpx;
  font-size: 22rpx;
  color: $brand-primary;
  font-weight: 500;
  position: relative;
  z-index: 1;
}

.upload-preview-img {
  width: 100%;
  display: block;
}

.upload-overlay {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: $spacing-md;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.5));
  display: flex;
  justify-content: center;
}

.upload-overlay-text {
  color: #fff;
  font-size: 24rpx;
  font-weight: 500;
}

/* ==================== 裁剪卡片 ==================== */
.cropper-card {
  background: $card-bg;
  border-radius: $radius-lg;
  overflow: hidden;
  box-shadow: $shadow-md;
}

/* ==================== 模板卡片 ==================== */
.template-card-wrap {
  background: $card-bg;
  border-radius: $radius-lg;
  padding: $spacing-md;
  box-shadow: $shadow-md;
}

/* ==================== 按钮 ==================== */
.step-actions {
  display: flex;
  gap: $spacing-md;
  margin-top: $spacing-lg;
  padding: 0 $spacing-sm;
}

.btn-primary {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: $spacing-xs;
  padding: 28rpx 0;
  background: $brand-gradient;
  border-radius: $radius-md;
  color: #fff;
  font-size: 30rpx;
  font-weight: 700;
  box-shadow: 0 6rpx 20rpx rgba(79, 110, 247, 0.35);
  transition: all 0.2s;
  letter-spacing: 1rpx;

  &:active {
    transform: scale(0.97);
    box-shadow: 0 4rpx 12rpx rgba(79, 110, 247, 0.25);
  }

  &.disabled {
    opacity: 0.4;
    pointer-events: none;
  }

  &.loading {
    opacity: 0.7;
    pointer-events: none;
  }
}

.btn-arrow {
  font-size: 28rpx;
}

.btn-secondary {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28rpx 36rpx;
  background: $card-bg;
  border-radius: $radius-md;
  color: $text-secondary;
  font-size: 28rpx;
  font-weight: 600;
  box-shadow: $shadow-sm;
  border: 2rpx solid $border-color;
  transition: all 0.2s;

  &:active {
    background: #F8FAFC;
    transform: scale(0.97);
  }
}

.btn-generate {
  background: $brand-gradient-warm;
  box-shadow: 0 6rpx 20rpx rgba(236, 72, 153, 0.3);
}

/* ==================== 处理中 ==================== */
.processing-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: $card-bg;
  border-radius: $radius-lg;
  padding: $spacing-2xl $spacing-lg;
  box-shadow: $shadow-md;
  gap: $spacing-md;
}

.processing-spinner {
  position: relative;
  width: 100rpx;
  height: 100rpx;
}

.spinner-ring {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  border: 4rpx solid transparent;
  border-top-color: $brand-primary;
  animation: spin 1s linear infinite;
}

.spinner-ring-2 {
  width: 70%;
  height: 70%;
  top: 15%;
  left: 15%;
  border-top-color: $brand-secondary;
  animation-duration: 0.8s;
  animation-direction: reverse;
}

.spinner-ring-3 {
  width: 40%;
  height: 40%;
  top: 30%;
  left: 30%;
  border-top-color: #EC4899;
  animation-duration: 0.6s;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.processing-title {
  font-size: 34rpx;
  font-weight: 700;
  color: $text-primary;
}

.processing-desc {
  font-size: 24rpx;
  color: $text-hint;
  text-align: center;
  line-height: 1.8;
}

.processing-steps {
  display: flex;
  flex-direction: column;
  gap: $spacing-sm;
  width: 100%;
  max-width: 400rpx;
  margin-top: $spacing-sm;
}

.proc-step {
  display: flex;
  align-items: center;
  gap: $spacing-sm;
  font-size: 24rpx;
  color: $text-hint;

  &.done { color: $u-success; }
  &.active { color: $brand-primary; font-weight: 600; }
}

.proc-dot {
  width: 12rpx;
  height: 12rpx;
  border-radius: 50%;
  background: $border-color;

  .proc-step.done & { background: $u-success; }
  .proc-step.active & {
    background: $brand-primary;
    animation: procPulse 1s ease-in-out infinite;
  }
}

@keyframes procPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(79, 110, 247, 0.4); }
  50% { box-shadow: 0 0 0 8rpx rgba(79, 110, 247, 0); }
}
</style>