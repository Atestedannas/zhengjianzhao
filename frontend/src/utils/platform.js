/**
 * 平台判断与支付方式适配工具
 */

/**
 * 是否为微信小程序环境
 */
export function isWechatMini() {
  // #ifdef MP-WEIXIN
  return true
  // #endif
  return false
}

/**
 * 是否为 H5 环境
 */
export function isH5() {
  // #ifdef H5
  return true
  // #endif
  return false
}

/**
 * 获取当前支持的支付方式列表
 * 小程序：仅微信支付
 * H5：微信扫码 + 支付宝
 */
export function getAvailablePayMethods() {
  if (isWechatMini()) {
    return [
      { value: 'wechat_jsapi', label: '微信支付', icon: 'wechat-fill' },
    ]
  }
  return [
    { value: 'wechat_native', label: '微信扫码支付', icon: 'wechat-fill' },
    { value: 'alipay', label: '支付宝支付', icon: 'zhifubao-circle-fill' },
  ]
}

/**
 * 判断是否支持保存到相册
 */
export function canSaveToAlbum() {
  // #ifdef MP-WEIXIN
  return true
  // #endif
  // #ifdef H5
  return false
  // #endif
  return false
}
