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

          <!-- 图片对比预览 -->
          <div v-else class="preview-compare">
            <div class="compare-item">
              <div class="compare-label">
                <el-icon><Picture /></el-icon>
                原图
              </div>
              <div class="compare-img-wrap">
                <img :src="photoStore.originalImage" alt="原图" class="compare-img" />
              </div>
              <div class="compare-info">
                <span v-if="originalFileInfo">{{ originalFileInfo }}</span>
              </div>
            </div>

            <div class="compare-arrow">
              <el-icon v-if="photoStore.processing" :size="28" class="loading-icon">
                <Loading />
              </el-icon>
              <el-icon v-else :size="28" color="#409eff">
                <ArrowRight />
              </el-icon>
            </div>

            <div class="compare-item">
              <div class="compare-label">
                <el-icon><CircleCheck /></el-icon>
                处理结果
              </div>
              <div class="compare-img-wrap result-wrap">
                <template v-if="photoStore.processResult">
                  <AuthImage
                    :src="photoStore.processResult.result_url"
                    alt="处理结果"
                    class="compare-img"
                  />
                </template>
                <template v-else-if="photoStore.processing">
                  <div class="processing-overlay">
                    <el-icon :size="48" class="spinning"><Loading /></el-icon>
                    <p>AI 正在处理中...</p>
                  </div>
                </template>
                <template v-else>
                  <div class="empty-result">
                    <el-icon :size="48" color="#dcdfe6"><MagicStick /></el-icon>
                    <p>点击开始处理</p>
                  </div>
                </template>
              </div>
              <div class="compare-info">
                <template v-if="photoStore.processResult">
                  <span>{{ photoStore.processResult.pixels }}</span>
                  <span>·</span>
                  <span>{{ photoStore.processResult.file_size_kb }} KB</span>
                </template>
              </div>
            </div>
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
            {{ photoStore.processing ? '处理中...' : '开始处理' }}
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

      <!-- 右侧：参数设置区 -->
      <div class="settings-section">
        <el-alert
          v-if="featureHint"
          :title="featureHint"
          type="info"
          show-icon
          class="feature-hint"
          @close="featureHint = ''"
        />
        <el-tabs v-model="activeTab" class="settings-tabs">
          <!-- 模板选择 -->
          <el-tab-pane label="规格选择" name="template">
            <div class="setting-group">
              <h4 class="setting-title">
                <el-icon><Collection /></el-icon>
                证件照规格
              </h4>
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
            </div>

            <div class="setting-group" v-if="!photoStore.selectedTemplate">
              <h4 class="setting-title">
                <el-icon><Position /></el-icon>
                自定义尺寸
              </h4>
              <div class="custom-size-row">
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
              <p class="setting-hint">像素单位，留空则保持原图尺寸</p>
            </div>
          </el-tab-pane>

          <!-- 背景色 -->
          <el-tab-pane label="背景颜色" name="bg">
            <div class="setting-group">
              <h4 class="setting-title">
                <el-icon><Brush /></el-icon>
                选择背景色
              </h4>
              <div class="bg-color-grid">
                <div
                  v-for="color in photoStore.availableBgColors"
                  :key="color.name"
                  class="bg-color-item"
                  :class="{ active: photoStore.selectedBgColor === color.name }"
                  @click="photoStore.selectedBgColor = color.name"
                >
                  <div
                    class="color-swatch"
                    :style="{ background: `rgb(${color.rgb.join(',')})` }"
                  ></div>
                  <span class="color-name">{{ getColorLabel(color.name) }}</span>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- 自然微调（证件照只允许轻微、可逆的肤色处理） -->
          <el-tab-pane label="自然微调" name="beautify">
            <div class="setting-group">
              <h4 class="setting-title">
                <el-icon><MagicStick /></el-icon>
                自然微调
              </h4>
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
            </div>

            <div class="setting-group" v-if="beautifyOn">
              <h4 class="setting-title">允许的微调项</h4>
              <el-checkbox v-model="photoStore.beautifyOptions.smooth">
                肤色均匀化（磨皮 ≤20%，保留毛孔纹理）
              </el-checkbox>
              <el-checkbox v-model="photoStore.beautifyOptions.brighten">
                轻微提亮（≤15%，不改变五官立体光影）
              </el-checkbox>
              <el-checkbox v-model="photoStore.beautifyOptions.blemish">
                去除临时痘印/红肿（保留痣与面部特征）
              </el-checkbox>
              <p class="compliance-note">
                已锁定：瘦脸 / 大眼 / 改眼距 / 改鼻翼 / 改下颌角 — 全部禁止。
              </p>
            </div>

            <!-- 原图 / 效果图对比滑块（PRD 五） -->
            <div
              class="setting-group"
              v-if="beautifyOn && photoStore.processResult && photoStore.originalImage"
            >
              <h4 class="setting-title">原图 / 效果图对比</h4>
              <div class="compare-slider-wrap">
                <img
                  :src="photoStore.originalImage"
                  alt="原图"
                  class="compare-layer"
                />
                <div class="compare-overlay" :style="{ width: `${compareRatio}%` }">
                  <AuthImage
                    :src="photoStore.processResult.result_url"
                    alt="效果图"
                    class="compare-layer"
                  />
                </div>
                <div class="compare-divider" :style="{ left: `${compareRatio}%` }"></div>
                <input
                  v-model.number="compareRatio"
                  class="compare-range"
                  type="range"
                  min="0"
                  max="100"
                  aria-label="原图与效果图对比"
                />
                <span class="compare-tag tag-left">原图</span>
                <span class="compare-tag tag-right">效果</span>
              </div>
              <p class="compliance-note">拖动滑块查看微调前后的差异（默认 50%）</p>
            </div>
          </el-tab-pane>

          <!-- 高级设置 -->
          <el-tab-pane label="高级设置" name="advanced">
            <div class="setting-group">
              <h4 class="setting-title">
                <el-icon><Setting /></el-icon>
                输出设置
              </h4>
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
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  UploadFilled,
  Plus,
  Picture,
  ArrowRight,
  CircleCheck,
  MagicStick,
  RefreshLeft,
  Download,
  Collection,
  Position,
  Brush,
  Setting,
  Loading,
} from '@element-plus/icons-vue'
import { usePhotoStore, ProcessPollError } from '@/stores/photo'
import { useAuthStore } from '@/stores/auth'
import { getDownloadUrl, getProtectedFile } from '@/api'
import AuthImage from '@/components/AuthImage.vue'
import { saveBlob } from '@/utils/file'

