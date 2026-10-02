<template>
  <view class="page-user">
    <!-- 头部渐变背景 + 用户信息 -->
    <view class="user-header-wrap">
      <view class="user-header-bg" />
      <view class="user-header-content">
        <view class="avatar-wrap">
          <image
            class="avatar"
            :src="userStore.avatarUrl || '/static/default-avatar.png'"
            mode="aspectFill"
          />
          <view class="avatar-ring" />
        </view>
        <text class="nickname">{{ userStore.nickname || '未登录用户' }}</text>
        <text class="user-id" v-if="userStore.profile?.id">ID: {{ userStore.profile.id }}</text>

        <!-- 登录/退出按钮 -->
        <!-- #ifdef H5 -->
        <view class="header-actions">
          <view v-if="!userStore.isLogin" class="login-btn" @click="goLogin">
            <text>立即登录</text>
          </view>
          <view v-else class="logout-btn" @click="handleLogout">
            <text>退出登录</text>
          </view>
        </view>
        <!-- #endif -->
      </view>
    </view>

    <!-- 数据卡片 -->
    <view class="stats-row">
      <view class="stat-card stat-free">
        <view class="stat-icon-wrap">
          <text class="stat-icon">🎁</text>
        </view>
        <view class="stat-info">
          <text class="stat-value">{{ userStore.freeCountText }}</text>
          <text class="stat-label">剩余免费次数</text>
        </view>
        <view class="stat-badge">
          <text>每次消耗 1 次</text>
        </view>
      </view>
      <view class="stat-card stat-balance" v-if="userStore.balance > 0">
        <view class="stat-icon-wrap">
          <text class="stat-icon">💰</text>
        </view>
        <view class="stat-info">
          <text class="stat-value">¥{{ userStore.balance }}</text>
          <text class="stat-label">账户余额</text>
        </view>
      </view>
    </view>

    <!-- 功能菜单 -->
    <view class="menu-section">
      <view class="menu-title">功能服务</view>
      <view class="menu-card">
        <view class="menu-item" @click="goHistory">
          <view class="menu-icon-wrap menu-icon-clock">
            <text class="menu-emoji">📋</text>
          </view>
          <view class="menu-body">
            <text class="menu-text">历史记录</text>
            <text class="menu-hint">查看处理过的照片</text>
          </view>
          <text class="menu-arrow">›</text>
        </view>
        <view class="menu-divider" />
        <view class="menu-item" @click="handleClearCache">
          <view class="menu-icon-wrap menu-icon-trash">
            <text class="menu-emoji">🗑️</text>
          </view>
          <view class="menu-body">
            <text class="menu-text">清除缓存</text>
            <text class="menu-hint">释放本地存储空间</text>
          </view>
          <text class="menu-arrow">›</text>
        </view>
      </view>
    </view>

    <!-- 版本信息 -->
    <view class="version-info">
      <text>照片合规处理 v1.0.0</text>
    </view>
  </view>
</template>

<script setup>
import { onMounted } from 'vue'
import { useUserStore } from '@/stores/user'
import { usePhotoStore } from '@/stores/photo'

const userStore = useUserStore()
const photoStore = usePhotoStore()

onMounted(async () => {
  if (!userStore.isLogin) {
    // 小程序: 尝试静默登录
    // #ifdef MP-WEIXIN
    try {
      await userStore.login()
    } catch {}
    // #endif
  }
  if (userStore.isLogin) {
    try {
      await userStore.fetchFreeCount()
    } catch {}
  }
})

function goLogin() {
  // #ifdef H5
  uni.navigateTo({ url: '/pages/login/index' })
  // #endif
}

function handleLogout() {
  userStore.logout()
  photoStore.reset()
  uni.showToast({ title: '已退出登录', icon: 'success' })
}

function goHistory() {
  uni.switchTab({ url: '/pages/history/index' })
}

function handleClearCache() {
  uni.showModal({
    title: '清除缓存',
    content: '将清除本地缓存的图片数据',
    success: (res) => {
      if (res.confirm) {
        uni.clearStorageSync()
        photoStore.reset()
        uni.showToast({ title: '缓存已清除', icon: 'success' })
      }
    },
  })
}
</script>

<style lang="scss" scoped>
.page-user {
  min-height: 100vh;
  background: $page-bg;
  padding-bottom: env(safe-area-inset-bottom, 0);
}

/* ==================== 头部 ==================== */
.user-header-wrap {
  position: relative;
  overflow: hidden;
  padding-bottom: $spacing-xl;
}

.user-header-bg {
  position: absolute;
  top: -80%;
  left: -30%;
  right: -30%;
  bottom: 0;
  background: $brand-gradient;
  border-radius: 0 0 50% 50%;
  transform: scaleX(1.5);
}

.user-header-content {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: $spacing-2xl;
  padding-bottom: $spacing-lg;
}

.avatar-wrap {
  position: relative;
  margin-bottom: $spacing-sm;
}

