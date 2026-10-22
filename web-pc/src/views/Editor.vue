<template>
  <div class="editor-page">
    <div class="editor-container">
      <!-- 左侧：图片预览区 -->
      <div class="preview-section">
        <div class="preview-header">
          <h3 class="preview-title">照片预览</h3>
          <span class="free-tip" v-if="authStore.isLogin">
            剩余免费次数：<strong>{{ authStore.freeCountText }}</strong> 次
          </span>
        </div>

        <div class="preview-area">
          <!-- 上传区域 -->
          <div
            v-if="!photoStore.originalImage"
            class="upload-zone"
            :class="{ dragover: isDragging }"
            @click="triggerUpload"
            @dragover.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
          >
            <el-icon :size="64" color="#c0c4cc" class="upload-icon">
              <UploadFilled />
            </el-icon>
            <p class="upload-text">点击或拖拽图片到此处上传</p>
            <p class="upload-hint">支持 JPG、PNG 格式，建议上传正面免冠照片</p>
            <el-button type="primary" size="large" style="margin-top: 20px">
              <el-icon><Plus /></el-icon>
              选择图片
            </el-button>
            <input
              ref="fileInputRef"
              type="file"
              accept="image/jpeg,image/png,image/jpg"
              style="display: none"
              @change="handleFileChange"
            />
          </div>

          <!-- 有结果：原图 / 效果图对比（三种形态，全部严格对齐） -->
          <div v-else-if="photoStore.processResult" class="preview-result">
            <CompareView
              :result-src="photoStore.processResult.result_url"
              :baseline-src="photoStore.processResult.baseline_url"
              :fallback-src="photoStore.originalImage"
            />
          </div>

          <!-- 处理中：进度 + 已等待时间 + 取消（不再是空转圈） -->
          <div v-else-if="photoStore.processing" class="processing-panel">
            <el-progress
              type="circle"
              :percentage="progressPercent"
              :width="120"
              :stroke-width="8"
            />
            <p class="processing-stage">{{ photoStore.processingStage || '正在处理…' }}</p>
            <p class="processing-meta">
              已等待 {{ photoStore.processingElapsed }} 秒<template
                v-if="photoStore.processingEta > 0"
              >，预计还需约 {{ photoStore.processingEta }} 秒</template>
            </p>
            <el-button size="small" @click="handleCancel">取消等待</el-button>
            <p class="processing-hint">
              最长等待 90 秒，超时会自动停止并提示重试；已提交的任务仍可能在后台完成，
              可稍后到「历史记录」查看。
            </p>
          </div>

          <!-- 仅上传、未处理：显示原图 -->
          <div v-else class="preview-original">
            <img :src="photoStore.originalImage" alt="原图" class="original-img" />
            <p class="original-hint">
              <el-icon><MagicStick /></el-icon>
              设置好参数后点「开始处理」，这里会显示与原图严格对齐的效果图对比
            </p>
          </div>
        </div>

        <!-- 操作按钮 -->
        <div class="preview-actions" v-if="photoStore.originalImage">
          <el-button @click="reUpload">
            <el-icon><RefreshLeft /></el-icon>
            重新上传
          </el-button>
          <el-button
            type="primary"
            size="large"
            :loading="photoStore.processing"
            :disabled="!authStore.isLogin || photoStore.processing"
            @click="handleProcess"
          >
            <el-icon><MagicStick /></el-icon>
            {{ photoStore.processing ? '处理中…' : (photoStore.processResult ? '重新处理' : '开始处理') }}
          </el-button>
          <el-button
            v-if="photoStore.processing"
            @click="handleCancel"
          >
            取消
          </el-button>
          <el-button
            v-if="photoStore.processResult"
            type="success"
            @click="handleDownload"
          >
            <el-icon><Download /></el-icon>
            下载结果
          </el-button>
        </div>

        <!-- 未登录提示 -->
        <div v-if="!authStore.isLogin" class="login-prompt">
          <el-alert
            title="请先登录后使用照片处理功能"
            type="warning"
            :closable="false"
            show-icon
          >
            <template #default>
              <p>登录后即可使用免费处理次数</p>
              <el-button type="primary" size="small" @click="$emit('showLogin')">
                立即登录
              </el-button>
            </template>
          </el-alert>
        </div>
      </div>

      <!-- 右侧：参数设置区（全部平铺，不再用左右箭头藏功能） -->
      <div class="settings-section">
        <el-alert
          v-if="featureHint"
          :title="featureHint"
          type="info"
          show-icon
          class="feature-hint"
          @close="featureHint = ''"
        />

        <el-scrollbar class="settings-scroll">
          <!-- ① 证件照规格 -->
          <el-collapse v-model="openPanels" class="settings-collapse">
            <el-collapse-item name="template">
              <template #title>
                <span class="panel-title"><el-icon><Collection /></el-icon>证件照规格</span>
                <span class="panel-summary">{{ templateSummary }}</span>
              </template>

              <div class="template-list">
                <div
                  v-for="tmpl in photoStore.templates"
                  :key="tmpl.id"
                  class="template-item"
                  :class="{ active: photoStore.selectedTemplate === tmpl.id }"
                  @click="photoStore.selectTemplate(tmpl.id)"
                >
                  <div class="tmpl-name">{{ tmpl.name }}</div>
                  <div class="tmpl-size">{{ tmpl.width_px }}×{{ tmpl.height_px }}px</div>
                </div>
              </div>

              <div class="custom-size-row" v-if="!photoStore.selectedTemplate">
                <el-input-number
                  v-model="photoStore.customWidth"
                  :min="0"
                  :max="8000"
                  size="small"
                  placeholder="宽"
                  style="width: 45%"
                />
                <span class="x-mark">×</span>
                <el-input-number
                  v-model="photoStore.customHeight"
                  :min="0"
                  :max="8000"
                  size="small"
                  placeholder="高"
                  style="width: 45%"
                />
              </div>
              <p class="setting-hint" v-if="!photoStore.selectedTemplate">
                像素单位，留空则保持原图尺寸
              </p>
            </el-collapse-item>

            <!-- ② 背景颜色 -->
            <el-collapse-item name="bg">
              <template #title>
                <span class="panel-title"><el-icon><Brush /></el-icon>背景颜色</span>
                <span class="panel-summary">{{ getColorLabel(photoStore.selectedBgColor) }}</span>
              </template>

              <div class="bg-color-grid">
                <div
                  v-for="color in photoStore.availableBgColors"
                  :key="color.name"
                  class="bg-color-item"
                  :class="{ active: photoStore.selectedBgColor === color.name }"
                  @click="photoStore.selectedBgColor = color.name"
                >
                  <div class="color-swatch" :style="swatchStyle(color)"></div>
                  <span class="color-name">{{ getColorLabel(color.name) }}</span>
                </div>
              </div>
              <div class="custom-bg-row" v-if="photoStore.selectedBgColor === 'custom'">
                <el-color-picker v-model="photoStore.customBgColor" size="small" />
                <span class="setting-hint">自定义背景色 {{ photoStore.customBgColor }}</span>
              </div>
            </el-collapse-item>

            <!-- ③ 自然微调（含美颜参数与对比说明） -->
            <el-collapse-item name="beautify">
              <template #title>
                <span class="panel-title"><el-icon><MagicStick /></el-icon>自然微调</span>
                <span class="panel-summary">
                  {{ beautifyOn ? `已开启 · 强度 ${strengthPercent}%` : '原图直出' }}
                </span>
              </template>

              <el-alert
                title="证件照仅允许轻微肤色均匀化：不改五官比例、不改骨骼轮廓、不丢失面部特征"
                type="info"
                :closable="false"
                show-icon
                class="compliance-alert"
              />
              <el-switch
                v-model="beautifyOn"
                size="large"
                active-text="开启微调"
                inactive-text="原图直出"
                class="beautify-switch"
              />

              <template v-if="beautifyOn">
                <h4 class="setting-title sub-title">美颜强度（还原度）</h4>
                <el-slider
                  v-model="strengthPercent"
                  :min="0"
                  :max="100"
                  :step="5"
                  :marks="{ 0: '原图', 40: '轻修', 100: '精修' }"
                />
                <p class="compliance-note">
                  当前强度 {{ strengthPercent }}%（推荐 40% 轻修，以真实为主）；
                  0% = 完全还原原图，磨皮/提亮/匀肤/分区肤色都会按此比例减弱。
                </p>

                <el-checkbox v-model="photoStore.beautifyOptions.smooth">
                  肤色均匀化（磨皮 ≤12%，保留毛孔纹理）
                </el-checkbox>
                <el-checkbox v-model="photoStore.beautifyOptions.brighten">
                  轻微提亮（≤10%，保留面部自然明暗）
                </el-checkbox>
                <el-checkbox v-model="photoStore.beautifyOptions.blemish">
                  去除临时痘印/红肿（保留痣与面部特征）
                </el-checkbox>

                <el-divider content-position="left">分区域处理</el-divider>
                <el-checkbox v-model="photoStore.beautifyOptions.tone">
                  分区肤色修正（额头/两颊/下颌/脖子各自对齐参考肤色）
                </el-checkbox>
                <el-checkbox v-model="photoStore.beautifyOptions.texture">
                  纹理回注（把磨皮拿走的毛孔细节补回来，防塑料感）
                </el-checkbox>
                <el-checkbox v-model="photoStore.beautifyOptions.feature">
                  五官轮廓增强（眼/眉/鼻翼/唇缘，不改形状不改位置）
                </el-checkbox>
                <p class="compliance-note">
                  以上三项是「分区域处理系统」的开关：关掉即退回「整脸滤镜」的旧行为，
                  便于逐项对比效果。
                </p>

                <el-checkbox v-model="artisticOn" :disabled="photoStore.idPhotoAlign">
                  艺术增强（轻磨皮 + 通透 + 眼神光，仅非证件照）
                </el-checkbox>
                <p v-if="artisticOn" class="compliance-note">
                  艺术档不受证件照合规红线约束，只用于写真/社交图片；
                  证件照报名请保持关闭（证件照对齐开启时本项自动禁用）。
                </p>
                <p class="compliance-note">
                  已锁定：瘦脸 / 大眼 / 改眼距 / 改鼻翼 / 改下颌角 — 全部禁止。
                </p>
              </template>
            </el-collapse-item>

            <!-- ④ 输出设置 -->
            <el-collapse-item name="advanced">
              <template #title>
                <span class="panel-title"><el-icon><Setting /></el-icon>输出设置</span>
                <span class="panel-summary">{{ photoStore.outputFormat }} · {{ photoStore.dpi }}DPI</span>
              </template>

              <div class="setting-row">
                <label>输出格式</label>
                <el-select v-model="photoStore.outputFormat" size="small" style="width: 140px">
                  <el-option
                    v-for="fmt in photoStore.outputFormats"
                    :key="fmt.format"
                    :label="`${fmt.format} (${fmt.description})`"
                    :value="fmt.format"
                  />
                </el-select>
              </div>
              <div class="setting-row">
                <label>DPI</label>
                <el-input-number
                  v-model="photoStore.dpi"
                  :min="72"
                  :max="1200"
                  size="small"
                  style="width: 140px"
                />
              </div>
              <div class="setting-row">
                <label>证件照对齐</label>
                <el-switch v-model="photoStore.idPhotoAlign" size="small" />
              </div>
              <div class="setting-row" v-if="photoStore.idPhotoAlign">
                <label>性别</label>
                <el-radio-group v-model="photoStore.gender" size="small">
                  <el-radio-button value="">自动</el-radio-button>
                  <el-radio-button value="male">男</el-radio-button>
                  <el-radio-button value="female">女</el-radio-button>
                </el-radio-group>
              </div>
              <p v-if="photoStore.idPhotoAlign && !photoStore.gender" class="compliance-note">
                性别「自动」为启发式估计（约七成准确率），仅影响服装建议文案；
                如需准确建议请手动选择男/女。
              </p>
            </el-collapse-item>
          </el-collapse>
        </el-scrollbar>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  UploadFilled,
  Plus,
  MagicStick,
  RefreshLeft,
  Download,
  Collection,
  Brush,
  Setting,
} from '@element-plus/icons-vue'
import { usePhotoStore, ProcessPollError, ProcessTimeoutError } from '@/stores/photo'
import { useAuthStore } from '@/stores/auth'
import { getDownloadUrl, getProtectedFile } from '@/api'
import CompareView from '@/components/CompareView.vue'
import { saveBlob } from '@/utils/file'