defineEmits(['showLogin'])

const route = useRoute()
const photoStore = usePhotoStore()
const authStore = useAuthStore()

const fileInputRef = ref<HTMLInputElement>()
const isDragging = ref(false)
const activeTab = ref('template')
/** 从首页功能卡片进来时显示的提示 */
const featureHint = ref('')
/** 原图/效果图对比滑块位置（0~100，100=全部显示效果图） */
const compareRatio = ref(50)

/**
 * 「自然微调」总开关。
 * 后端只接受数值档位（0=关闭，1=自然微调），这里做布尔 ↔ 数值的双向映射，
 * 保证 UI 上只有一个开关，避免用户选到被红线钳制的 2/3 档。
 */
const beautifyOn = computed({
  get: () => photoStore.beautifyLevel > 0,
  set: (on: boolean) => {
    photoStore.beautifyLevel = on ? 1 : 0
  },
})

const originalFileInfo = computed(() => {
  if (!photoStore.originalFile) return ''
  const sizeKB = (photoStore.originalFile.size / 1024).toFixed(1)
  return `${sizeKB} KB`
})

const colorLabels: Record<string, string> = {
  white: '白色',
  blue: '蓝色',
  red: '红色',
  gray: '灰色',
  black: '黑色',
  lightblue: '浅蓝',
  darkblue: '深蓝',
  gradient: '渐变',
}

function getColorLabel(name: string): string {
  return colorLabels[name] || name
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
  photoStore.cancelPolling()
})

/**
 * 首页功能卡片 → 编辑器页签 + 预置参数。
 * 例：/editor?feature=beautify 直接打开「自然微调」。
 */
const FEATURE_ENTRY: Record<string, { tab: string; label: string; apply?: () => void }> = {
  // 抠图由后端流水线自动完成，这里把用户带到「背景颜色」——换底就是抠图最直接的用法
  cutout: { tab: 'bg', label: '智能抠图' },
  bg: { tab: 'bg', label: '背景替换' },
  beautify: { tab: 'beautify', label: '自然微调' },
  size: { tab: 'template', label: '尺寸裁剪' },
  format: { tab: 'advanced', label: '格式转换' },
  dpi: {
    tab: 'advanced',
    label: '高清输出',
    apply: () => {
      // 卡片承诺 300DPI，进来就预置好（不覆盖已经更高的值）
      if (photoStore.dpi < 300) photoStore.dpi = 300
    },
  },
}

