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

        <!-- 出图状态（P0 可观测性）：美颜是否真的生效 + 后端质量提示。
             过去「0 张脸 / 模型缺失 / 蒙版过小」都只塞一条 warning，
             用户拿到「没美颜的图」却毫不知情；现在状态显式可见。 -->
        <div v-if="photoStore.processResult" class="result-status">
          <el-alert
            v-if="beautyStatus"
            :title="beautyStatus.title"
            :type="beautyStatus.type"
            :closable="false"
            show-icon
          >
            <template #default>
              <span>{{ beautyStatus.detail }}</span>
            </template>
          </el-alert>
          <el-alert
            v-if="resultWarnings.length"
            title="出图提示"
            type="warning"
            :closable="false"
            show-icon
          >
            <template #default>
              <ul class="warning-list">
                <li v-for="(w, i) in resultWarnings" :key="i">{{ w }}</li>
              </ul>
            </template>
          </el-alert>
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
                <span class="panel-title"><span class="step-num">1</span>证件照规格</span>
                <span class="panel-summary">{{ templateSummary }}</span>
              </template>

              <div class="template-grid">
                <div
                  v-for="tmpl in topTemplates"
                  :key="tmpl.id"
                  class="template-card"
                  :class="{ active: photoStore.selectedTemplate === tmpl.id }"
                  @click="photoStore.selectTemplate(tmpl.id)"
                >
                  <span class="tmpl-name">{{ tmpl.name }}</span>
                  <span class="tmpl-size">{{ tmpl.width_px }}×{{ tmpl.height_px }}</span>
                </div>
                <div class="template-card more-card" @click="showMoreSpecs = true">
                  <span class="tmpl-name">更多规格</span>
                  <span class="tmpl-size">{{ moreTemplates.length ? `${moreTemplates.length} 个可选` : '无更多' }}</span>
                </div>
                <div
                  v-if="!photoStore.selectedTemplate"
                  class="template-card custom-card active"
                  @click="photoStore.selectTemplate(null)"
                >
                  <span class="tmpl-name">自定义尺寸</span>
                  <span class="tmpl-size">手动输入</span>
                </div>
              </div>

              <el-dialog v-if="showMoreSpecs" title="更多规格" width="500" max-width="800">
                <div style="max-height: 300px; overflow: auto; padding: 12px;">
                  <p v-for="tmpl in moreTemplates" :key="tmpl.id">{{ tmpl.name }} ({{ tmpl.width_px }}×{{ tmpl.height_px }})</p>
                </div>
                <template #footer>
                  <el-button @click="showMoreSpecs = false">关闭</el-button>
                </template>
              </el-dialog>

              <div class="custom-size-panel" v-if="!photoStore.selectedTemplate">
                <div class="custom-size-inputs">
                  <el-input-number
                    v-model="photoStore.customWidth"
                    :min="0"
                    :max="8000"
                    size="small"
                    placeholder="宽"
                  />
                  <span class="x-mark">×</span>
                  <el-input-number
                    v-model="photoStore.customHeight"
                    :min="0"
                    :max="8000"
                    size="small"
                    placeholder="高"
                  />
                  <span class="unit">px</span>
                </div>
                <p class="setting-hint">像素单位，留空则保持原图尺寸</p>
              </div>

              <!-- 文件大小限制：很多报名网站对证件照体积有硬性要求 -->
              <div class="file-size-panel">
                <div class="file-size-row">
                  <label class="file-size-label">文件大小上限</label>
                  <el-input-number
                    v-model="photoStore.maxKb"
                    :min="0"
                    :max="10240"
                    :step="10"
                    size="small"
                    style="width: 110px"
                  />
                  <span class="unit">KB</span>
                </div>
                <div class="file-size-presets">
                  <el-button
                    v-for="preset in SIZE_PRESETS_KB"
                    :key="preset.value"
                    size="small"
                    :type="photoStore.maxKb === preset.value ? 'primary' : 'default'"
                    @click="photoStore.maxKb = preset.value"
                  >
                    {{ preset.label }}
                  </el-button>
                </div>
                <p class="setting-hint">
                  0 = 不额外限制（默认 ≤100KB）。很多报名网站要求 ≤200KB 或 20~100KB，
                  直接填公告里的上限即可；后端会在不超上限的前提下保持最高画质。
                </p>
              </div>
            </el-collapse-item>

            <!-- ② 背景颜色 -->
            <el-collapse-item name="bg">
              <template #title>
                <span class="panel-title"><span class="step-num">2</span>背景颜色</span>
                <span class="panel-summary">{{ getColorLabel(photoStore.selectedBgColor) }}</span>
              </template>

              <div class="bg-color-groups">
                <div
                  v-for="g in photoStore.groupedBgColors"
                  :key="g.group"
                  class="bg-color-group"
                >
                  <div class="bg-group-title">{{ g.group }}</div>
                  <div class="bg-color-grid">
                    <div
                      v-for="color in g.colors"
                      :key="color.name"
                      class="bg-color-item"
                      :class="{ active: photoStore.selectedBgColor === color.name }"
                      :title="colorTip(color)"
                      @click="photoStore.selectedBgColor = color.name"
                    >
                      <div class="color-swatch" :style="swatchStyle(color)"></div>
                      <span class="color-name">{{ getColorLabel(color.name) }}</span>
                    </div>
                  </div>
                </div>
              </div>
              <p class="setting-hint bg-usage-hint" v-if="currentColorUsage">
                {{ currentColorUsage }}
              </p>
              <div class="custom-bg-row" v-if="photoStore.selectedBgColor === 'custom'">
                <el-color-picker v-model="photoStore.customBgColor" size="small" />
                <span class="setting-hint">自定义背景色 {{ photoStore.customBgColor }}</span>
              </div>
            </el-collapse-item>

            <!-- ③ 自然微调（含美颜参数与对比说明） -->
            <el-collapse-item name="beautify">
              <template #title>
                <span class="panel-title"><span class="step-num">3</span>美颜</span>
                <span class="panel-summary">
                  {{ beautifySummary }}
                </span>
              </template>

              <el-alert
                title="证件照仅允许轻微肤色均匀化：不改五官比例、不改骨骼轮廓、不丢失面部特征"
                type="info"
                :closable="false"
                show-icon
                class="compliance-alert"
              />
              <!-- 美颜总开关：做成整行大卡，开关够大、状态一眼可见（用户反馈旧版开关太小找不到） -->
              <div
                class="beautify-master"
                :class="{ on: beautifyOn }"
                @click="beautifyOn = !beautifyOn"
              >
                <div class="bm-text">
                  <span class="bm-title">美颜微调</span>
                  <span class="bm-desc">{{ beautifyOn ? beautifySummary : '原图直出 · 不做任何修饰' }}</span>
                </div>
                <el-switch
                  v-model="beautifyOn"
                  size="large"
                  class="beautify-switch"
                  @click.stop
                />
              </div>

              <template v-if="beautifyOn">
                <!-- 强度滑杆.
                     【为什么去掉 marks】el-slider 的 marks 是 position:absolute +
                     margin-top:15px + translateX(-50%) 的绝对定位文字，既会溢出到
                     滑杆两侧（0% 的「原图」被裁掉一半），又会和下面的说明文字重叠
                     （截图里的压字就是它）。改成「标题行显示当前值 + 三个可点预设按钮」，
                     全部是正常文档流，几何上不可能再互相覆盖。 -->
                <div class="slider-block">
                  <div class="slider-head">
                    <h4 class="setting-title sub-title">美颜强度（还原度）</h4>
                    <span class="slider-value">{{ strengthPercent }}%</span>
                  </div>
                  <div class="slider-wrap">
                    <el-slider
                      v-model="strengthPercent"
                      :min="0"
                      :max="100"
                      :step="5"
                      :show-tooltip="true"
                    />
                  </div>
                  <div class="slider-presets">
                    <el-button
                      v-for="preset in STRENGTH_PRESETS"
                      :key="preset.value"
                      size="small"
                      :type="strengthPercent === preset.value ? 'primary' : 'default'"
                      @click="strengthPercent = preset.value"
                    >
                      {{ preset.label }}
                    </el-button>
                  </div>
                </div>
                <p class="compliance-note slider-note">
                  当前强度 {{ strengthPercent }}%。默认 100% 为「自然微调」的完整强度，
                  磨皮/提亮/匀肤仍由后端合规红线硬钳制（不会改变识别特征）；
                  0% = 完全还原原图，调低可进一步减轻修饰。
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

                <el-checkbox v-model="studioOn" :disabled="photoStore.idPhotoAlign">
                  影楼精修（强磨皮 + 匀肤 + 补光塑形 + 肤色白平衡 + 眼妆，仅非报名场景）
                </el-checkbox>
                <p v-if="studioOn" class="compliance-note">
                  精修档走独立的「影楼预算」：磨皮尺度放大到色块级、分区肤色预算加倍、
                  并额外做补光/塑形/去黄/眼白提净。仍然<b>不做任何几何改动</b>
                  （无瘦脸/大眼/改眼距/改下颌），但磨皮强度已超出证件照红线，
                  报名、护照、签证等场景请改用「自然微调」。
                </p>

                <el-checkbox v-model="artisticOn" :disabled="photoStore.idPhotoAlign || studioOn">
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
                <span class="panel-title"><span class="step-num">4</span>输出设置</span>
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
const showMoreSpecs = ref(false)

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
 * 仅展示高频规格（使用次数最多的前 4 个），其余通过「更多规格」弹窗进入。
 * 按使用次数（usage_count）降序排列；后端未返回 usage_count 时退回原顺序。
 */