defineEmits(['showLogin'])

const route = useRoute()
const photoStore = usePhotoStore()
const authStore = useAuthStore()

const fileInputRef = ref<HTMLInputElement>()
const isDragging = ref(false)
/** 从首页功能卡片进来时显示的提示 */
const featureHint = ref('')

/**
 * 右侧面板全部平铺展开（accordion 手风琴）.
 *
 * 【为什么不再用左右箭头翻页】用户反馈：用箭头切换时根本不知道右边还有没有
 * 别的功能，「背景颜色」「自然微调」「高级设置」全被藏起来，操作成本极高。
 * 现在四个分组**默认全部展开**、可整体滚动，用户一眼看到所有能力；
 * 折叠只是为了「我看完了，收起来省空间」，而不是「藏功能」。
 */
const openPanels = ref<string[]>(['template', 'bg', 'beautify', 'advanced'])

/**
 * 「自然微调」总开关。
 * 后端只接受数值档位（0=关闭，1=自然微调，4=艺术档），这里做布尔 ↔ 数值的双向映射，
 * 保证 UI 上只有一个开关，避免用户选到被红线钳制的 2/3 档。
 */
const beautifyOn = computed({
  get: () => photoStore.beautifyLevel > 0,
  set: (on: boolean) => {
    photoStore.beautifyLevel = on ? (photoStore.beautifyLevel === 4 ? 4 : 1) : 0
  },
})

