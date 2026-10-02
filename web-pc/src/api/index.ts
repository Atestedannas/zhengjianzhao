import axios from 'axios'
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

const instance: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
})

/** 受保护二进制资源的请求配置：需要读取响应头（Content-Disposition）时置 true */
type RawResponseConfig = AxiosRequestConfig & { returnFullResponse?: boolean }

// ==================== 登录失效统一处理 ====================
// API 层不在 Vue 组件上下文里，拿不到 router / pinia store。
// 由 main.ts 通过 setUnauthorizedHandler 注入处理逻辑，
// 这样既不会跳错路径，也不会因公开页面 401 造成刷新死循环。
type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(fn: UnauthorizedHandler | null) {
  unauthorizedHandler = fn
}

const TOKEN_KEY = 'web_token'
const REFRESH_KEY = 'web_refresh_token'
const EXPIRES_KEY = 'web_expires_at'

/** 清除本地登录凭据 */
function clearCredentials() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(EXPIRES_KEY)
  localStorage.removeItem('web_nickname')
  localStorage.removeItem('web_avatar')
}

// ==================== Token 刷新队列 ====================
let isRefreshing = false
let pendingRequests: Array<{
  resolve: (token: string) => void
  reject: (err: Error) => void
}> = []

function subscribePendingRequests(token: string): Promise<string> {
  return new Promise((resolve, reject) => {
    pendingRequests.push({ resolve, reject })
  })
}

function onRefreshSuccess(newToken: string) {
  pendingRequests.forEach(({ resolve }) => resolve(newToken))
  pendingRequests = []
}

function onRefreshFailure(err: Error) {
  pendingRequests.forEach(({ reject }) => reject(err))
  pendingRequests = []
}

