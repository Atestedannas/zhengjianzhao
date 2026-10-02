<template>
  <view class="page-history">
    <!-- 头部 -->
    <view class="history-header">
      <text class="history-title">历史记录</text>
      <text class="history-sub">最近处理过的照片</text>
    </view>

    <view class="list-wrap">
      <view v-if="loading" class="loading-wrap">
        <view class="loading-spinner" />
        <text>加载中...</text>
      </view>

      <view v-else-if="records.length === 0" class="empty-wrap">
        <view class="empty-icon">
          <text class="empty-emoji">📋</text>
        </view>
        <text class="empty-title">暂无处理记录</text>
        <text class="empty-desc">去首页上传照片开始制作合规证件照</text>
      </view>

      <view v-else class="record-list">
        <view
          v-for="record in records"
          :key="record.id"
          class="record-card"
          @click="viewRecord(record)"
        >
          <view class="record-thumb-wrap">
            <image
              class="record-thumb"
              :src="record.thumb_url || record.result_url"
              mode="aspectFill"
            />
            <view class="record-format-badge">
              <text>{{ record.output_format || 'JPG' }}</text>
            </view>
          </view>
          <view class="record-info">
            <text class="record-template">{{ record.template_name || '自定义' }}</text>
            <view class="record-meta-row">
              <text class="record-meta">{{ record.pixels }}</text>
              <view class="meta-dot" />
              <text class="record-meta">{{ (record.result_size / 1024).toFixed(1) }}KB</text>
            </view>
            <text class="record-time">{{ formatTime(record.created_at) }}</text>
          </view>
          <text class="record-arrow">›</text>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getHistory } from '@/api/process'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const records = ref([])
const loading = ref(true)

onMounted(async () => {
  if (!userStore.isLogin) {
    // H5: 跳转登录页
    // #ifdef H5
    uni.navigateTo({ url: '/pages/login/index' })
    loading.value = false
    return
    // #endif
    // 小程序: 尝试静默登录
    // #ifdef MP-WEIXIN
    try {
      await userStore.login()
      if (!userStore.isLogin) {
        uni.showToast({ title: '请先登录', icon: 'none' })
        loading.value = false
        return
      }
    } catch {
      uni.showToast({ title: '请先登录', icon: 'none' })
      loading.value = false
      return
    }
    // #endif
  }
  await loadHistory()
})

async function loadHistory() {
  loading.value = true
  try {
    const res = await getHistory(10)
    if (res.code === 200) {
      records.value = res.data.records || res.data || []
    }
  } catch {
    uni.showToast({ title: '加载失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function formatTime(dateStr) {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    const month = d.getMonth() + 1
    const day = d.getDate()
    const hour = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${month}月${day}日 ${hour}:${min}`
  } catch {
    return dateStr
  }
}

function viewRecord(record) {
  if (record.result_url) {
    uni.previewImage({
      urls: [record.result_url],
      current: record.result_url,
    })
  }
}
</script>

<style lang="scss" scoped>
.page-history {
  min-height: 100vh;
  background: $page-bg;
  padding-bottom: env(safe-area-inset-bottom, 0);
}

/* ==================== 头部 ==================== */
.history-header {
  padding: $spacing-lg $spacing-md $spacing-md;
}

.history-title {
  display: block;
  font-size: 40rpx;
  font-weight: 800;
  color: $text-primary;
  margin-bottom: 4rpx;
}

.history-sub {
  font-size: 24rpx;
  color: $text-hint;
}

.list-wrap {
  padding: 0 $spacing-md;
}

/* ==================== 加载中 ==================== */
.loading-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: $spacing-2xl * 2;
  gap: $spacing-sm;
  color: $text-hint;
  font-size: 26rpx;
}

.loading-spinner {
  width: 48rpx;
  height: 48rpx;
  border-radius: 50%;
  border: 3rpx solid $border-color;
  border-top-color: $brand-primary;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ==================== 空状态 ==================== */
.empty-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: $spacing-2xl * 2;
  gap: $spacing-sm;
  text-align: center;
}

.empty-icon {
  width: 120rpx;
  height: 120rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(79, 110, 247, 0.06), rgba(123, 92, 247, 0.06));
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: $spacing-sm;
}

.empty-emoji {
  font-size: 56rpx;
}

.empty-title {
  font-size: 30rpx;
  font-weight: 700;
  color: $text-primary;
}

.empty-desc {
  font-size: 24rpx;
  color: $text-hint;
  line-height: 1.6;
}

/* ==================== 记录列表 ==================== */
.record-list {
  display: flex;
  flex-direction: column;
  gap: $spacing-sm;
}

.record-card {
  display: flex;
  align-items: center;
  background: $card-bg;
  border-radius: $radius-lg;
  padding: $spacing-md;
  gap: $spacing-md;
  box-shadow: $shadow-sm;
  transition: all 0.2s;

  &:active {
    transform: scale(0.98);
    box-shadow: $shadow-md;
  }
}

.record-thumb-wrap {
  position: relative;
  flex-shrink: 0;
}

.record-thumb {
  width: 120rpx;
  height: 160rpx;
  border-radius: $radius-md;
  background: #F1F5F9;
  display: block;
}

.record-format-badge {
  position: absolute;
  bottom: -4rpx;
  right: -4rpx;
  padding: 2rpx 10rpx;
  background: $brand-primary;
  border-radius: 8rpx;
  font-size: 18rpx;
  color: #fff;
  font-weight: 600;
}

.record-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  overflow: hidden;
}

.record-template {
  font-size: 30rpx;
  font-weight: 700;
  color: $text-primary;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-meta-row {
  display: flex;
  align-items: center;
  gap: 8rpx;
}

.record-meta {
  font-size: 24rpx;
  color: $text-secondary;
}

.meta-dot {
  width: 4rpx;
  height: 4rpx;
  border-radius: 50%;
  background: $text-hint;
}

.record-time {
  font-size: 22rpx;
  color: $text-hint;
}

.record-arrow {
  font-size: 32rpx;
  color: $text-hint;
  font-weight: 300;
}
</style>