/**
 * 「艺术增强」档（后端 ARTISTIC=4）：仅非证件照流程可用。
 * 证件照对齐模式开启时该选项禁用，防止把不受红线约束的档位用在报名照片上；
 * 关闭总开关会回落到自然微调（1），重新勾选即回到艺术档。
 */
const artisticOn = computed({
  get: () => photoStore.beautifyLevel === 4,
  set: (on: boolean) => {
    photoStore.beautifyLevel = on ? 4 : 1
  },
})

/** 美颜强度滑杆（0~100%），映射到后端 retouch_strength 0~1 */
const strengthPercent = computed({
  get: () => Math.round(photoStore.retouchStrength * 100),
  set: (v: number) => {
    photoStore.retouchStrength = v / 100
  },
})

/** 面板标题右侧的摘要：不展开也能看到当前选择 */
const templateSummary = computed(() => {
  const t = photoStore.currentTemplate
  if (t) return `${t.name} · ${t.width_px}×${t.height_px}`
  if (photoStore.customWidth && photoStore.customHeight) {
    return `自定义 ${photoStore.customWidth}×${photoStore.customHeight}`
  }
  return '未选择（按原图尺寸输出）'
})

/**
 * 处理进度：由「已等待秒数 / 预计总时长」估算，圆环只做安抚用。
 * 真实状态仍以轮询结果为准 —— 这里永远不显示 100%，避免「满了还没好」。
 */
