<template>
  <view class="page-result">
    <ResultPreview
      v-if="photoStore.resultUrl"
      :imageUrl="photoStore.resultUrl"
      :info="photoStore.resultInfo"
      :recordId="photoStore.resultInfo.record_id"
      @reprocess="handleReprocess"
      @backHome="handleBackHome"
    />

    <!-- 无结果时的空状态 -->
    <view v-else class="empty-state">
      <view class="empty-icon">
        <text class="empty-emoji">📭</text>
      </view>
      <text class="empty-title">暂无处理结果</text>
      <text class="empty-desc">去首页上传照片开始制作合规证件照</text>
      <view class="empty-btn" @click="handleBackHome">
        <text>去处理照片</text>
      </view>
    </view>
  </view>
</template>

<script setup>
import { usePhotoStore } from '@/stores/photo'
import ResultPreview from '@/components/ResultPreview.vue'

const photoStore = usePhotoStore()

function handleReprocess() {
  photoStore.reset()
  uni.navigateBack()
}

function handleBackHome() {
  photoStore.reset()
  uni.switchTab({ url: '/pages/index/index' })
}
</script>

<style lang="scss" scoped>
.page-result {
  min-height: 100vh;
  background: $page-bg;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: $spacing-2xl * 2 $spacing-lg;
  text-align: center;
}

.empty-icon {
  width: 140rpx;
  height: 140rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(79, 110, 247, 0.06), rgba(123, 92, 247, 0.06));
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: $spacing-md;
}

.empty-emoji {
  font-size: 64rpx;
}

.empty-title {
  font-size: 34rpx;
  font-weight: 700;
  color: $text-primary;
  margin-bottom: $spacing-xs;
}

.empty-desc {
  font-size: 26rpx;
  color: $text-hint;
  margin-bottom: $spacing-lg;
  line-height: 1.6;
}

.empty-btn {
  padding: 24rpx 56rpx;
  background: $brand-gradient;
  border-radius: $radius-md;
  color: #fff;
  font-size: 28rpx;
  font-weight: 600;
  box-shadow: 0 6rpx 20rpx rgba(79, 110, 247, 0.3);
  transition: all 0.2s;

  &:active {
    transform: scale(0.96);
  }
}
</style>