<template>
  <header class="app-header">
    <div class="container header-inner">
      <div class="logo-area" @click="goHome">
        <div class="logo-icon">
          <el-icon :size="28" color="#fff">
            <Picture />
          </el-icon>
        </div>
        <div class="logo-text">
          <h1 class="logo-title">智能证件照</h1>
          <span class="logo-subtitle">AI 照片处理工具</span>
        </div>
      </div>

      <nav class="nav-menu">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          active-class="active"
        >
          <el-icon>
            <component :is="item.icon" />
          </el-icon>
          <span>{{ item.name }}</span>
        </router-link>
      </nav>

      <div class="user-area">
        <template v-if="authStore.isLogin">
          <div class="free-count-badge" @click="goProfile">
            <el-icon><Coin /></el-icon>
            <span>免费 {{ authStore.freeCountText }} 次</span>
          </div>
          <el-dropdown @command="handleCommand" trigger="click">
            <div class="user-info">
              <el-avatar :size="36" :src="authStore.userInfo.avatar_url">
                {{ authStore.userInfo.nickname?.charAt(0) || 'U' }}
              </el-avatar>
              <span class="username">{{ authStore.userInfo.nickname || '用户' }}</span>
              <el-icon class="arrow-icon"><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>
                  个人中心
                </el-dropdown-item>
                <el-dropdown-item command="history">
                  <el-icon><Clock /></el-icon>
                  处理记录
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
        <template v-else>
          <el-button type="primary" @click="showLoginDialog = true">
            登录
          </el-button>
        </template>
      </div>
    </div>

    <!-- 登录弹窗：密码 / 微信扫码 / 支付宝扫码 -->
    <el-dialog
      v-model="showLoginDialog"
      title="登录"
      width="440px"
      :close-on-click-modal="false"
      center
      @closed="onLoginDialogClosed"
    >
      <el-tabs v-model="loginTab" stretch>
        <el-tab-pane label="密码登录" name="password">
          <el-form
            ref="loginFormRef"
            :model="loginForm"
            :rules="loginRules"
            label-position="top"
            @submit.prevent="handleLogin"
          >
            <el-form-item label="访问密码" prop="password">
              <el-input
                v-model="loginForm.password"
                type="password"
                placeholder="请输入访问密码"
                show-password
                size="large"
              />
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                size="large"
                style="width: 100%"
                :loading="loginLoading"
                @click="handleLogin"
              >
                登录
              </el-button>
            </el-form-item>
          </el-form>
          <div class="login-tip">
            <el-icon><InfoFilled /></el-icon>
            <span>请输入系统管理员提供的访问密码</span>
          </div>
        </el-tab-pane>

        <el-tab-pane label="微信扫码" name="wechat">
          <OAuthLogin
            provider="wechat"
            :active="loginTab === 'wechat'"
            :redirect-to="pendingRedirect"
          />
        </el-tab-pane>

        <el-tab-pane label="支付宝扫码" name="alipay">
          <OAuthLogin
            provider="alipay"
            :active="loginTab === 'alipay'"
            :redirect-to="pendingRedirect"
          />
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
  </header>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import {
  Picture,
  HomeFilled,
  EditPen,
  Clock,
  User,
  Coin,
  ArrowDown,
  SwitchButton,
  InfoFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { webLogin } from '@/api'
import { safeRedirect } from '@/utils/redirect'
import OAuthLogin from '@/components/OAuthLogin.vue'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const showLoginDialog = ref(false)
const loginLoading = ref(false)
const loginFormRef = ref<FormInstance>()
const loginTab = ref<'password' | 'wechat' | 'alipay'>('password')

/** 扫码登录回来后要跳回的站内地址 */
const pendingRedirect = computed(() => safeRedirect(route.query.redirect, '/'))

function onLoginDialogClosed() {
  loginForm.password = ''
  loginTab.value = 'password'
}

const loginForm = reactive({
  password: '',
})

const loginRules: FormRules = {
  password: [{ required: true, message: '请输入访问密码', trigger: 'blur' }],
}

const navItems = [
  { path: '/', name: '首页', icon: HomeFilled },
  { path: '/editor', name: '照片编辑', icon: EditPen },
  { path: '/history', name: '处理记录', icon: Clock },
]

function goHome() {
  router.push('/')
}

function goProfile() {
  router.push('/profile')
}

/**
 * 路由守卫把未登录用户弹回首页时会带上 `?needLogin=1&redirect=<原目标>`。
 * 这里负责消费这两个参数：未登录自动弹登录框，已登录则补跳到目标页。
 */
function syncLoginStateFromQuery() {
  const redirect = route.query.redirect
  const needLogin = route.query.needLogin === '1'

  if (authStore.isLogin) {
    // 登录态已就绪（本地已有 token，或刚登录完刷新）→ 把守卫写进 URL 的目标补跳掉，
    // 否则用户会一直停在首页，永远进不去 /editor、/history。
    if (needLogin || redirect) {
      router.replace(safeRedirect(redirect))
    }
    return
  }

  if (needLogin) {
    showLoginDialog.value = true
  }
}

onMounted(syncLoginStateFromQuery)
watch(() => route.fullPath, syncLoginStateFromQuery)

async function handleLogin() {
  if (!loginFormRef.value) return
  await loginFormRef.value.validate(async (valid) => {
    if (!valid) return
    loginLoading.value = true
    try {
      await authStore.login(loginForm.password)
      ElMessage.success('登录成功')
      showLoginDialog.value = false
      loginForm.password = ''

      // 有回跳目标就回到用户原本要去的页面。这里不能 reload：
      // reload 只会把带参数的首页再加载一遍，跳转流程就断在这了。
      const target = safeRedirect(route.query.redirect, '')
      if (target) {
        await router.replace(target)
      } else {
        // 没有回跳目标（用户自己点的登录）：刷新当前页数据
        window.location.reload()
      }
    } catch (err) {
      // 错误已在拦截器中提示
    } finally {
      loginLoading.value = false
    }
  })
}

function handleCommand(command: string) {
  switch (command) {
    case 'profile':
      router.push('/profile')
      break
    case 'history':
      router.push('/history')
      break
    case 'logout':
      authStore.logout()
      ElMessage.success('已退出登录')
      if (route.path !== '/') {
        router.push('/')
      } else {
        window.location.reload()
      }
      break
  }
}
</script>

<style scoped>
.app-header {
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  position: sticky;
  top: 0;
  z-index: 100;
}

.header-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
}

