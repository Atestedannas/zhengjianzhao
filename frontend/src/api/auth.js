/**
 * 鉴权 API
 */
import { request } from './request'

export function wechatLogin(code) {
  return request({
    url: '/api/v1/auth/wechat-login',
    method: 'POST',
    data: { code },
  })
}

export function getWechatWebQrcode() {
  return request({
    url: '/api/v1/auth/wechat-web-qrcode',
    method: 'POST',
  })
}

export function wechatWebCallback(code, state) {
  return request({
    url: '/api/v1/auth/wechat-web-callback',
    method: 'POST',
    data: { code, state },
  })
}

export function alipayWebQrcode() {
  return request({
    url: '/api/v1/auth/alipay-web-qrcode',
    method: 'POST',
  })
}

export function alipayWebCallback(code, state) {
  return request({
    url: '/api/v1/auth/alipay-web-callback',
    method: 'POST',
    data: { code, state },
  })
}

export function refreshToken(refreshToken) {
  return request({
    url: '/api/v1/auth/refresh',
    method: 'POST',
    data: { refresh_token: refreshToken },
  })
}
