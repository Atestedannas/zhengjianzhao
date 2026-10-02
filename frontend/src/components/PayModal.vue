<template>
  <view class="pay-modal-mask" v-if="visible" @click="handleMaskClick">
    <view class="pay-modal" @click.stop>
      <!-- 头部 -->
      <view class="pay-header">
        <text class="pay-title">支付</text>
        <u-icon name="close" size="20" color="#999" @click="handleCancel" />
      </view>

      <!-- 金额 -->
      <view class="pay-amount">
        <text class="amount-symbol">¥</text>
        <text class="amount-value">{{ amount }}</text>
      </view>
      <text class="pay-desc">单次照片处理费用</text>

      <!-- 支付方式选择 -->
      <view class="pay-methods">
        <text class="section-label">选择支付方式</text>
        <view class="method-list">
          <view
            v-for="method in methods"
            :key="method.value"
            class="method-item"
            :class="{ selected: selectedMethod === method.value }"
            @click="selectedMethod = method.value"
          >
            <u-icon :name="method.icon" size="24" color="#333" />
            <text class="method-label">{{ method.label }}</text>
            <u-icon
              v-if="selectedMethod === method.value"
              name="checkbox-mark"
              size="18"
              color="#007AFF"
              class="method-check"
            />
          </view>
        </view>
      </view>

      <!-- 二维码展示区（H5 微信扫码） -->
      <view v-if="showQrcode && qrcodeUrl" class="qrcode-area">
        <text class="qrcode-tip">请使用微信扫描二维码支付</text>
        <image class="qrcode-img" :src="qrcodeUrl" mode="widthFix" />
        <text class="qrcode-waiting">等待支付中...</text>
        <u-loading mode="circle" size="20" />
      </view>

      <!-- 确认支付按钮 -->
      <view class="pay-actions" v-if="!showQrcode">
        <u-button
          type="primary"
          block
          :loading="isPaying"
          :disabled="!selectedMethod"
          @click="handlePay"
        >
          {{ isPaying ? '支付中...' : '确认支付 ¥' + amount }}
        </u-button>
      </view>

      <!-- 支付中状态 -->
      <view v-if="isPaying && !showQrcode" class="paying-status">
        <u-loading mode="circle" />
        <text class="paying-text">正在处理支付，请稍候...</text>
      </view>

      <!-- 取消按钮 -->
      <view class="cancel-btn" v-if="!isPaying || showQrcode">
        <u-button type="default" plain size="small" @click="handleCancel">取消</u-button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, computed } from 'vue'
import { isWechatMini } from '@/utils/platform'
import { getAvailablePayMethods } from '@/utils/platform'
import { usePaymentStore } from '@/stores/payment'

const props = defineProps({
  visible: { type: Boolean, default: false },
  amount: { type: Number, default: 0 },
  orderNo: { type: String, default: '' },
})

const emit = defineEmits(['close', 'paid', 'cancel'])

const paymentStore = usePaymentStore()
const selectedMethod = ref('')
const isPaying = ref(false)
const showQrcode = ref(false)
const qrcodeUrl = ref('')

const methods = computed(() => getAvailablePayMethods())

function handleMaskClick() {
  if (!isPaying.value) {
    emit('cancel')
  }
}

function handleCancel() {
  if (!isPaying.value) {
    paymentStore.closePayModal()
    emit('close')
  }
}

async function handlePay() {
  if (!selectedMethod.value || isPaying.value) return

  isPaying.value = true

  // 小程序微信支付
  // #ifdef MP-WEIXIN
  if (selectedMethod.value === 'wechat_jsapi') {
    const result = await paymentStore.handleWechatPay()
    if (result.success) {
      emit('paid', { orderNo: props.orderNo })
    } else {
      isPaying.value = false
      uni.showToast({ title: result.message || '支付失败', icon: 'none' })
    }
  }
  // #endif

  // #ifdef H5
  if (selectedMethod.value === 'wechat_native') {
    // H5 微信扫码支付
    const res = await paymentStore.createOrder('wechat_native')
    if (res.success && paymentStore.payParams?.codeUrl) {
      showQrcode.value = true
      qrcodeUrl.value = paymentStore.payParams.codeUrl
      paymentStore.startPolling()
    } else {
      isPaying.value = false
      uni.showToast({ title: res.message || '获取二维码失败', icon: 'none' })
    }
  } else if (selectedMethod.value === 'alipay') {
    const result = await paymentStore.handleAlipayPay()
    if (result.success && result.redirected) {
      // 支付宝页面跳转，本窗口开始轮询
      paymentStore.startPolling()
    } else {
      isPaying.value = false
      uni.showToast({ title: result.message || '支付失败', icon: 'none' })
    }
  }
  // #endif
}
</script>

<style lang="scss" scoped>
.pay-modal-mask {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 999;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.pay-modal {
  width: 100%;
  max-width: 500px;
  background: #fff;
  border-radius: 32rpx 32rpx 0 0;
  padding: $spacing-lg $spacing-md;
  padding-bottom: calc($spacing-xl + env(safe-area-inset-bottom, 0));
  animation: slideUp 0.3s ease;
}

@keyframes slideUp {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}

.pay-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: $spacing-md;
}

.pay-title {
  font-size: 34rpx;
  font-weight: 700;
  color: $text-primary;
}

.pay-amount {
  text-align: center;
  margin: $spacing-lg 0 $spacing-xs;
}

.amount-symbol {
  font-size: 36rpx;
  color: $text-primary;
  font-weight: 500;
}

.amount-value {
  font-size: 72rpx;
  font-weight: 700;
  color: $text-primary;
}

.pay-desc {
  display: block;
  text-align: center;
  font-size: 26rpx;
  color: $text-hint;
  margin-bottom: $spacing-lg;
}

.section-label {
  display: block;
  font-size: 26rpx;
  color: $text-secondary;
  margin-bottom: $spacing-sm;
}

.method-list {
  display: flex;
  flex-direction: column;
  gap: $spacing-sm;
}

.method-item {
  display: flex;
  align-items: center;
  gap: $spacing-sm;
  padding: $spacing-md;
  background: #F8F8F8;
  border-radius: $radius-md;
  border: 2rpx solid transparent;
  transition: all 0.2s;

  &.selected {
    border-color: $u-primary;
    background: rgba(0, 122, 255, 0.04);
  }
}

.method-label {
  flex: 1;
  font-size: 28rpx;
  color: $text-primary;
}

.method-check {
  margin-left: auto;
}

.qrcode-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: $spacing-lg 0;
  gap: $spacing-sm;
}

.qrcode-tip {
  font-size: 26rpx;
  color: $text-secondary;
}

.qrcode-img {
  width: 320rpx;
  padding: $spacing-sm;
  background: #fff;
  border: 2rpx solid $border-color;
  border-radius: $radius-md;
}

.qrcode-waiting {
  font-size: 26rpx;
  color: $u-warning;
}

.pay-actions {
  margin-top: $spacing-lg;
}

.paying-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: $spacing-lg;
  gap: $spacing-sm;
}

.paying-text {
  font-size: 26rpx;
  color: $text-secondary;
}

.cancel-btn {
  text-align: center;
  margin-top: $spacing-md;
  display: flex;
  justify-content: center;
}
</style>
