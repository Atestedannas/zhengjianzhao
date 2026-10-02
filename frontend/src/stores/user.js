/**
 * 用户状态管理
 * state: token, refreshToken, profile, freeCount, balance, isLogin
 */
import { defineStore } from 'pinia'
import { getProfile, getFreeCount } from '@/api/user'
import { wechatLogin } from '@/api/auth'
import { clearTokens, setAccessToken, setRefreshToken } from '@/utils/auth'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: uni.getStorageSync('access_token') || '',
    refreshToken: uni.getStorageSync('refresh_token') || '',
    profile: null,
    freeCount: 0,
    balance: 0,
    isLogin: false,
  }),

  getters: {
    // -1 是管理员设置的「无限次数」，不能当成「没有次数」
    hasFreeCount: (state) => state.freeCount !== 0,
    freeCountText: (state) => (state.freeCount === -1 ? '∞' : String(state.freeCount)),
    nickname: (state) => state.profile?.nickname || '未登录',
    avatarUrl: (state) => state.profile?.avatar_url || '',
  },

  actions: {
    /**
     * 小程序静默登录
     */
    async login() {
      try {
        // #ifdef MP-WEIXIN
        const loginRes = await uni.login()
        if (!loginRes.code) {
          uni.showToast({ title: '获取登录凭证失败', icon: 'none' })
          return false
        }
        const res = await wechatLogin(loginRes.code)
        if (res.code === 200) {
          const { access_token, refresh_token } = res.data
          this.token = access_token
          this.refreshToken = refresh_token
          this.isLogin = true
          setAccessToken(access_token)
          setRefreshToken(refresh_token)
          await this.fetchProfile()
          await this.fetchFreeCount()
          return true
        }
        // #endif
        return false
      } catch (e) {
        console.error('[UserStore] login error:', e)
        return false
      }
    },

    /**
     * H5 登录后设置 Token（由扫码回调页面调用）
     */
    setTokens(accessToken, refreshTokenValue) {
      this.token = accessToken
      this.refreshToken = refreshTokenValue
      this.isLogin = true
      setAccessToken(accessToken)
      setRefreshToken(refreshTokenValue)
    },

    /**
     * 拉取用户信息
     */
    async fetchProfile() {
      if (!this.isLogin) return
      try {
        const res = await getProfile()
        if (res.code === 200) {
          this.profile = res.data
        }
      } catch {
        // 静默失败，后续请求会处理
      }
    },

    /**
     * 查询剩余免费次数
     */
    async fetchFreeCount() {
      if (!this.isLogin) return
      try {
        const res = await getFreeCount()
        if (res.code === 200) {
          this.freeCount = res.data?.free_count ?? 0
          this.balance = res.data?.balance ?? 0
        }
      } catch {
        // 静默失败
      }
    },

    /**
     * 退出登录
     */
    logout() {
      this.token = ''
      this.refreshToken = ''
      this.profile = null
      this.freeCount = 0
      this.balance = 0
      this.isLogin = false
      clearTokens()
    },
  },
})