const topTemplates = computed(() => {
  const sorted = [...photoStore.templates].sort((a, b) => (b.usage_count ?? 0) - (a.usage_count ?? 0))
  return sorted.slice(0, 4)
})

/**
 * 其余规格，供「更多规格」弹窗展示。
 */
const moreTemplates = computed(() => {
  const sorted = [...photoStore.templates].sort((a, b) => (b.usage_count ?? 0) - (a.usage_count ?? 0))
  return sorted.slice(4)
})

/**
 * 「自然微调」总开关。
 * 后端只接受数值档位（0=关闭，1=自然微调，4=艺术档），这里做布尔 ↔ 数值的双向映射，
 * 保证 UI 上只有一个开关，避免用户选到被红线钳制的 2/3 档。
 */
const beautifyOn = computed({
  get: () => photoStore.beautifyLevel > 0,
  set: (on: boolean) => {
    photoStore.beautifyLevel = on
      ? (photoStore.beautifyLevel === 4 || photoStore.beautifyLevel === 5
        ? photoStore.beautifyLevel
        : 1)
      : 0
  },
})

/**
 * 「影楼精修」档（后端 STUDIO=5）：仅非报名场景可用，与艺术档互斥。
 *
 * 【为什么单独一个开关而不是塞进档位下拉】档位是数字，用户看不懂 1/4/5；
 * 而「自然微调 / 艺术增强 / 影楼精修」是三种**使用场景**，用三个语义化开关
 * 表达最直接。互斥由这里的 setter 保证：打开精修自动关掉艺术档，反之亦然。
 * 证件照对齐（报名场景）开启时禁用，防止把超红线的档位用在报名照上。
 */
