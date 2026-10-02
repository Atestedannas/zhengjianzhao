<script setup>
import { onLaunch } from '@dcloudio/uni-app'
import { useUserStore } from '@/stores/user'

onLaunch(async () => {
  console.log('[App] onLaunch')
  // 静默登录：尝试用持久化 token 拉取用户信息
  const token = uni.getStorageSync('access_token')
  if (token) {
    const userStore = useUserStore()
    userStore.token = token
    userStore.refreshToken = uni.getStorageSync('refresh_token') || ''
    userStore.isLogin = true
    try {
      await userStore.fetchProfile()
      await userStore.fetchFreeCount()
    } catch {
      // token 可能已过期，后续请求会触发 401 自动刷新流程
    }
  }
  // #ifdef MP-WEIXIN
  // 小程序如果没有 token，首次打开会由 index 页触发 wx.login
  // #endif
})

// 全局路由守卫：H5 下未登录不允许访问需要登录的页面
// #ifdef H5
const protectedPages = ['pages/history/index', 'pages/user/index']
const publicPages = ['pages/login/index', 'pages/index/index']

uni.addInterceptor('navigateTo', {
  invoke(args) {
    const userStore = useUserStore()
    const targetPage = args.url.split('?')[0]
    if (protectedPages.includes(targetPage) && !userStore.isLogin) {
      uni.navigateTo({ url: '/pages/login/index' })
      return false
    }
  }
})

uni.addInterceptor('switchTab', {
  invoke(args) {
    const userStore = useUserStore()
    const targetPage = args.url.split('?')[0]
    if (protectedPages.includes(targetPage) && !userStore.isLogin) {
      uni.navigateTo({ url: '/pages/login/index' })
      return false
    }
  }
})
// #endif
</script>

<style lang="scss">
/* 全局样式 */
page {
  background-color: $page-bg;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  color: $text-primary;
  font-size: 28rpx;
  line-height: 1.6;
}

/* 安全区适配 */
.safe-area-bottom {
  padding-bottom: constant(safe-area-inset-bottom);
  padding-bottom: env(safe-area-inset-bottom);
}
</style>
