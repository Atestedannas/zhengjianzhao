import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { webLogin, oauthWebLogin, refreshTokenApi, getUserProfile, logoutApi, type UserProfile, type TokenResult, type OAuthProvider } from '@/api'

const TOKEN_KEY = 'web_token'
const REFRESH_KEY = 'web_refresh_token'
const EXPIRES_KEY = 'web_expires_at'
const NICKNAME_KEY = 'web_nickname'
const AVATAR_KEY = 'web_avatar'

export const useAuthStore = defineStore('auth', () => {
  // ===== 状态 =====
  const token = ref('')
  const refreshTokenVal = ref('')
  const expiresAt = ref(0)
  const freeCount = ref(0)
  const userInfo = ref<Partial<UserProfile>>({})

  // ===== 计算属性 =====
  const isLogin = computed(() => !!token.value)

  const isTokenExpiring = computed(() => {
    if (!expiresAt.value) return false
    // 提前 60 秒刷新
    return Date.now() > expiresAt.value - 60_000
  })

  /** 免费次数展示文案：-1 是管理员设置的「无限次数」，不能显示成 -1 次 */
  const freeCountText = computed(() => (freeCount.value === -1 ? '∞' : String(freeCount.value)))

  // ===== 方法 =====

  /** 从 localStorage 恢复登录状态 */
  function restoreLogin() {
    try {
      const savedToken = localStorage.getItem(TOKEN_KEY)
      const savedRefresh = localStorage.getItem(REFRESH_KEY)
      const savedExpires = localStorage.getItem(EXPIRES_KEY)
      const savedNickname = localStorage.getItem(NICKNAME_KEY)
      const savedAvatar = localStorage.getItem(AVATAR_KEY)

      if (savedToken) {
        token.value = savedToken
        refreshTokenVal.value = savedRefresh || ''
        expiresAt.value = savedExpires ? Number(savedExpires) : 0
        userInfo.value.nickname = savedNickname || ''
        userInfo.value.avatar_url = savedAvatar || ''

        // 如果 token 即将过期，尝试刷新
        if (isTokenExpiring.value && refreshTokenVal.value) {
          refreshAccessToken()
        }

        // 加载用户信息
        loadUserProfile()
      }
    } catch {
      clearState()
    }
  }

  /** 加载用户信息 */
  async function loadUserProfile() {
    if (!token.value) return
    try {
      const profile = await getUserProfile()
      userInfo.value = profile
      freeCount.value = profile.free_count
    } catch {
      // 忽略错误
    }
  }

  /** 登录 */
  async function login(password: string) {
    await applyTokenResult(await webLogin({ password }))
  }

  /** 落盘登录结果并拉取用户信息（密码登录 / 扫码登录共用） */
  async function applyTokenResult(result: TokenResult) {
    token.value = result.access_token
    refreshTokenVal.value = result.refresh_token
    expiresAt.value = Date.now() + result.expires_in * 1000

    localStorage.setItem(TOKEN_KEY, result.access_token)
    localStorage.setItem(REFRESH_KEY, result.refresh_token)
    localStorage.setItem(EXPIRES_KEY, String(expiresAt.value))

    // 加载用户信息
    await loadUserProfile()

    if (userInfo.value.nickname) {
      localStorage.setItem(NICKNAME_KEY, userInfo.value.nickname)
    }
    if (userInfo.value.avatar_url) {
      localStorage.setItem(AVATAR_KEY, userInfo.value.avatar_url)
    }
  }

  /** 扫码登录回调：用授权 code 换 token */
  async function loginWithOAuth(provider: OAuthProvider, code: string, state: string) {
    await applyTokenResult(await oauthWebLogin(provider, code, state))
  }

  /** 刷新 access_token */
  async function refreshAccessToken(): Promise<boolean> {
    if (!refreshTokenVal.value) return false
    try {
      const result = await refreshTokenApi(refreshTokenVal.value)
      token.value = result.access_token
      if (result.refresh_token) {
        refreshTokenVal.value = result.refresh_token
      }
      expiresAt.value = Date.now() + result.expires_in * 1000

      localStorage.setItem(TOKEN_KEY, result.access_token)
      localStorage.setItem(REFRESH_KEY, refreshTokenVal.value)
      localStorage.setItem(EXPIRES_KEY, String(expiresAt.value))

      return true
    } catch {
      clearState()
      return false
    }
  }

  /** 退出登录 */
  async function logout() {
    try {
      await logoutApi()
    } catch {
      // ignore
    }
    clearState()
  }

  /** 清除所有状态 */
  function clearState() {
    token.value = ''
    refreshTokenVal.value = ''
    expiresAt.value = 0
    freeCount.value = 0
    userInfo.value = {}

    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
    localStorage.removeItem(EXPIRES_KEY)
    localStorage.removeItem(NICKNAME_KEY)
    localStorage.removeItem(AVATAR_KEY)
  }

  /** 更新免费次数 */
  function updateFreeCount(count: number) {
    freeCount.value = count
  }

  return {
    // 状态
    token,
    refreshTokenVal,
    expiresAt,
    freeCount,
    userInfo,
    // 计算属性
    isLogin,
    isTokenExpiring,
    freeCountText,
    // 方法
    restoreLogin,
    loadUserProfile,
    login,
    applyTokenResult,
    loginWithOAuth,
    refreshAccessToken,
    logout,
    clearState,
    updateFreeCount,
  }
})