const studioOn = computed({
  get: () => photoStore.beautifyLevel === 5,
  set: (on: boolean) => {
    photoStore.beautifyLevel = on ? 5 : 1
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

/**
 * 出图后的美颜状态（P0）：把「静默降级」变成用户可见的显式提示。
 * 后端 beautify_report 给出 mode / iod / 蒙版覆盖 / skin_diff / 身份相似度，
 * 前端只负责呈现，不自己猜「效果好不好」。
 */
type AlertType = 'success' | 'warning' | 'info' | 'error'

const beautyStatus = computed<{ type: AlertType; title: string; detail: string } | null>(() => {
  const r = photoStore.processResult
  if (!r || !beautifyOn.value) return null
  const rep = r.beautify_report || {}
  // 老记录没有报告字段时不臆测，避免把「历史成功出图」误报成失败
  if (typeof rep.applied !== 'boolean') return null
  if (!rep.applied) {
    return {
      type: 'error',
      title: '美颜未生效',
      detail: `本次出图等同原图（${rep.reason || '未知原因'}）。建议换正面、清晰、脸部占比更大的照片后重试。`,
    }
  }
  const iod = Math.round(rep.iod_px || 0)
  const coverage = ((rep.mask_coverage || 0) * 100).toFixed(1)
  const sim = rep.identity_sim != null ? `，身份相似度 ${Number(rep.identity_sim).toFixed(3)}` : ''
  if (rep.mode === 'fallback') {
    return {
      type: 'warning',
      title: '美颜已降级',
      detail: `未检出人脸关键点，仅使用基础肤色蒙版（覆盖 ${coverage}%），修饰效果有限；建议换正面、清晰的照片${sim}。`,
    }
  }
  return {
    type: 'success',
    title: '美颜已生效',
    detail: `人脸识别正常：瞳距 ${iod}px、皮肤覆盖 ${coverage}%、皮肤区域平均改动 ${rep.skin_diff ?? 0} 级${sim}。`,
  }
})

/** 后端 warnings 去掉已在 beautyStatus 里说明的美颜/身份类，避免重复提示 */
const resultWarnings = computed(() =>
  (photoStore.processResult?.warnings || []).filter(
    (w) => !w.startsWith('美颜') && !w.startsWith('身份校验'),
  ),
)

/**
 * 美颜面板标题右侧的摘要：一眼看出当前是哪一档。
 * 【为什么要区分档位名】三个档位的观感差别很大（自然微调/艺术/影楼精修），
 * 只显示「已开启 · 强度 70%」会让用户以为换了档位只是强度变了。
 */
const beautifySummary = computed(() => {
  if (!beautifyOn.value) return '原图直出'
  const levelName = studioOn.value
    ? '影楼精修'
    : (artisticOn.value ? '艺术增强' : '自然微调')
  return `${levelName} · 强度 ${strengthPercent.value}%`
})

/**
 * 强度预设按钮.
 * 滑杆的 marks 在窄面板里很容易和说明文字抢位置，这里额外给三个可点按钮，
 * 用户不必精确拖拽也能一次到位（也是滑杆刻度的「文字版」）。
 */
const STRENGTH_PRESETS = [
  { label: '原图 0%', value: 0 },
  { label: '轻修 70%', value: 70 },
  { label: '标准 100%', value: 100 },
]

/** 文件大小上限预设（KB）：覆盖常见报名网站要求 */
const SIZE_PRESETS_KB = [
  { label: '不限', value: 0 },
  { label: '≤100KB', value: 100 },
  { label: '≤200KB', value: 200 },
  { label: '≤500KB', value: 500 },
]

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
  // ---- 白 ----
  white: '标准白',
  white_std: '标准白',
  pearl_white: '珍珠白',
  // ---- 蓝（标准色号，按报考公告挑）----
  blue: '标准天蓝',
  blue_std: '标准天蓝',
  blue_gov: '考公深蓝',
  blue_alt: '蓝色变体',
  blue_cyan: '青蓝',
  sky_light: '学历浅蓝',
  light_blue: '浅蓝',
  sky_blue: '天蓝',
  dark_blue: '护照深蓝',
  navy: '藏青',
  // ---- 红 ----
  red: '标准红',
  red_std: '正红',
  red_deep: '深红',
  red_crimson: '正深红',
  red_alt: '红色变体',
  red_dark: '暗红',
  wine_red: '酒红',
  maroon: '深红褐',
  // ---- 灰 ----
  gray_light: '浅灰',
  gray_mid: '中浅灰',
  gray_deep: '灰',
  gray: '中灰',
  light_gray: '浅灰（旧）',
  dark_gray: '深灰',
  // ---- 其他 ----
  green: '绿色',
  forest_green: '墨绿',
  mint_green: '薄荷绿',
  pink: '粉色',
  rose: '玫瑰粉',
  cream: '米色',
  champagne: '香槟米',
  teal: '青色',
  lavender: '淡紫',
  custom: '自定义',
}

function getColorLabel(name: string): string {
  return colorLabels[name] || name
}

/**
 * 色块 tooltip：优先用后端下发的「中文名 + HEX + 适用场景」。
 * 色板现在有 5 种蓝、5 种红，光看色块分辨不出 #438EDB 与 #0066CC，
 * 而报名公告里写的偏偏就是 HEX —— 所以每个色块必须把这三个信息带出来。
 */
function colorTip(color: { name: string; label?: string; hex?: string; usage?: string }): string {
  const name = getColorLabel(color.name)
  const parts = [name]
  if (color.hex) parts.push(color.hex)
  if (color.usage) parts.push(color.usage)
  return parts.join(' · ')
}

/** 当前选中色的适用场景说明（色板下方一行提示，避免用户猜） */
const currentColorUsage = computed(() => {
  const list = photoStore.availableBgColors as Array<{ name: string; usage?: string }>
  const hit = list.find((c) => c.name === photoStore.selectedBgColor)
  return hit?.usage || ''
})

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
    /**
     * 预计剩余时间估算。
     *
     * 【为什么不再用「初估 15 秒、每秒回落到 5 秒」】换 BiRefNet 后单张实测
     * 56~61 秒，端到端 70~120 秒。旧逻辑第 60 秒就开始显示「还需 5 秒」，
     * 然后一直卡在 5 秒不动 —— 用户会以为程序挂了，比不显示更糟。
     *
     * 新模型：分段估计，越接近真实耗时越保守，且永不显示负数。
     *   <20s → 60s（还没到主体耗时段，别给「马上好」的错觉）
     *   <45s → 30s
     *   <70s → 15s
     *   再久 → 10s 起步，每 10 秒加回 5 秒（进度因此始终在推进，不出现停滞感）
     */
    let eta: number
    if (sec < 20) eta = 60
    else if (sec < 45) eta = 30
    else if (sec < 70) eta = 15
    else eta = 10 + Math.floor((sec - 70) / 10) * 5
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
  grid-template-columns: 1fr 400px;
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

/* 出图状态（美颜生效报告 + 后端质量提示） */
.result-status {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
}

.result-status .warning-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
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

/* 步骤序号：让「规格 → 背景 → 美颜 → 输出」的处理流程一眼可见 */
.step-num {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
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

/* BG Colors：按色调分组（白/蓝/红/灰），每色标 HEX。
   原来 24 色挤 4 列、真正要点的白蓝红淹没在一堆色号里；现在只铺有明确
   使用场景的标准色号，分组 + 色号 + 场景说明，用户按报考公告对号入座。 */
.bg-color-groups {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.bg-group-title {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  margin-bottom: 6px;
  padding-left: 2px;
  letter-spacing: 0.5px;
}

.bg-color-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.bg-color-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  padding: 6px 2px;
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
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.bg-color-item.active .color-swatch {
  border-color: #409eff;
}

.color-name {
  font-size: 11px;
  color: #606266;
  line-height: 1.2;
  text-align: center;
  white-space: nowrap;
}

.color-hex {
  font-size: 10px;
  color: #a8abb2;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  line-height: 1;
}

.bg-usage-hint {
  margin-top: 8px;
  line-height: 1.5;
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

/* ===== 美颜总开关：整行大卡（用户反馈旧开关太小、找不到） ===== */
.beautify-master {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  margin-bottom: 14px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: #fafafa;
  cursor: pointer;
  transition: all 0.2s;
}
.beautify-master:hover {
  border-color: #c6d9f1;
}
.beautify-master.on {
  border-color: #409eff;
  background: #ecf5ff;
}
.bm-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.bm-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.bm-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}
.beautify-master.on .bm-desc {
  color: #409eff;
}

.beautify-switch {
  flex-shrink: 0;
}

/* 美颜选项 checkbox：去掉默认右间距、统一行高，避免长短不一挤在一起 */
.settings-collapse :deep(.el-checkbox) {
  margin-right: 0;
  height: auto;
  padding: 5px 0;
  line-height: 1.5;
}
.settings-collapse :deep(.el-checkbox__label) {
  line-height: 1.5;
}

.compliance-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

/* ===== 强度滑杆：三段独立排布，彻底避免刻度/数值/说明互相压字 =====
   旧问题：el-slider 的 marks 是绝对定位在滑杆下方 ~22px 处，
   紧跟其后的说明文字如果 margin 不够就会被刻度覆盖（截图里就是这种重叠）。
   现在把「标题+当前值」「滑杆+刻度」「预设按钮」「说明」拆成四个各自占位的块，
   滑杆块预留固定高度容纳刻度，说明文字再另起一段。 */
.slider-block {
  margin-top: 8px;
}

.slider-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.slider-head .sub-title {
  margin: 0;
}

.slider-value {
  font-size: 15px;
  font-weight: 700;
  color: #409eff;
  font-variant-numeric: tabular-nums;
}

.slider-wrap {
  /* el-slider 本体高 32px（按钮 20px + 上下留白）；没有 marks 了，
     只留一点左右内边距，避免 0%/100% 的按钮贴边被裁 */
  padding: 4px 6px;
}

.slider-presets {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.slider-presets .el-button + .el-button {
  margin-left: 0;
}

.slider-note {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #ebeef5;
}

/* ===== Template Grid：紧凑双列卡片 =====
   旧版每张卡「大图标+名称+尺寸」竖排 ~90px 高，6 张卡占掉右栏近半屏，
   把背景色/美颜全顶到视口外。现在去掉图标、名称+尺寸紧凑竖排，
   每张 ~48px，一屏内能看到全部高频规格。 */
.template-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.template-card {
  position: relative;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 9px 12px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  min-height: 48px;
}
.template-card:hover {
  border-color: #409eff;
  background: #f0f7ff;
}
.template-card.active {
  border-color: #409eff;
  background: #ecf5ff;
  box-shadow: inset 0 0 0 1px #409eff;
}
/* 选中角标：替代旧的大图标，不占纵向空间 */
.template-card.active::after {
  content: '✓';
  position: absolute;
  top: 4px;
  right: 8px;
  font-size: 12px;
  font-weight: 700;
  color: #409eff;
}
.template-card .tmpl-name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  line-height: 1.3;
}
.template-card .tmpl-size {
  font-size: 11px;
  color: #909399;
  font-variant-numeric: tabular-nums;
}
.more-card {
  border-style: dashed;
  color: #909399;
}
.more-card .tmpl-name {
  color: #909399;
}
.custom-card {
  border-color: #e4e7ed;
}

/* 自定义尺寸面板 */
.custom-size-panel {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
}
.custom-size-inputs {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.custom-size-inputs .x-mark {
  color: #909399;
  font-size: 16px;
}
.unit {
  font-size: 12px;
  color: #909399;
}

/* 文件大小限制面板 */
.file-size-panel {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
}
.file-size-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.file-size-label {
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
}
.file-size-presets {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.file-size-presets .el-button + .el-button {
  margin-left: 0;
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
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
