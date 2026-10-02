import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api'
import { useAuthStore } from './stores/auth'
import './assets/main.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)
app.use(ElementPlus)

// 登录失效（401 且刷新失败）统一处理：
// 清掉内存中的登录态；只有当前页面本身需要登录时才回首页并提示，
// 公开页面原地不动 —— 避免硬跳转跳到 / 或反复刷新。
setUnauthorizedHandler(() => {
  useAuthStore(pinia).clearState()
  const current = router.currentRoute.value
  if (current.meta.requiresAuth) {
    // 带上当前地址，登录成功后由 AppHeader 回跳到用户原本要去的页面
    router.replace({ path: '/', query: { redirect: current.fullPath, needLogin: '1' } })
  }
})

app.mount('#app')
