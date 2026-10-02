import axios from 'axios'
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { secureGet, secureRemove } from '@/utils/crypto'

const instance: AxiosInstance = axios.create({
  baseURL: '/api/v1/admin',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// ==================== Token 刷新队列 ====================
let isRefreshing = false
let pendingRequests: Array<{
  resolve: (token: string) => void
  reject: (err: Error) => void
}> = []

/** 将请求加入等待队列，刷新完成后统一重试 */
function subscribePendingRequests(token: string): Promise<string> {
  return new Promise((resolve, reject) => {
    pendingRequests.push({ resolve, reject })
  })
}

/** 刷新完成后，用新 token 重试所有等待中的请求 */
function onRefreshSuccess(newToken: string) {
  pendingRequests.forEach(({ resolve }) => resolve(newToken))
  pendingRequests = []
}

/** 刷新失败，拒绝所有等待中的请求 */
function onRefreshFailure(err: Error) {
  pendingRequests.forEach(({ reject }) => reject(err))
  pendingRequests = []
}

// ==================== 请求拦截器 ====================
instance.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const token = await secureGet('admin_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ==================== 响应拦截器 ====================
instance.interceptors.response.use(
  (response: AxiosResponse) => {
    // 二进制流（blob / 文件下载）直接返回原始数据
    if (response.config.responseType === 'blob') {
      return response.data
    }
    const data = response.data
    // 非统一 JSON 包裹（如纯文本/CSV/空响应）原样返回
    if (typeof data !== 'object' || data === null || data.code === undefined) {
      return data
    }
    if (data.code === 200) {
      return data.data
    }
    if (data.code === 401) {
      return handle401(response.config)
    }
    ElMessage.error(data.message || '请求失败')
    return Promise.reject(new Error(data.message || '请求失败'))
  },
  async (error) => {
    const status = error.response?.status
    if (status === 401) {
      // 登录接口的 401 是账号密码错误，不触发 token 刷新
      const url: string = error.config?.url || ''
      if (url.includes('/login')) {
        const msg = error.response?.data?.detail || '用户名或密码错误'
        ElMessage.error(msg)
        return Promise.reject(error)
      }
      return handle401(error.config)
    }
    const errMsg =
      error.response?.data?.detail ||
      error.response?.data?.message ||
      error.message ||
      '网络错误'
    ElMessage.error(errMsg)
    return Promise.reject(error)
  },
)

/** 处理 401：尝试刷新 Token 并重试 */
async function handle401(failedConfig: InternalAxiosRequestConfig): Promise<any> {
  if (isRefreshing) {
    // 已有一个刷新请求在进行中，当前请求排队等待
    try {
      const newToken = await subscribePendingRequests('')
      failedConfig.headers.Authorization = `Bearer ${newToken}`
      return instance(failedConfig)
    } catch {
      return Promise.reject(new Error('Token 刷新失败'))
    }
  }

  isRefreshing = true

  try {
    const refreshTokenVal = await secureGet('admin_refresh_token')
    if (!refreshTokenVal) {
      throw new Error('无 refresh_token')
    }

    // 直接调用刷新接口（不走拦截器，避免循环）
    const response = await axios.post('/api/v1/admin/refresh', {
      refresh_token: refreshTokenVal,
    })

    if (response.data?.code === 200) {
      const newToken = response.data.data.access_token
      const newRefreshToken = response.data.data.refresh_token
      const expiresIn = response.data.data.expires_in

      await import('@/utils/crypto').then(({ secureSet }) =>
        Promise.all([
          secureSet('admin_token', newToken),
          // 后端每次刷新都会轮换 refresh_token 并黑名单旧 token，必须同步更新
          secureSet('admin_refresh_token', newRefreshToken),
          secureSet('admin_expires_at', String(Date.now() + expiresIn * 1000)),
        ]),
      )

      onRefreshSuccess(newToken)
      failedConfig.headers.Authorization = `Bearer ${newToken}`
      return instance(failedConfig)
    }

    throw new Error('刷新失败')
  } catch (refreshErr) {
    onRefreshFailure(refreshErr as Error)
    await doLogout()
    return Promise.reject(refreshErr)
  } finally {
    isRefreshing = false
  }
}

/** 清除状态并跳转登录页 */
async function doLogout() {
  ElMessage.error('登录已过期，请重新登录')
  const keys = ['admin_token', 'admin_refresh_token', 'admin_username', 'admin_role', 'admin_expires_at']
  keys.forEach((k) => secureRemove(k))

  // 使用 router 跳转（避免硬刷新）
  try {
    const { useRouter } = await import('vue-router')
    const router = useRouter()
    router.push('/login')
  } catch {
    window.location.href = '/login'
  }
}

// ==================== 封装方法 ====================
export async function get<T = any>(url: string, params?: any, config?: AxiosRequestConfig): Promise<T> {
  return instance.get(url, { ...config, params }) as Promise<T>
}

export async function post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return instance.post(url, data, config) as Promise<T>
}

export async function put<T = any>(url: string, data?: any): Promise<T> {
  return instance.put(url, data) as Promise<T>
}

export async function patch<T = any>(url: string, data?: any): Promise<T> {
  return instance.patch(url, data) as Promise<T>
}

export async function del<T = any>(url: string): Promise<T> {
  return instance.delete(url) as Promise<T>
}

export default instance