// ==================== 请求拦截器 ====================
instance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('web_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ==================== 响应拦截器 ====================
instance.interceptors.response.use(
  (response: AxiosResponse) => {
    // 二进制流：默认只返回 Blob；需要读 Content-Disposition 时返回整个响应
    if (response.config.responseType === 'blob') {
      return (response.config as RawResponseConfig).returnFullResponse ? response : response.data
    }
    const data = response.data
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
      const url: string = error.config?.url || ''
      if (url.includes('/login') || url.includes('/web-login')) {
        const msg = error.response?.data?.detail || '登录失败'
        ElMessage.error(msg)
        return Promise.reject(error)
      }
      return handle401(error.config)
    }
    if (status === 402) {
      const msg = error.response?.data?.message || '需要支付'
      ElMessage.warning(msg)
      return Promise.reject({ ...error, needPay: true, data: error.response?.data?.data })
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
  // 本地完全没有凭据 → 这是"未登录"，不是"登录过期"。
  // 静默拒绝即可，交给页面/路由守卫决定是否引导登录；
  // 否则首页等公开页面会误触发登出并跳转，导致每次打开都被弹走。
  if (!localStorage.getItem(TOKEN_KEY) && !localStorage.getItem(REFRESH_KEY)) {
    return Promise.reject(new Error('未登录'))
  }

  if (isRefreshing) {
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
    const refreshToken = localStorage.getItem(REFRESH_KEY)
    if (!refreshToken) {
      throw new Error('无 refresh_token')
    }

    const response = await axios.post('/api/v1/auth/refresh', {
      refresh_token: refreshToken,
    })

    if (response.data?.code === 200) {
      const newToken = response.data.data.access_token
      const newRefreshToken = response.data.data.refresh_token
      const expiresIn = response.data.data.expires_in

      localStorage.setItem(TOKEN_KEY, newToken)
      localStorage.setItem(REFRESH_KEY, newRefreshToken)
      localStorage.setItem(EXPIRES_KEY, String(Date.now() + expiresIn * 1000))

      onRefreshSuccess(newToken)
      failedConfig.headers.Authorization = `Bearer ${newToken}`
      return instance(failedConfig)
    }

    throw new Error('刷新失败')
  } catch (refreshErr) {
    onRefreshFailure(refreshErr as Error)
    doLogout()
    return Promise.reject(refreshErr)
  } finally {
    isRefreshing = false
  }
}

/** 登录确实失效：清凭据 → 通知应用层（原地清理并回到首页），不再硬跳转 */
function doLogout() {
  clearCredentials()
  ElMessage.error('登录已过期，请重新登录')

  if (unauthorizedHandler) {
    unauthorizedHandler()
    return
  }

  // 兜底：没有注入处理器时，按 Vite base 回到应用根路径（/web-pc/）。
  // 已经在根路径时不再跳转，避免无限刷新。
  const base = import.meta.env.BASE_URL || '/'
  const path = window.location.pathname
  if (path !== base && path !== base.replace(/\/$/, '')) {
    window.location.assign(base)
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

export async function del<T = any>(url: string): Promise<T> {
  return instance.delete(url) as Promise<T>
}

// ==================== 受保护的二进制资源 ====================
// /process/{id}/preview、/process/{id}/download 都要 Bearer 鉴权，而 <img src>、
// <a href> 发起的请求带不上 Authorization 头，直接放 URL 里必然 401。
// 这里统一用同一个 axios 实例（自动带 token）取回 Blob，再由页面转成 object URL。

/** 后端给的 /api/v1/xxx 形式路径 → 与 baseURL 拼接用的相对路径 */
function toApiPath(url: string): string {
  const prefix = '/api/v1'
  return url.startsWith(`${prefix}/`) ? url.slice(prefix.length) : url
}

/** 解析 Content-Disposition 里的文件名（含 RFC 5987 的 filename*） */
function parseFilename(disposition?: string): string | null {
  if (!disposition) return null
  const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(disposition)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''))
    } catch {
      // 编码异常时退回普通 filename
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(disposition)
  return plain?.[1]?.trim() ?? null
}

async function requestProtectedBlob(url: string): Promise<AxiosResponse<Blob>> {
  return (await instance.get(toApiPath(url), {
    responseType: 'blob',
    returnFullResponse: true,
  } as RawResponseConfig)) as unknown as AxiosResponse<Blob>
}

/** 取受保护的图片/文件内容 */
export async function getProtectedBlob(url: string): Promise<Blob> {
  return (await requestProtectedBlob(url)).data
}

/** 取受保护的下载文件，并带上后端指定的文件名 */
export async function getProtectedFile(url: string): Promise<{ blob: Blob; filename: string | null }> {
  const response = await requestProtectedBlob(url)
  return {
    blob: response.data,
    filename: parseFilename(response.headers?.['content-disposition'] as string | undefined),
  }
}

// ==================== 业务 API ====================

// --- 认证相关 ---
export interface WebLoginParams {
  password: string
}

export interface TokenResult {
  access_token: string
  refresh_token: string
  expires_in: number
  is_new_user: boolean
}

export function webLogin(params: WebLoginParams): Promise<TokenResult> {
  return post('/auth/web-login', params)
}

// --- 扫码登录（微信 / 支付宝）---
export type OAuthProvider = 'wechat' | 'alipay'

export interface OAuthQrcodeResult {
  /** 平台授权地址：微信用于 iframe 内嵌二维码，支付宝用于整页跳转 */
  auth_url: string
  /** 防 CSRF 的 state，回调时原样带回 */
  state: string
}

export function getOAuthAuthUrl(provider: OAuthProvider): Promise<OAuthQrcodeResult> {
  return post(provider === 'wechat' ? '/auth/wechat-web-qrcode' : '/auth/alipay-web-qrcode', {})
}

export function oauthWebLogin(
  provider: OAuthProvider,
  code: string,
  state: string,
): Promise<TokenResult> {
  return post(provider === 'wechat' ? '/auth/wechat-web-callback' : '/auth/alipay-web-callback', {
    code,
    state,
  })
}

export function refreshTokenApi(refreshToken: string): Promise<TokenResult> {
  return post('/auth/refresh', { refresh_token: refreshToken })
}

export function logoutApi(): Promise<void> {
  return post('/user/logout')
}

// --- 用户相关 ---
export interface UserProfile {
  id: number
  nickname: string
  avatar_url: string
  free_count: number
  balance: number
  total_spent: number
  created_at: string | null
  last_login_at: string | null
}

export function getUserProfile(): Promise<UserProfile> {
  return get('/user/profile')
}

export function getFreeCount(): Promise<{ free_count: number }> {
  return get('/user/free-count')
}

// --- 模板相关 ---
export interface TemplateItem {
  id: number
  name: string
  width_px: number
  height_px: number
  dpi: number
  min_kb: number
  max_kb: number
  allowed_bg_colors: string[]
  output_format: string
  physical_size_mm: string
  remark: string
}

export function getTemplates(): Promise<{ items: TemplateItem[]; count: number }> {
  return get('/templates/')
}

export function getTemplateDetail(id: number): Promise<TemplateItem> {
  return get(`/templates/${id}`)
}

// --- 照片处理相关 ---
export interface ProcessParams {
  template_id?: number
  width?: number
  height?: number
  resize_mode?: string
  upscale?: boolean
  dpi?: number
  min_kb?: number
  max_kb?: number
  bg_color?: string
  output_format?: string
  beautify_level?: number
  beautify_smooth?: boolean
  beautify_brighten?: boolean
  beautify_blemish?: boolean
  id_photo_align?: boolean
  gender?: string
}

export interface ProcessResult {
  record_id: number
  result_url: string
  download_url: string
  file_size_kb: number
  pixels: string
  dpi: number
  output_format: string
  mime_type: string
  warnings: string[]
  faces_detected: number
  processing_time_ms: number
  free_used: boolean
  remaining_free_count: number
}

export function processPhoto(file: File, params: ProcessParams): Promise<ProcessResult> {
  const formData = new FormData()
  formData.append('file', file)
  if (params.template_id) formData.append('template_id', String(params.template_id))
  if (params.width) formData.append('width', String(params.width))
  if (params.height) formData.append('height', String(params.height))
  if (params.resize_mode) formData.append('resize_mode', params.resize_mode)
  if (params.upscale !== undefined) formData.append('upscale', String(params.upscale))
  if (params.dpi) formData.append('dpi', String(params.dpi))
  if (params.min_kb) formData.append('min_kb', String(params.min_kb))
  if (params.max_kb) formData.append('max_kb', String(params.max_kb))
  if (params.bg_color) formData.append('bg_color', params.bg_color)
  if (params.output_format) formData.append('output_format', params.output_format)
  if (params.beautify_level !== undefined) formData.append('beautify_level', String(params.beautify_level))
  if (params.beautify_smooth !== undefined) formData.append('beautify_smooth', String(params.beautify_smooth))
  if (params.beautify_brighten !== undefined) formData.append('beautify_brighten', String(params.beautify_brighten))
  if (params.beautify_blemish !== undefined) formData.append('beautify_blemish', String(params.beautify_blemish))
  if (params.id_photo_align !== undefined) formData.append('id_photo_align', String(params.id_photo_align))
  if (params.gender) formData.append('gender', params.gender)

  return post('/process/', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export interface SizePreset {
  name: string
  width: number
  height: number
}

export function getSizePresets(): Promise<{ presets: SizePreset[]; count: number }> {
  return get('/process/presets')
}

export interface OutputFormat {
  format: string
  ext: string
  mime: string
  description: string
}

export function getOutputFormats(): Promise<{ formats: OutputFormat[] }> {
  return get('/process/formats')
}

export interface BgColor {
  name: string
  rgb: number[]
}

export function getBgColors(): Promise<{ colors: BgColor[] }> {
  return get('/process/bg-colors')
}

export function getPreviewUrl(recordId: number): string {
  return `/api/v1/process/${recordId}/preview`
}

export function getDownloadUrl(recordId: number): string {
  return `/api/v1/process/${recordId}/download`
}

// --- 历史记录 ---
export interface HistoryItem {
  id: number
  template_id: number | null
  template_name: string | null
  original_size: number
  result_size: number
  result_pixels: string
  bg_color: string
  status: string
  processing_time_ms: number
  created_at: string | null
  has_preview: boolean
  result_url: string | null
  thumb_url: string | null
}

export function getHistory(): Promise<{ items: HistoryItem[]; count: number }> {
  return get('/process/history')
}

export default instance
