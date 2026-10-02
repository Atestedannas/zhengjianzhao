<template>
  <div class="oauth-login">
    <!-- 微信：官方 qrconnect 页面自带二维码，默认成功后跳转顶层窗口到回调地址 -->
    <template v-if="provider === 'wechat'">
      <div class="oauth-frame-wrap">
        <iframe
          v-if="authUrl"
          :src="authUrl"
          frameborder="0"
          scrolling="no"
          width="300"
          height="400"
          title="微信扫码登录"
        ></iframe>
        <div v-else-if="error" class="oauth-state is-error">{{ error }}</div>
        <div v-else class="oauth-state">
          <el-icon class="is-loading" :size="22"><Loading /></el-icon>
          <span>正在准备二维码…</span>
        </div>
      </div>
      <p class="oauth-tip">请用微信扫描二维码，并在手机上确认登录</p>
      <a v-if="authUrl" :href="authUrl" target="_blank" rel="noopener" class="oauth-fallback">
        二维码没显示？点此在新窗口打开
      </a>
    </template>

    <!-- 支付宝：官方要求整页跳转到授权页，二维码由支付宝页面展示 -->
    <template v-else>
      <div class="oauth-alipay">
        <el-button type="primary" size="large" :loading="loading" @click="goAlipay">
          <el-icon><Link /></el-icon>
          <span>前往支付宝扫码</span>
        </el-button>
        <p class="oauth-tip">
          点击后跳转到支付宝登录页，扫码并在手机上确认，完成后会自动回到本页面
        </p>
        <p v-if="error" class="oauth-tip is-error">{{ error }}</p>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
/** 扫码登录面板：微信内嵌官方二维码，支付宝跳官方授权页。 */
import { ref, watch, onUnmounted } from 'vue'
import { Loading, Link } from '@element-plus/icons-vue'
import { getOAuthAuthUrl, type OAuthProvider } from '@/api'
import { rememberOAuthRedirect } from '@/utils/redirect'

const props = defineProps<{
  provider: OAuthProvider
  /** 面板是否可见（弹窗切到该 tab 时才去请求授权地址） */
  active: boolean
  /** 扫码回来后要跳回的站内地址 */
  redirectTo?: string
}>()

const authUrl = ref('')
const error = ref('')
const loading = ref(false)
let seq = 0

async function load() {
  const mine = ++seq
  authUrl.value = ''
  error.value = ''
  loading.value = true
  try {
    const result = await getOAuthAuthUrl(props.provider)
    if (mine !== seq) return
    authUrl.value = result.auth_url || ''
    if (!authUrl.value) {
      error.value = '服务端未返回授权地址'
      return
    }
    // 平台跳回时原来的 ?redirect= 会丢，先存起来给回调页用
    rememberOAuthRedirect(props.redirectTo || '/')
  } catch (e: any) {
    if (mine !== seq) return
    // 后端未配置时返回 503，这里把原因原样展示，便于排查
    error.value = e?.response?.data?.detail || e?.message || '获取授权地址失败'
  } finally {
    if (mine === seq) loading.value = false
  }
}

function goAlipay() {
  if (!authUrl.value) return
  window.location.assign(authUrl.value)
}

watch(
  () => [props.provider, props.active] as const,
  ([, active]) => {
    if (active) {
      load()
    } else {
      // 离开该 tab 时清空，避免回来时展示旧二维码（微信 state 已失效）
      seq++
      authUrl.value = ''
      error.value = ''
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  seq++
})
</script>

<style scoped>
.oauth-login {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 320px;
  padding-top: 8px;
}

.oauth-frame-wrap {
  width: 300px;
  height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
}

.oauth-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  color: #909399;
  font-size: 13px;
  text-align: center;
  padding: 0 16px;
}

.oauth-state.is-error,
.oauth-tip.is-error {
  color: #f56c6c;
}

.oauth-tip {
  margin: 12px 0 0;
  font-size: 13px;
  color: #909399;
  text-align: center;
  line-height: 1.6;
}

.oauth-fallback {
  margin-top: 8px;
  font-size: 12px;
  color: #409eff;
  text-decoration: none;
}

.oauth-alipay {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 280px;
  padding: 0 16px;
}
</style>