.avatar {
  width: 120rpx;
  height: 120rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.3);
  border: 4rpx solid rgba(255, 255, 255, 0.6);
}

.avatar-ring {
  position: absolute;
  top: -8rpx;
  left: -8rpx;
  right: -8rpx;
  bottom: -8rpx;
  border-radius: 50%;
  border: 3rpx dashed rgba(255, 255, 255, 0.3);
  animation: avatarSpin 20s linear infinite;
}

@keyframes avatarSpin {
  to { transform: rotate(360deg); }
}

.nickname {
  font-size: 36rpx;
  font-weight: 700;
  color: #fff;
  margin-bottom: 4rpx;
}

.user-id {
  font-size: 22rpx;
  color: rgba(255, 255, 255, 0.7);
  margin-bottom: $spacing-sm;
}

.header-actions {
  margin-top: $spacing-sm;
}

.login-btn {
  padding: 14rpx 48rpx;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 40rpx;
  color: #fff;
  font-size: 28rpx;
  font-weight: 600;
  backdrop-filter: blur(10px);
  border: 2rpx solid rgba(255, 255, 255, 0.4);
  transition: all 0.2s;

  &:active {
    background: rgba(255, 255, 255, 0.35);
    transform: scale(0.96);
  }
}

.logout-btn {
  padding: 14rpx 48rpx;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 40rpx;
  color: rgba(255, 255, 255, 0.8);
  font-size: 26rpx;
  font-weight: 500;
  transition: all 0.2s;

  &:active {
    background: rgba(255, 255, 255, 0.3);
    transform: scale(0.96);
  }
}

/* ==================== 数据卡片 ==================== */
.stats-row {
  display: flex;
  gap: $spacing-sm;
  padding: 0 $spacing-md;
  margin-top: -$spacing-md;
  position: relative;
  z-index: 2;
}

.stat-card {
  flex: 1;
  background: $card-bg;
  border-radius: $radius-lg;
  padding: $spacing-md;
  box-shadow: $shadow-md;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $spacing-xs;
  text-align: center;
}

.stat-icon-wrap {
  width: 64rpx;
  height: 64rpx;
  border-radius: 20rpx;
  display: flex;
  align-items: center;
  justify-content: center;
}

.stat-free .stat-icon-wrap {
  background: linear-gradient(135deg, rgba(79, 110, 247, 0.1), rgba(123, 92, 247, 0.1));
}

.stat-balance .stat-icon-wrap {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.1), rgba(239, 68, 68, 0.1));
}

.stat-icon {
  font-size: 32rpx;
}

.stat-info {
  display: flex;
  flex-direction: column;
  gap: 2rpx;
}

.stat-value {
  font-size: 36rpx;
  font-weight: 800;
  color: $text-primary;
}

.stat-free .stat-value {
  color: $brand-primary;
}

.stat-balance .stat-value {
  color: $u-warning;
}

.stat-label {
  font-size: 22rpx;
  color: $text-hint;
}

.stat-badge {
  padding: 4rpx 16rpx;
  background: rgba(79, 110, 247, 0.06);
  border-radius: 12rpx;
  font-size: 20rpx;
  color: $brand-primary;
  font-weight: 500;
}

/* ==================== 菜单 ==================== */
.menu-section {
  padding: $spacing-md;
}

.menu-title {
  font-size: 24rpx;
  font-weight: 600;
  color: $text-hint;
  margin-bottom: $spacing-sm;
  padding-left: $spacing-xs;
  text-transform: uppercase;
  letter-spacing: 2rpx;
}

.menu-card {
  background: $card-bg;
  border-radius: $radius-lg;
  overflow: hidden;
  box-shadow: $shadow-sm;
}

.menu-item {
  display: flex;
  align-items: center;
  padding: $spacing-md $spacing-lg;
  gap: $spacing-md;
  transition: background 0.15s;

  &:active {
    background: #F8FAFC;
  }
}

.menu-icon-wrap {
  width: 72rpx;
  height: 72rpx;
  border-radius: 20rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.menu-icon-clock {
  background: linear-gradient(135deg, rgba(79, 110, 247, 0.08), rgba(123, 92, 247, 0.08));
}

.menu-icon-trash {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.06), rgba(245, 158, 11, 0.06));
}

.menu-emoji {
  font-size: 32rpx;
}

.menu-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2rpx;
}

.menu-text {
  font-size: 30rpx;
  font-weight: 600;
  color: $text-primary;
}

.menu-hint {
  font-size: 22rpx;
  color: $text-hint;
}

.menu-arrow {
  font-size: 36rpx;
  color: $text-hint;
  font-weight: 300;
}

.menu-divider {
  height: 1rpx;
  background: $border-color;
  margin: 0 $spacing-lg;
}

/* ==================== 版本信息 ==================== */
.version-info {
  text-align: center;
  padding: $spacing-xl;
  font-size: 24rpx;
  color: $text-hint;
}
</style>