const progressPercent = computed(() => {
  const eta = photoStore.processingEta || 15
  const elapsed = photoStore.processingElapsed
  const total = Math.max(eta, elapsed + eta)
  if (total <= 0) return 5
  return Math.min(95, Math.max(5, Math.round((elapsed / total) * 100)))
})

const colorLabels: Record<string, string> = {
  keep: '保留原背景',
  white: '白色',
  pearl_white: '珍珠白',
  blue: '标准蓝',
  dark_blue: '深蓝',
  light_blue: '浅蓝',
  sky_blue: '天蓝',
  red: '标准红',
  wine_red: '酒红',
  maroon: '深红',
  gray: '灰色',
  light_gray: '浅灰',
  dark_gray: '深灰',
  green: '绿色',
  forest_green: '墨绿',
  mint_green: '薄荷绿',
  pink: '粉色',
  rose: '玫瑰粉',
  cream: '米色',
  champagne: '香槟米',
  navy: '藏青',
  teal: '青色',
  lavender: '淡紫',
  custom: '自定义',
}

function getColorLabel(name: string): string {
  return colorLabels[name] || name
}

/** 色板样式：普通色用 rgb；keep 画斜纹占位；custom 用取色器当前值 */
function swatchStyle(color: { name: string; rgb: number[] }): Record<string, string> {
  if (color.name === 'keep') {
    return {
      background:
        'repeating-linear-gradient(45deg, #fff 0 6px, #dcdfe6 6px 12px)',
    }
  }
  if (color.name === 'custom') {
    return { background: photoStore.customBgColor }
  }
  return { background: `rgb(${color.rgb.join(',')})` }
}

