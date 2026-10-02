<template>
  <view class="page-login">
    <!-- 顶部品牌区 -->
    <view class="login-hero">
      <view class="login-hero-bg" />
      <view class="login-hero-content">
        <view class="hero-logo">
          <text class="hero-logo-text">📸</text>
        </view>
        <text class="hero-title">欢迎回来</text>
        <text class="hero-subtitle">选择以下方式登录，开始使用照片处理服务</text>
      </view>
    </view>

    <!-- 登录选项卡片 -->
    <view class="login-options">
      <view class="login-card wechat-card" @click="startWechatLogin">
        <view class="card-icon-wrap wechat-icon">
          <text class="card-emoji">💚</text>
        </view>
        <view class="card-text">
          <text class="card-title">微信扫码登录</text>
          <text class="card-desc">使用微信扫描二维码快速登录</text>
        </view>
        <text class="card-arrow">›</text>
      </view>

      <view class="login-card alipay-card" @click="startAlipayLogin">
        <view class="card-icon-wrap alipay-icon">
          <text class="card-emoji">💙</text>
        </view>
        <view class="card-text">
          <text class="card-title">支付宝登录</text>
          <text class="card-desc">使用支付宝扫码授权登录</text>
        </view>
        <text class="card-arrow">›</text>
      </view>
    </view>

    <!-- 底部提示 -->
    <view class="login-footer">
      <text>登录即表示同意服务条款和隐私政策</text>
    </view>

    <!-- 二维码弹窗 -->
    <u-popup
      :show="showQrcode"
      @close="closeQrcode"
      mode="center"
      :round="24"
    >
      <view class="qrcode-popup">
        <view class="qrcode-popup-header">
          <text class="qrcode-title">
            {{ qrcodeType === 'wechat' ? '微信扫码登录' : '支付宝扫码登录' }}
          </text>
          <text class="qrcode-subtitle">
            请使用{{ qrcodeType === 'wechat' ? '微信' : '支付宝' }}扫描二维码
          </text>
        </view>

        <view class="qrcode-wrap">
          <canvas
            v-if="qrcodeContent"
            :id="'qrcode-canvas'"
            canvas-id="qrcode-canvas"
            class="qrcode-canvas"
            :style="{ width: '400rpx', height: '400rpx' }"
          />
          <view v-else class="qrcode-loading">
            <view class="qrcode-spinner" />
            <text>生成二维码中...</text>
          </view>
        </view>

        <view class="polling-status" v-if="polling">
          <view class="polling-dot" />
          <text>等待扫码确认...</text>
        </view>

        <view class="qrcode-cancel" @click="closeQrcode">
          <text>取消</text>
        </view>
      </view>
    </u-popup>

    <!-- 登录成功跳转 -->
    <view v-if="loginSuccess" class="login-success-overlay">
      <view class="login-success-card">
        <view class="success-icon">✓</view>
        <text class="success-title">登录成功</text>
        <text class="success-desc">正在跳转...</text>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, nextTick, onUnmounted } from 'vue'
import { getWechatWebQrcode, wechatWebCallback, alipayWebQrcode, alipayWebCallback } from '@/api/auth'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

const showQrcode = ref(false)
const qrcodeType = ref('wechat')
const qrcodeContent = ref('')
const polling = ref(false)
const loginSuccess = ref(false)
let pollingTimer = null
let qrcodeState = ''

function generateState() {
  return 'login_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8)
}

function renderQrcode(url) {
  qrcodeContent.value = url
}

async function startWechatLogin() {
  qrcodeType.value = 'wechat'
  try {
    const res = await getWechatWebQrcode()
    if (res.code === 200) {
      qrcodeState = res.data.state || generateState()
      const qrcodeUrl = res.data.qrcode_url || res.data.code_url
      showQrcode.value = true
      renderQrcode(qrcodeUrl)
      startPolling('wechat')
    } else {
      uni.showToast({ title: '获取二维码失败', icon: 'none' })
    }
  } catch {
    uni.showToast({ title: '网络错误', icon: 'none' })
  }
}

async function startAlipayLogin() {
  qrcodeType.value = 'alipay'
  try {
    const res = await alipayWebQrcode()
    if (res.code === 200) {
      qrcodeState = res.data.state || generateState()
      const qrcodeUrl = res.data.qrcode_url || res.data.code_url
      showQrcode.value = true
      renderQrcode(qrcodeUrl)
      startPolling('alipay')
    } else {
      uni.showToast({ title: '获取二维码失败', icon: 'none' })
    }
  } catch {
    uni.showToast({ title: '网络错误', icon: 'none' })
  }
}

function startPolling(type) {
  polling.value = true
  pollingTimer = setInterval(async () => {
    try {
      const res = type === 'wechat'
        ? await wechatWebCallback('polling', qrcodeState)
        : await alipayWebCallback('polling', qrcodeState)

      if (res.code === 200 && res.data?.access_token) {
        stopPolling()
        loginSuccess.value = true
        userStore.setTokens(res.data.access_token, res.data.refresh_token)
        await userStore.fetchProfile()
        await userStore.fetchFreeCount()

        setTimeout(() => {
          uni.switchTab({ url: '/pages/index/index' })
        }, 1000)
      }
    } catch {
      // 继续轮询
    }
  }, 2000)
}

