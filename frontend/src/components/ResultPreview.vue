<template>
  <view class="result-preview">
    <!-- 结果图片展示 -->
    <view class="preview-card">
      <view class="preview-image-wrap">
        <image
          class="preview-image"
          :src="imageUrl"
          mode="widthFix"
          :show-menu-by-longpress="true"
          @error="onImageError"
        />
        <view v-if="imageError" class="image-error">
          <text class="error-emoji">🖼️</text>
          <text>图片加载失败，请重新处理</text>
        </view>
      </view>
    </view>

    <!-- 元信息 -->
    <view class="meta-section" v-if="info">
      <view class="meta-title">处理详情</view>
      <view class="meta-card">
        <view class="meta-item">
          <view class="meta-icon-wrap">
            <text>📦</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">文件大小</text>
            <text class="meta-value">{{ info.file_size_kb ? info.file_size_kb.toFixed(1) + ' KB' : '-' }}</text>
          </view>
        </view>
        <view class="meta-item">
          <view class="meta-icon-wrap">
            <text>📐</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">像素尺寸</text>
            <text class="meta-value">{{ info.pixels || '-' }}</text>
          </view>
        </view>
        <view class="meta-item">
          <view class="meta-icon-wrap">
            <text>🔍</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">DPI</text>
            <text class="meta-value">{{ info.dpi || '-' }}</text>
          </view>
        </view>
        <view class="meta-item">
          <view class="meta-icon-wrap">
            <text>📄</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">输出格式</text>
            <text class="meta-value">{{ info.output_format || 'JPEG' }}</text>
          </view>
        </view>
        <view class="meta-item" v-if="info.faces_detected !== undefined">
          <view class="meta-icon-wrap">
            <text>👤</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">检测人脸</text>
            <text class="meta-value">{{ info.faces_detected }} 个</text>
          </view>
        </view>
        <view class="meta-item" v-if="info.processing_time_ms !== undefined">
          <view class="meta-icon-wrap">
            <text>⏱️</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">处理耗时</text>
            <text class="meta-value">{{ (info.processing_time_ms / 1000).toFixed(1) }}s</text>
          </view>
        </view>
        <view class="meta-item" v-if="info.remaining_free_count !== undefined">
          <view class="meta-icon-wrap">
            <text>🎁</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">剩余免费次数</text>
            <text class="meta-value highlight">{{ info.remaining_free_count === -1 ? '∞' : info.remaining_free_count }}</text>
          </view>
        </view>
        <view class="meta-item warning-item" v-if="info.warnings && info.warnings.length">
          <view class="meta-icon-wrap warning-icon">
            <text>⚠️</text>
          </view>
          <view class="meta-text">
            <text class="meta-label">提示</text>
            <text class="meta-value warning-text">{{ info.warnings.join('; ') }}</text>
          </view>
        </view>
      </view>
    </view>

    <!-- 操作按钮 -->
    <view class="result-actions">
      <view class="action-btn download-btn" @click="handleDownload" :class="{ loading: downloading }">
        <text class="action-btn-icon">⬇️</text>
        <text>下载照片</text>
      </view>

      <view class="action-btn reprocess-btn" @click="$emit('reprocess')">
        <text class="action-btn-icon">🔄</text>
        <text>重新处理</text>
      </view>

      <view class="action-btn back-btn" @click="$emit('backHome')">
        <text class="action-btn-icon">🏠</text>
        <text>返回首页</text>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { downloadResult } from '@/api/process'

const props = defineProps({
  imageUrl: { type: String, required: true },
  info: { type: Object, default: () => ({}) },
  recordId: { type: [Number, String], default: null },
})

const emit = defineEmits(['reprocess', 'backHome'])

const downloading = ref(false)
const imageError = ref(false)

function onImageError() {
  imageError.value = true
}

