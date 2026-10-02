/**
 * 支付状态管理
 * state: showModal, orderNo, amount, payParams, payMethod, isPaying
 */
import { defineStore } from 'pinia'
import { createPayment, queryOrderStatus } from '@/api/payment'
import { isWechatMini } from '@/utils/platform'

export const usePaymentStore = defineStore('payment', {
  state: () => ({
    showModal: false,
    orderNo: '',
    amount: 0,
    payParams: null,
    payMethod: '',
    isPaying: false,
    pollingTimer: null,
  }),

  actions: {
    /**
     * 打开支付弹窗
     */
    openPayModal({ orderNo, amount }) {
      this.showModal = true
      this.orderNo = orderNo
      this.amount = amount
      this.payParams = null
      this.payMethod = ''
      this.isPaying = false
    },

    /**
     * 关闭支付弹窗
     */
    closePayModal() {
      this.showModal = false
      this.isPaying = false
      this.stopPolling()
    },

    /**
     * 创建支付订单
     */
    async createOrder(payMethod) {
      this.payMethod = payMethod
      this.isPaying = true
      try {
        const res = await createPayment(payMethod)
        if (res.code === 200) {
          this.payParams = res.data.pay_params
          this.orderNo = res.data.order_no
          return { success: true, data: res.data }
        }
        this.isPaying = false
        return { success: false, message: res.message || '创建订单失败' }
      } catch (e) {
        this.isPaying = false
        return { success: false, message: e.message }
      }
    },

    /**
     * 处理微信支付（小程序或 H5）
     */
    async handleWechatPay() {
      if (!this.payParams) {
        const res = await this.createOrder(isWechatMini() ? 'wechat_jsapi' : 'wechat_native')
        if (!res.success) return res
      }

      // #ifdef MP-WEIXIN
      // 小程序：调起微信支付
      return new Promise((resolve) => {
        uni.requestPayment({
          provider: 'wxpay',
          timeStamp: String(this.payParams.timeStamp),
          nonceStr: this.payParams.nonceStr,
          package: this.payParams.package,
          signType: this.payParams.signType || 'RSA',
          paySign: this.payParams.paySign,
          success: () => {
            this.isPaying = false
            this.showModal = false
            resolve({ success: true })
          },
          fail: (err) => {
            this.isPaying = false
            resolve({ success: false, message: err.errMsg || '支付取消' })
          },
        })
      })
      // #endif

      // #ifdef H5
      // H5 微信扫码：展示二维码，启动轮询
      if (this.payParams && this.payParams.codeUrl) {
        return this.payParams
      }
      return { success: false, message: '获取二维码失败' }
      // #endif
    },

    /**
     * 处理支付宝支付
     */
    async handleAlipayPay() {
      // #ifdef H5
      if (!this.payParams) {
        const res = await this.createOrder('alipay')
        if (!res.success) return res
      }
      // 跳转到支付宝收银台
      if (this.payParams && this.payParams.payUrl) {
        window.open(this.payParams.payUrl, '_blank')
        // 开始轮询订单状态
        this.startPolling()
        return { success: true, redirected: true }
      }
      // #endif
      return { success: false, message: '当前环境不支持支付宝支付' }
    },

    /**
     * H5 扫码支付：启动轮询订单状态
     */
    startPolling() {
      this.stopPolling()
      this.pollingTimer = setInterval(async () => {
        try {
          const res = await queryOrderStatus(this.orderNo)
          if (res.code === 200 && res.data.status === 'paid') {
            this.stopPolling()
            this.isPaying = false
            this.showModal = false
            uni.showToast({ title: '支付成功', icon: 'success' })
          }
        } catch {
          // 继续轮询
        }
      }, 2000)
    },

    /**
     * 停止轮询
     */
    stopPolling() {
      if (this.pollingTimer) {
        clearInterval(this.pollingTimer)
        this.pollingTimer = null
      }
    },

    /**
     * 查询当前订单状态
     */
    async queryStatus() {
      if (!this.orderNo) return null
      try {
        const res = await queryOrderStatus(this.orderNo)
        return res.data
      } catch {
        return null
      }
    },
  },
})