const EDITOR_TABS = ['template', 'bg', 'beautify', 'advanced']

function applyEntryFromQuery() {
  const feature = typeof route.query.feature === 'string' ? route.query.feature : ''
  const entry = feature ? FEATURE_ENTRY[feature] : undefined

  if (entry) {
    entry.apply?.()
    activeTab.value = entry.tab
    featureHint.value = `已进入「${entry.label}」，上传照片后点「开始处理」即可`
    return
  }

  const tab = typeof route.query.tab === 'string' ? route.query.tab : ''
  if (EDITOR_TABS.includes(tab)) {
    activeTab.value = tab
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
    // 轮询被取消（卸载 / 重新提交 / 主动取消）静默处理
    if (err?.name === 'ProcessCancelledError') return
    if (err.needPay) {
      ElMessage.warning('免费次数已用完，请联系管理员充值')
    }
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
  grid-template-columns: 1fr 360px;
  gap: 24px;
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

/* Compare Preview */
.preview-compare {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  width: 100%;
}

.compare-item {
  flex: 1;
  max-width: 300px;
  text-align: center;
}

.compare-label {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 500;
  color: #606266;
  margin-bottom: 12px;
}

.compare-img-wrap {
  width: 100%;
  aspect-ratio: 3/4;
  border-radius: 8px;
  overflow: hidden;
  background: #f5f7fa;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #ebeef5;
}

.compare-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.result-wrap {
  position: relative;
}

.processing-overlay {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.9);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #409eff;
}

.processing-overlay p {
  margin: 0;
  font-size: 14px;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.empty-result {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: #c0c4cc;
}

.empty-result p {
  margin: 0;
  font-size: 14px;
}

.compare-info {
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.compare-arrow {
  flex-shrink: 0;
  color: #dcdfe6;
}

.loading-icon {
  color: #409eff;
  animation: spin 1s linear infinite;
}

/* Preview Actions */
.preview-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid #ebeef5;
}

.login-prompt {
  margin-top: 20px;
}

/* ===== Settings Section ===== */
.settings-section {
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
}

.feature-hint {
  margin: 12px 16px 0;
  width: auto;
}

.settings-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 16px;
}

.settings-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.setting-group {
  padding: 20px;
  border-bottom: 1px solid #f0f2f5;
}

.setting-group:last-child {
  border-bottom: none;
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

/* ===== 自然微调：合规提示 + 原图/效果对比滑块 ===== */
.compliance-alert {
  margin-bottom: 12px;
  line-height: 1.5;
}

.beautify-switch {
  margin-top: 4px;
}

.compliance-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

.compare-slider-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 3 / 4;
  border-radius: 8px;
  overflow: hidden;
  background: #f5f7fa;
  user-select: none;
}

.compare-layer {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.compare-overlay {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  overflow: hidden;
}

.compare-overlay .compare-layer {
  /* 裁切层需与底图严格像素对齐，宽度锁定为容器宽度 */
  width: 100%;
  max-width: none;
}

.compare-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #fff;
  box-shadow: 0 0 6px rgba(0, 0, 0, 0.35);
  transform: translateX(-1px);
  pointer-events: none;
}

.compare-range {
  position: absolute;
  left: 0;
  bottom: 8px;
  width: 100%;
  margin: 0;
  z-index: 5;
  cursor: ew-resize;
}

.compare-tag {
  position: absolute;
  top: 8px;
  padding: 2px 8px;
  font-size: 12px;
  color: #fff;
  background: rgba(0, 0, 0, 0.45);
  border-radius: 10px;
  pointer-events: none;
}

.tag-left {
  left: 8px;
}

.tag-right {
  right: 8px;
}

/* ===== Responsive ===== */
@media (max-width: 1024px) {
  .editor-container {
    grid-template-columns: 1fr;
  }

  .preview-compare {
    flex-direction: column;
  }

  .compare-arrow {
    transform: rotate(90deg);
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