async function handleDownload() {
  downloading.value = true
  try {
    // #ifdef MP-WEIXIN
    uni.showLoading({ title: '下载中...' })
    const downloadRes = await uni.downloadFile({ url: props.imageUrl })
    if (downloadRes.statusCode === 200) {
      await uni.saveImageToPhotosAlbum({ filePath: downloadRes.tempFilePath })
      uni.hideLoading()
      uni.showToast({ title: '已保存到相册', icon: 'success' })
    }
    // #endif

    // #ifdef H5
    if (props.recordId) {
      const res = await downloadResult(props.recordId)
      if (res.code === 200 && res.data?.download_url) {
        const link = document.createElement('a')
        link.href = res.data.download_url
        link.download = 'photo.jpg'
        link.click()
        uni.showToast({ title: '下载已开始', icon: 'success' })
      }
    } else {
      const link = document.createElement('a')
      link.href = props.imageUrl
      link.download = 'photo.jpg'
      link.click()
      uni.showToast({ title: '下载已开始', icon: 'success' })
    }
    // #endif
  } catch (e) {
    uni.showToast({ title: '保存失败: ' + (e.errMsg || '未知错误'), icon: 'none' })
  } finally {
    downloading.value = false
  }
}
</script>

<style lang="scss" scoped>
.result-preview {
  padding: $spacing-md;
}

/* ==================== 预览图片 ==================== */
.preview-card {
  background: $card-bg;
  border-radius: $radius-lg;
  overflow: hidden;
  box-shadow: $shadow-md;
  margin-bottom: $spacing-md;
}

.preview-image-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 400rpx;
  background: #F8FAFC;
}

.preview-image {
  width: 100%;
  display: block;
}

.image-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $spacing-sm;
  padding: $spacing-xl;
  color: $text-hint;
  font-size: 26rpx;
}

.error-emoji {
  font-size: 64rpx;
}

/* ==================== 元信息 ==================== */
.meta-section {
  margin-bottom: $spacing-md;
}

.meta-title {
  font-size: 24rpx;
  font-weight: 600;
  color: $text-hint;
  margin-bottom: $spacing-sm;
  padding-left: $spacing-xs;
  text-transform: uppercase;
  letter-spacing: 2rpx;
}

.meta-card {
  background: $card-bg;
  border-radius: $radius-lg;
  overflow: hidden;
  box-shadow: $shadow-sm;
}

.meta-item {
  display: flex;
  align-items: center;
  padding: $spacing-md $spacing-lg;
  gap: $spacing-md;
  border-bottom: 1rpx solid $border-color;

  &:last-child {
    border-bottom: none;
  }
}

.meta-icon-wrap {
  width: 56rpx;
  height: 56rpx;
  border-radius: 16rpx;
  background: #F8FAFC;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24rpx;
  flex-shrink: 0;
}

.warning-icon {
  background: rgba(245, 158, 11, 0.08);
}

.meta-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2rpx;
}

.meta-label {
  font-size: 22rpx;
  color: $text-hint;
}

.meta-value {
  font-size: 28rpx;
  font-weight: 600;
  color: $text-primary;

  &.highlight {
    color: $brand-primary;
    font-weight: 700;
  }

  &.warning-text {
    font-size: 22rpx;
    font-weight: 400;
    color: $u-warning;
  }
}

/* ==================== 操作按钮 ==================== */
.result-actions {
  display: flex;
  flex-direction: column;
  gap: $spacing-sm;
  padding: $spacing-sm 0;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: $spacing-xs;
  padding: 28rpx 0;
  border-radius: $radius-md;
  font-size: 30rpx;
  font-weight: 600;
  transition: all 0.2s;

  &:active {
    transform: scale(0.97);
  }
}

.action-btn-icon {
  font-size: 28rpx;
}

.download-btn {
  background: $brand-gradient;
  color: #fff;
  box-shadow: 0 6rpx 20rpx rgba(79, 110, 247, 0.35);

  &.loading {
    opacity: 0.7;
    pointer-events: none;
  }
}

.reprocess-btn {
  background: $card-bg;
  color: $brand-primary;
  border: 2rpx solid $brand-primary;
}

.back-btn {
  background: $card-bg;
  color: $text-secondary;
  border: 2rpx solid $border-color;
}
</style>