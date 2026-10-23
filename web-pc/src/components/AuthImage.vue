<template>
  <img v-if="displaySrc" :src="displaySrc" :alt="alt" />
  <div v-else class="auth-image-fallback">
    <el-icon :size="28" color="#c0c4cc"><Picture /></el-icon>
  </div>
</template>

<script setup lang="ts">
/**
 * 带鉴权的图片。
 *
 * 后端的 /process/{id}/preview 需要 Bearer Token，`<img src>` 发不出这个头，
 * 所以这里改用 axios 取 Blob，再转成 object URL 交给 <img>。
 * 父组件传进来的 class 会通过属性透传落到根元素上，样式不受影响。
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { Picture } from '@element-plus/icons-vue'
import { getProtectedBlob } from '@/api'

const props = defineProps<{
  /** 可以是 /api/v1/... 的绝对路径，也可以是相对 baseURL 的路径 */
  src?: string | null
  alt?: string
}>()

const objectUrl = ref('')
let createdUrl: string | null = null
// 连续切换 src 时，丢弃过期响应，避免旧图覆盖新图
let seq = 0

/**
 * 是否需要走鉴权取 Blob.
 *
 * 只有后端受保护接口（/api/...）才需要 Bearer Token。静态资源
 * （/web-pc/xxx.jpg、data:、blob:、http(s)://）直接交给 <img> 即可 ——
 * 一律走 axios 会把这些路径拼成 /api/v1/web-pc/xxx.jpg 去请求，
 * 结果必然 404，图片位置只剩占位图。
 */
function needsAuth(src: string): boolean {
  if (!src) return false
  if (/^(data:|blob:|https?:)/.test(src)) return false
  return src.startsWith('/api/')
}

/** 实际交给 <img> 的地址：受保护资源用 objectURL，其余原样直连 */
const displaySrc = computed(() => {
  const src = props.src || ''
  if (!src) return ''
  return needsAuth(src) ? objectUrl.value : src
})

function releaseObjectUrl() {
  if (createdUrl) {
    URL.revokeObjectURL(createdUrl)
    createdUrl = null
  }
}

watch(
  () => props.src,
  async (src) => {
    const mine = ++seq
    releaseObjectUrl()
    objectUrl.value = ''
    if (!src) return
    // 静态资源不走鉴权：直接交给 <img>，无需请求 Blob
    if (!needsAuth(src)) return

    try {
      const blob = await getProtectedBlob(src)
      if (mine !== seq) return
      createdUrl = URL.createObjectURL(blob)
      objectUrl.value = createdUrl
    } catch {
      // 未登录 / 已过期 / 文件已清理：显示占位图即可
      if (mine === seq) objectUrl.value = ''
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  seq++
  releaseObjectUrl()
})
</script>

<style scoped>
.auth-image-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  min-height: 80px;
  background: #f5f7fa;
}
</style>