/** 已等待秒数：本地计时，不依赖后端进度接口（后端只有 pending/processing） */
let elapsedTimer: ReturnType<typeof setInterval> | null = null

function startElapsedTimer() {
  stopElapsedTimer()
  const started = Date.now()
  elapsedTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - started) / 1000)
    photoStore.processingElapsed = sec
    // 预计剩余：初估 15 秒，随时间保守回落到 5 秒，避免显示「还需 -3 秒」
    const eta = Math.max(5, 15 - Math.floor(sec / 6))
    photoStore.processingEta = eta
  }, 500)
}

function stopElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

onMounted(async () => {
  await Promise.all([
    photoStore.loadTemplates(),
    photoStore.loadOutputFormats(),
    photoStore.loadBgColors(),
  ])

  // 从 URL 参数中获取模板 ID
  const templateId = route.query.template
  if (templateId) {
    photoStore.selectTemplate(Number(templateId))
  }

  applyEntryFromQuery()
})

// 离开页面时停止轮询，避免定时器泄漏和后台重复请求
onUnmounted(() => {
  stopElapsedTimer()
  photoStore.cancelPolling()
})

/**
 * 首页功能卡片 → 编辑器分组 + 预置参数。
 * 例：/editor?feature=beautify 直接定位到「自然微调」。
 */
const FEATURE_ENTRY: Record<string, { panel: string; label: string; apply?: () => void }> = {
  // 抠图由后端流水线自动完成，这里把用户带到「背景颜色」——换底就是抠图最直接的用法
  cutout: { panel: 'bg', label: '智能抠图' },
  bg: { panel: 'bg', label: '背景替换' },
  beautify: { panel: 'beautify', label: '自然微调' },
  size: { panel: 'template', label: '尺寸裁剪' },
  format: { panel: 'advanced', label: '格式转换' },
  dpi: {
    panel: 'advanced',
    label: '高清输出',
    apply: () => {
      // 卡片承诺 300DPI，进来就预置好（不覆盖已经更高的值）
      if (photoStore.dpi < 300) photoStore.dpi = 300
    },
  },
}

const PANEL_NAMES = ['template', 'bg', 'beautify', 'advanced']

/** 从 query 定位分组：把目标分组放到最上面并保持全部展开 */
function applyEntryFromQuery() {
  const feature = typeof route.query.feature === 'string' ? route.query.feature : ''
  const entry = feature ? FEATURE_ENTRY[feature] : undefined

  const target = entry?.panel || (typeof route.query.tab === 'string' ? route.query.tab : '')
  if (entry) {
    entry.apply?.()
    featureHint.value = `已定位到「${entry.label}」，上传照片后点「开始处理」即可`
  }

  if (target && PANEL_NAMES.includes(target)) {
    // 全部保持展开（不藏功能），只把目标分组滚动到视野内
    openPanels.value = [...PANEL_NAMES]
    const index = PANEL_NAMES.indexOf(target)
    requestAnimationFrame(() => {
      // el-collapse-item 没有稳定的 data 属性，按顺序取第 index 个分组标题
      const headers = document.querySelectorAll('.settings-collapse .el-collapse-item__header')
      headers[index]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }
}

function triggerUpload() {
  fileInputRef.value?.click()
}

function handleFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    handleFile(file)
  }
  // 重置 input，允许重复选择同一文件
  input.value = ''
}

function handleDrop(e: DragEvent) {
  isDragging.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file && file.type.startsWith('image/')) {
    handleFile(file)
  } else {
    ElMessage.warning('请上传图片文件')
  }
}

function handleFile(file: File) {
  // 检查文件类型
  if (!['image/jpeg', 'image/png', 'image/jpg'].includes(file.type)) {
    ElMessage.warning('仅支持 JPG、PNG 格式的图片')
    return
  }
  // 检查文件大小（10MB）
  if (file.size > 10 * 1024 * 1024) {
    ElMessage.warning('图片大小不能超过 10MB')
    return
  }
  photoStore.setOriginalImage(file)
}

