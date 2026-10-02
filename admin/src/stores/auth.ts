import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { adminLogin, adminLogout, refreshToken } from '@/api/auth'
import type { LoginParams } from '@/api/auth'
import { secureSet, secureGet, secureRemove } from '@/utils/crypto'

const TOKEN_KEY = 'admin_token'
const REFRESH_KEY = 'admin_refresh_token'
const USERNAME_KEY = 'admin_username'
const ROLE_KEY = 'admin_role'
const EXPIRES_KEY = 'admin_expires_at'

export const useAuthStore = defineStore('auth', () => {
  const token = ref('')
  const refreshTokenVal = ref('')
  const username = ref('')
  const role = ref<'super_admin' | 'normal_admin'>('normal_admin')
  const expiresAt = ref(0) // 过期时间戳 (ms)

  const isLogin = computed(() => !!token.value)
  const isSuperAdmin = computed(() => role.value === 'super_admin')
  const isTokenExpiring = computed(() => {
    if (!expiresAt.value) return false
    // 提前 60 秒刷新
    return Date.now() > expiresAt.value - 60_000
  })

  /** 从 localStorage 恢复登录状态（异步解密） */
  async function restoreLogin() {
    try {
      const [savedToken, savedRefresh, savedUsername, savedRole, savedExpires] =
        await Promise.all([
          secureGet(TOKEN_KEY),
          secureGet(REFRESH_KEY),
          secureGet(USERNAME_KEY),
          secureGet(ROLE_KEY),
          secureGet(EXPIRES_KEY),
        ])

      if (savedToken) {
        token.value = savedToken
        refreshTokenVal.value = savedRefresh || ''
        username.value = savedUsername || ''
        role.value =
          (savedRole as 'super_admin' | 'normal_admin') || 'normal_admin'
        expiresAt.value = savedExpires ? Number(savedExpires) : 0

        // 如果 token 已过期，尝试刷新
        if (isTokenExpiring.value && refreshTokenVal.value) {
          await refreshAccessToken()
        }
      }
    } catch {
      // 恢复失败，清除状态
      clearState()
    }
  }

  /** 登录：调用 API 并加密存储 */
  async function login(params: LoginParams) {
    const result = await adminLogin(params)
    token.value = result.access_token
    refreshTokenVal.value = result.refresh_token
    username.value = result.username
    role.value = result.role
    expiresAt.value = Date.now() + result.expires_in * 1000

    await Promise.all([
      secureSet(TOKEN_KEY, result.access_token),
      secureSet(REFRESH_KEY, result.refresh_token),
      secureSet(USERNAME_KEY, result.username),
      secureSet(ROLE_KEY, result.role),
      secureSet(EXPIRES_KEY, String(expiresAt.value)),
    ])
  }

  /** 使用 refresh_token 刷新 access_token */
  async function refreshAccessToken(): Promise<boolean> {
    if (!refreshTokenVal.value) return false
    try {
      const result = await refreshToken(refreshTokenVal.value)
      token.value = result.access_token
      // 后端每次刷新会轮换 refresh_token 并黑名单旧 token，需同步更新
      if (result.refresh_token) {
        refreshTokenVal.value = result.refresh_token
      }
      expiresAt.value = Date.now() + result.expires_in * 1000

      await Promise.all([
        secureSet(TOKEN_KEY, result.access_token),
        secureSet(REFRESH_KEY, refreshTokenVal.value),
        secureSet(EXPIRES_KEY, String(expiresAt.value)),
      ])
      return true
    } catch {
      // 刷新失败，清除状态
      await clearState()
      return false
    }
  }

  /** 登出：调用 API 并清除加密存储 */
  async function logout() {
    try {
      await adminLogout()
    } catch {
      // ignore logout errors
    }
    await clearState()
  }

  /** 清除所有状态 */
  async function clearState() {
    token.value = ''
    refreshTokenVal.value = ''
    username.value = ''
    role.value = 'normal_admin'
    expiresAt.value = 0

    secureRemove(TOKEN_KEY)
    secureRemove(REFRESH_KEY)
    secureRemove(USERNAME_KEY)
    secureRemove(ROLE_KEY)
    secureRemove(EXPIRES_KEY)
  }

  return {
    token,
    refreshTokenVal,
    username,
    role,
    expiresAt,
    isLogin,
    isSuperAdmin,
    isTokenExpiring,
    restoreLogin,
    login,
    refreshAccessToken,
    logout,
    clearState,
  }
})