function stopPolling() {
  polling.value = false
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

function closeQrcode() {
  stopPolling()
  showQrcode.value = false
  qrcodeContent.value = ''
}

onUnmounted(() => {
  stopPolling()
})
</script>

<style lang="scss" scoped>
.page-login {
  min-height: 100vh;
  background: $page-bg;
}

/* ==================== Hero 区域 ==================== */
.login-hero {
  position: relative;
  overflow: hidden;
  padding-bottom: $spacing-xl;
}

.login-hero-bg {
  position: absolute;
  top: -80%;
  left: -30%;
  right: -30%;
  bottom: 0;
  background: $brand-gradient;
  border-radius: 0 0 50% 50%;
  transform: scaleX(1.5);
}

.login-hero-content {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: $spacing-2xl * 2;
  padding-bottom: $spacing-xl;
}

.hero-logo {
  width: 120rpx;
  height: 120rpx;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 32rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(10px);
  box-shadow: 0 8rpx 32rpx rgba(0, 0, 0, 0.12);
  margin-bottom: $spacing-md;
}

.hero-logo-text {
  font-size: 56rpx;
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
  text-align: center;
  line-height: 1.6;
  padding: 0 $spacing-xl;
}

/* ==================== 登录选项 ==================== */
.login-options {
  padding: 0 $spacing-md;
  margin-top: -$spacing-md;
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: $spacing-sm;
}

.login-card {
  display: flex;
  align-items: center;
  padding: $spacing-lg;
  background: $card-bg;
  border-radius: $radius-lg;
  gap: $spacing-md;
  box-shadow: $shadow-md;
  transition: all 0.2s;

  &:active {
    transform: scale(0.98);
    box-shadow: $shadow-sm;
  }
}

.card-icon-wrap {
  width: 80rpx;
  height: 80rpx;
  border-radius: 24rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.wechat-icon {
  background: linear-gradient(135deg, rgba(7, 193, 96, 0.12), rgba(7, 193, 96, 0.06));
}

.alipay-icon {
  background: linear-gradient(135deg, rgba(22, 119, 255, 0.12), rgba(22, 119, 255, 0.06));
}

.card-emoji {
  font-size: 36rpx;
}

.card-text {
  flex: 1;
}

.card-title {
  display: block;
  font-size: 30rpx;
  font-weight: 700;
  color: $text-primary;
  margin-bottom: 4rpx;
}

.card-desc {
  font-size: 24rpx;
  color: $text-hint;
}

.card-arrow {
  font-size: 36rpx;
  color: $text-hint;
  font-weight: 300;
}

/* ==================== 底部 ==================== */
.login-footer {
  text-align: center;
  padding: $spacing-xl;
  font-size: 22rpx;
  color: $text-hint;
}

/* ==================== 二维码弹窗 ==================== */
.qrcode-popup {
  padding: $spacing-xl $spacing-lg;
  text-align: center;
  min-width: 520rpx;
}

.qrcode-popup-header {
  margin-bottom: $spacing-lg;
}

.qrcode-title {
  display: block;
  font-size: 34rpx;
  font-weight: 700;
  color: $text-primary;
  margin-bottom: $spacing-xs;
}

.qrcode-subtitle {
  font-size: 24rpx;
  color: $text-hint;
}

.qrcode-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
  margin-bottom: $spacing-md;
  min-height: 400rpx;
}

.qrcode-canvas {
  border: 2rpx solid $border-color;
  border-radius: $radius-md;
}

.qrcode-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $spacing-sm;
  padding: $spacing-xl;
  color: $text-hint;
  font-size: 26rpx;
}

.qrcode-spinner {
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

.polling-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: $spacing-xs;
  font-size: 24rpx;
  color: $brand-primary;
  margin-bottom: $spacing-md;
  font-weight: 500;
}

.polling-dot {
  width: 12rpx;
  height: 12rpx;
  border-radius: 50%;
  background: $brand-primary;
  animation: procPulse 1s ease-in-out infinite;
}

@keyframes procPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(79, 110, 247, 0.4); }
  50% { box-shadow: 0 0 0 8rpx rgba(79, 110, 247, 0); }
}

.qrcode-cancel {
  padding: 20rpx 0;
  font-size: 28rpx;
  color: $text-hint;
  font-weight: 500;
}

/* ==================== 登录成功 ==================== */
.login-success-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 999;
}

.login-success-card {
  background: $card-bg;
  border-radius: $radius-lg;
  padding: $spacing-xl $spacing-2xl;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $spacing-sm;
  box-shadow: $shadow-lg;
}

.success-icon {
  width: 80rpx;
  height: 80rpx;
  border-radius: 50%;
  background: $u-success;
  color: #fff;
  font-size: 40rpx;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.success-title {
  font-size: 32rpx;
  font-weight: 700;
  color: $text-primary;
}

.success-desc {
  font-size: 26rpx;
  color: $text-hint;
}
</style>