function reUpload() {
  photoStore.clearImage()
  photoStore.processResult = null
}

/** 用户取消：立刻停轮询并恢复按钮（后端任务可能仍在跑，历史记录里能看到） */
function handleCancel() {
  photoStore.cancelProcess()
  stopElapsedTimer()
  ElMessage.info('已停止等待；任务可能仍在后台完成，可稍后到「历史记录」查看')
}

async function handleProcess() {
  // 防止重复提交：后端按次扣费，轮询期间不允许再次提交
  if (photoStore.processing) return
  if (!authStore.isLogin) {
    ElMessage.warning('请先登录')
    return
  }
  if (!photoStore.originalFile) {
    ElMessage.warning('请先上传照片')
    return
  }

  startElapsedTimer()
  try {
    const result = await photoStore.processCurrentPhoto()
    if (result.free_used) {
      if (result.remaining_free_count === -1) {
        // 管理员设置的无限次数：不再报「剩余 -1 次」
        ElMessage.success('处理成功！当前为无限免费次数')
      } else {
        ElMessage.success(`处理成功！已使用 1 次免费额度，剩余 ${result.remaining_free_count} 次`)
      }
    } else {
      ElMessage.success('处理成功！')
    }
  } catch (err: any) {
    // 处理失败 / 轮询超时的文案由 store 抛出，直接展示
    if (err instanceof ProcessPollError) {
      if (err.timeout) {
        ElMessage.warning(err.message)
      } else {
        ElMessage.error(err.message)
      }
      return
    }
    // 【关键修复】客户端硬超时 / 单次请求超时：必须给用户明确的可重试提示，
    // 而不是让按钮永远停在 loading（历史 bug 的直接原因）
    if (err instanceof ProcessTimeoutError) {
      ElMessage.warning(err.message)
      return
    }
    // 轮询被取消（卸载 / 重新提交 / 主动取消）静默处理
    if (err?.name === 'ProcessCancelledError') return
    if (err.needPay) {
      ElMessage.warning('免费次数已用完，请联系管理员充值')
      return
    }
    // 兜底：任何未预期的异常都要有提示，绝不能静默卡住
    ElMessage.error(err?.message || '处理失败，请重试')
  } finally {
    stopElapsedTimer()
  }
}

async function handleDownload() {
  const result = photoStore.processResult
  if (!result) return
  try {
    // 下载接口同样要 Bearer 鉴权，必须先取 Blob 再落盘
    const { blob, filename } = await getProtectedFile(getDownloadUrl(result.record_id))
    saveBlob(blob, filename || `证件照_${result.pixels}.jpg`)
  } catch {
    // 失败提示由响应拦截器统一处理
  }
}
</script>

<style scoped>
.editor-page {
  padding: 24px 0;
  background: #f5f7fa;
  min-height: calc(100vh - 64px);
}

.editor-container {
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 20px;
  display: grid;
  grid-template-columns: 1fr 380px;
  gap: 24px;
  align-items: start;
}

/* ===== Preview Section ===== */
.preview-section {
  background: #fff;
  border-radius: 12px;
  padding: 24px;
  display: flex;
  flex-direction: column;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}

.preview-title {
  font-size: 18px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 0;
}

.free-tip {
  font-size: 13px;
  color: #909399;
}

.free-tip strong {
  color: #e6a23c;
  font-size: 15px;
}

.preview-area {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

/* Upload Zone */
.upload-zone {
  width: 100%;
  max-width: 500px;
  padding: 60px 40px;
  border: 2px dashed #dcdfe6;
  border-radius: 12px;
  text-align: center;
  cursor: pointer;
  transition: all 0.3s;
  background: #fafafa;
}

.upload-zone:hover,
.upload-zone.dragover {
  border-color: #409eff;
  background: #ecf5ff;
}

.upload-icon {
  margin-bottom: 16px;
}

.upload-text {
  font-size: 16px;
  color: #606266;
  margin: 0 0 8px 0;
}

.upload-hint {
  font-size: 13px;
  color: #909399;
  margin: 0;
}

/* 结果对比区 */
.preview-result {
  width: 100%;
}

/* 仅上传未处理：原图预览 */
.preview-original {
  width: 100%;
  max-width: 420px;
  text-align: center;
}

.original-img {
  width: 100%;
  max-height: 56vh;
  object-fit: contain;
  border-radius: 10px;
  border: 1px solid #ebeef5;
  background: #f5f7fa;
}

.original-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin: 12px 0 0;
  font-size: 13px;
  color: #909399;
}

