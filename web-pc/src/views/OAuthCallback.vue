<template>
  <div class="oauth-callback">
    <el-result
      :icon="status === 'error' ? 'error' : 'success'"
      :title="title"
      :sub-title="message"
    >
      <template #extra>
        <el-button v-if="status === 'error'" type="primary" @click="goHome">返回首页</el-button>
      </template>
    </el-result>
  </div>
</template>

<script setup lang="ts">
/**
 * 微信 / 支付宝扫码登录的回调落地页。
 *
 * 平台会带着授权码把浏览器跳到这里：
 *   微信   → ?code=...&state=...
 *   支付宝 → ?auth_code=...&state=...
 * state 形如 "wechat.xxxx" / "alipay.xxxx"，据此决定调哪个回调接口。
 */
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { takeOAuthRedirect } from '@/utils/redirect'
import type { OAuthProvider } from '@/api'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const status = ref<'success' | 'error'>('success')
const title = ref('正在登录…')
const message = ref('')

function firstString(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value) && typeof value[0] === 'string') return value[0]
  return ''
}

function goHome() {
  router.replace('/')
}

onMounted(async () => {
  const query = route.query
  const state = firstString(query.state)
  // 微信回 code，支付宝回 auth_code；有些场景会带 error
  const code = firstString(query.code) || firstString(query.auth_code)
  const platformError =
    firstString(query.error_description) || firstString(query.error) || ''

  if (platformError) {
    status.value = 'error'
    title.value = '登录失败'
    message.value = platformError
    return
  }

  if (!code || !state) {
    status.value = 'error'
    title.value = '登录失败'
    message.value = '回调参数不完整，请返回首页重新登录'
    return
  }

  const provider: OAuthProvider = state.startsWith('alipay') ? 'alipay' : 'wechat'

  try {
    await authStore.loginWithOAuth(provider, code, state)
    title.value = '登录成功'
    message.value = '正在返回…'
    await router.replace(takeOAuthRedirect())
  } catch (e: any) {
    status.value = 'error'
    title.value = '登录失败'
    message.value = e?.response?.data?.detail || e?.message || '请返回首页重试'
  }
})
</script>

<style scoped>
.oauth-callback {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}
</style>