.logo-area {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}

.logo-icon {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-text {
  display: flex;
  flex-direction: column;
}

.logo-title {
  font-size: 18px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 0;
  line-height: 1.2;
}

.logo-subtitle {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.nav-menu {
  display: flex;
  align-items: center;
  gap: 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 6px;
  color: #606266;
  font-size: 14px;
  transition: all 0.25s;
  text-decoration: none;
}

.nav-item:hover {
  color: #409eff;
  background: #ecf5ff;
}

.nav-item.active {
  color: #409eff;
  background: #ecf5ff;
  font-weight: 500;
}

.user-area {
  display: flex;
  align-items: center;
  gap: 16px;
}

.free-count-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
  border-radius: 20px;
  color: #e6a23c;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: transform 0.2s;
}

.free-count-badge:hover {
  transform: scale(1.05);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: background 0.2s;
}

.user-info:hover {
  background: #f5f7fa;
}

.username {
  font-size: 14px;
  color: #303133;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.arrow-icon {
  color: #909399;
  font-size: 12px;
}

.login-tip {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #909399;
  font-size: 12px;
  margin-top: -8px;
}

@media (max-width: 768px) {
  .nav-menu {
    display: none;
  }

  .logo-subtitle {
    display: none;
  }

  .username {
    display: none;
  }

  .free-count-badge span {
    display: none;
  }
}
</style>
