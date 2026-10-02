/**
 * 统一请求封装
 * 功能：BASE_URL 配置、Token 注入、401 自动刷新、402 支付拦截、统一错误处理
 */

// 根据环境自动切换 API 地址
// 开发环境：使用相对路径，由 vite proxy 转发
// 生产环境：替换为实际后端域名
const BASE_URL = (() => {
  // #ifdef H5
  if (process.env.NODE_ENV === 'production') {
    return 'https://your-domain.com'  // 替换为实际生产域名
  }
  // #endif
  return ''  // 开发环境使用相对路径，走 vite.config.js proxy
})()

let isRefreshing = false
let refreshSubscribers = []

function onTokenRefreshed(newToken) {
  refreshSubscribers.forEach((cb) => cb(newToken))
  refreshSubscribers = []
}

function addRefreshSubscriber(cb) {
  refreshSubscribers.push(cb)
}

async function refreshAccessToken() {
  const refreshToken = uni.getStorageSync('refresh_token')
  if (!refreshToken) return false

  try {
    const res = await uni.request({
      url: BASE_URL + '/api/v1/auth/refresh',
      method: 'POST',
      data: { refresh_token: refreshToken },
      timeout: 15000,
    })

    if (res.statusCode === 200 && res.data?.code === 200) {
      const { access_token, refresh_token: newRefresh } = res.data.data
      uni.setStorageSync('access_token', access_token)
      if (newRefresh) uni.setStorageSync('refresh_token', newRefresh)
      return access_token
    }
  } catch {
    // 刷新失败
  }
  return null
}

export async function request(options) {
  const url = /^https?:\/\//.test(options.url) ? options.url : BASE_URL + options.url

  const header = { ...options.header }
  const token = uni.getStorageSync('access_token')
  if (token) {
    header['Authorization'] = `Bearer ${token}`
  }

  // 上传文件特殊处理
  if (options.method === 'UPLOAD') {
    return handleUpload(options, header)
  }

  try {
    const res = await uni.request({
      url,
      method: options.method || 'GET',
      data: options.data,
      header,
      timeout: options.timeout || 30000,
    })

    // 401: Token 过期，尝试刷新
    if (res.statusCode === 401) {
      if (!isRefreshing) {
        isRefreshing = true
        const newToken = await refreshAccessToken()
        isRefreshing = false

        if (newToken) {
          onTokenRefreshed(newToken)
          // 用新 token 重试
          header['Authorization'] = `Bearer ${newToken}`
          const retryRes = await uni.request({
            url,
            method: options.method || 'GET',
            data: options.data,
            header,
            timeout: options.timeout || 30000,
          })
          return handleResponse(retryRes)
        } else {
          // 刷新失败，通知登出
          refreshSubscribers = []
          const userStore = (await import('@/stores/user')).useUserStore
          userStore().logout()
          uni.showToast({ title: '登录已过期，请重新登录', icon: 'none' })
          throw new Error('登录已过期')
        }
      } else {
        // 已有刷新进行中，排队等待
        return new Promise((resolve) => {
          addRefreshSubscriber(async (newToken) => {
            header['Authorization'] = `Bearer ${newToken}`
            const retryRes = await uni.request({
              url,
              method: options.method || 'GET',
              data: options.data,
              header,
              timeout: options.timeout || 30000,
            })
            resolve(handleResponse(retryRes))
          })
        })
      }
    }

    return handleResponse(res)
  } catch (err) {
    return handleNetworkError(err)
  }
}

function handleResponse(res) {
  const body = res.data

  // 402: 需要付费
  if (res.statusCode === 402 || body?.code === 402) {
    return body
  }

  // HTTP 200 但业务失败
  if (body?.code && body.code !== 200 && body.code !== 402) {
    uni.showToast({ title: body.message || '请求失败', icon: 'none' })
    throw new Error(body.message || 'Request failed')
  }

  return body
}

async function handleUpload(options, header) {
  const url = /^https?:\/\//.test(options.url) ? options.url : BASE_URL + options.url

  return new Promise((resolve, reject) => {
    const uploadTask = uni.uploadFile({
      url,
      filePath: options.filePath,
      name: options.name || 'file',
      formData: options.data || {},
      header,
      timeout: options.timeout || 60000,
      success: (res) => {
        if (res.statusCode === 200) {
          try {
            const data = JSON.parse(res.data)
            resolve(data)
          } catch {
            resolve(res.data)
          }
        } else if (res.statusCode === 401) {
          reject(new Error('登录已过期'))
        } else {
          reject(new Error(`上传失败: ${res.statusCode}`))
        }
      },
      fail: (err) => reject(err),
    })

    if (options.onProgress) {
      uploadTask.onProgressUpdate((progress) => {
        options.onProgress(progress.progress)
      })
    }
  })
}

function handleNetworkError(err) {
  const msg = err.errMsg || err.message || '网络错误'
  if (msg.includes('timeout')) {
    uni.showToast({ title: '请求超时，请重试', icon: 'none' })
  } else if (msg.includes('fail')) {
    uni.showToast({ title: '网络异常，请检查网络', icon: 'none' })
  } else {
    uni.showToast({ title: msg, icon: 'none' })
  }
  throw err
}

export default request