/* 处理中：进度面板（替代旧的「空转圈」遮罩） */
.processing-panel {
  width: 100%;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 40px 24px;
  border: 1px dashed #dcdfe6;
  border-radius: 12px;
  background: #fafcff;
}

.processing-stage {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  color: #303133;
  text-align: center;
}

.processing-meta {
  margin: 0;
  font-size: 13px;
  color: #909399;
}

.processing-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #c0c4cc;
  line-height: 1.6;
  text-align: center;
}

/* Preview Actions */
.preview-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid #ebeef5;
}

.login-prompt {
  margin-top: 20px;
}

/* ===== Settings Section（全部平铺 + 手风琴） ===== */
.settings-section {
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  /* 固定在视口高度内滚动：用户向下滚就能看到全部功能，不需要翻页 */
  position: sticky;
  top: 88px;
  max-height: calc(100vh - 112px);
}

.feature-hint {
  margin: 12px 16px 0;
  width: auto;
  flex-shrink: 0;
}

.settings-scroll {
  flex: 1;
  min-height: 0;
}

.settings-collapse {
  border-top: none;
}

.settings-collapse :deep(.el-collapse-item__header) {
  padding: 0 16px;
  height: auto;
  min-height: 52px;
  line-height: 1.4;
  display: flex;
  gap: 10px;
}

.settings-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: 1px solid #f0f2f5;
}

.settings-collapse :deep(.el-collapse-item__content) {
  padding: 4px 16px 18px;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}

.panel-summary {
  margin-left: auto;
  margin-right: 8px;
  font-size: 12px;
  color: #909399;
  font-weight: 400;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sub-title {
  margin-top: 16px;
  font-size: 14px;
}

.setting-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 0 0 16px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.custom-bg-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}

.setting-hint {
  font-size: 12px;
  color: #909399;
  margin: 8px 0 0 0;
}

/* Template List */
.template-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  max-height: 320px;
  overflow-y: auto;
  padding-right: 4px;
}

.template-item {
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  text-align: center;
}

.template-item:hover {
  border-color: #409eff;
  background: #f0f7ff;
}

.template-item.active {
  border-color: #409eff;
  background: #ecf5ff;
}

.tmpl-name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 4px;
}

.tmpl-size {
  font-size: 11px;
  color: #909399;
}

/* Custom Size */
.custom-size-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.x-mark {
  color: #909399;
  font-size: 16px;
}

/* BG Colors */
.bg-color-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.bg-color-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 8px;
  border-radius: 8px;
  transition: all 0.2s;
}

.bg-color-item:hover {
  background: #f5f7fa;
}

.bg-color-item.active {
  background: #ecf5ff;
}

.color-swatch {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.bg-color-item.active .color-swatch {
  border-color: #409eff;
}

.color-name {
  font-size: 12px;
  color: #606266;
}

/* Setting Row */
.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid #f0f2f5;
}

.setting-row:last-child {
  border-bottom: none;
}

.setting-row label {
  font-size: 14px;
  color: #606266;
}

/* ===== 自然微调：合规提示 ===== */
.compliance-alert {
  margin-bottom: 12px;
  line-height: 1.5;
}

.beautify-switch {
  margin-top: 4px;
  margin-bottom: 8px;
}

.compliance-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

/* ===== Responsive ===== */
@media (max-width: 1024px) {
  .editor-container {
    grid-template-columns: 1fr;
  }

  .settings-section {
    position: static;
    max-height: none;
  }
}

@media (max-width: 768px) {
  .editor-page {
    padding: 12px 0;
  }

  .editor-container {
    padding: 0 12px;
  }

  .preview-section {
    padding: 16px;
  }

  .upload-zone {
    padding: 40px 20px;
  }

  .template-list {
    grid-template-columns: 1fr 1fr;
  }

  .bg-color-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
