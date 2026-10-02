/**
 * Token 存储/读取/清除工具
 */

const KEY_ACCESS_TOKEN = 'access_token'
const KEY_REFRESH_TOKEN = 'refresh_token'

export function getAccessToken() {
  return uni.getStorageSync(KEY_ACCESS_TOKEN) || ''
}

export function setAccessToken(token) {
  uni.setStorageSync(KEY_ACCESS_TOKEN, token)
}

export function getRefreshToken() {
  return uni.getStorageSync(KEY_REFRESH_TOKEN) || ''
}

export function setRefreshToken(token) {
  uni.setStorageSync(KEY_REFRESH_TOKEN, token)
}

export function clearTokens() {
  uni.removeStorageSync(KEY_ACCESS_TOKEN)
  uni.removeStorageSync(KEY_REFRESH_TOKEN)
}

export function hasToken() {
  return !!getAccessToken()
}
