<template>
  <view class="template-selector">
    <!-- 模式切换 -->
    <view class="mode-tabs">
      <view
        class="mode-tab"
        :class="{ active: !customMode }"
        @click="customMode = false"
      >预设规格</view>
      <view
        class="mode-tab"
        :class="{ active: customMode }"
        @click="customMode = true"
      >自定义</view>
    </view>

    <!-- 预设模板列表 -->
    <view v-if="!customMode" class="template-list">
      <view
        v-for="tpl in templates"
        :key="tpl.id"
        class="template-card"
        :class="{ selected: selectedId === tpl.id }"
        @click="selectTemplate(tpl)"
      >
        <view class="tpl-header">
          <text class="tpl-name">{{ tpl.name }}</text>
          <view v-if="selectedId === tpl.id" class="tpl-check">
            <text>✓</text>
          </view>
        </view>
        <view class="tpl-info">
          <text class="tpl-spec">{{ tpl.width_px }}x{{ tpl.height_px }}px</text>
          <text class="tpl-size">{{ tpl.min_kb }}-{{ tpl.max_kb }}KB</text>
        </view>
        <text class="tpl-desc" v-if="tpl.physical_size_mm">
          {{ tpl.physical_size_mm }} | {{ tpl.dpi }}DPI
        </text>
        <text class="tpl-remark" v-if="tpl.remark">{{ tpl.remark }}</text>
      </view>

      <view v-if="loading" class="loading-wrap">
        <view class="loading-spinner" />
        <text class="loading-text">加载模板中...</text>
      </view>
    </view>

    <!-- 自定义参数表单 -->
    <view v-if="customMode" class="custom-form">
      <!-- 尺寸调整 -->
      <view class="section-title">尺寸调整</view>
      <view class="form-row">
        <view class="form-item half">
          <text class="form-label">宽度 (px)</text>
          <u-input v-model="customParams.width" type="number" placeholder="如 413" />
        </view>
        <view class="form-item half">
          <text class="form-label">高度 (px)</text>
          <u-input v-model="customParams.height" type="number" placeholder="如 579" />
        </view>
      </view>

      <!-- 缩放模式 -->
      <view class="form-item">
        <text class="form-label">缩放模式</text>
        <view class="chip-group">
          <view
            v-for="mode in resizeModes"
            :key="mode.value"
            class="chip"
            :class="{ active: customParams.resizeMode === mode.value }"
            @click="customParams.resizeMode = mode.value"
          >{{ mode.label }}</view>
        </view>
      </view>

      <!-- 输出格式 -->
      <view class="form-item">
        <text class="form-label">输出格式</text>
        <view class="chip-group">
          <view
            v-for="fmt in outputFormats"
            :key="fmt.value"
            class="chip"
            :class="{ active: customParams.outputFormat === fmt.value }"
            @click="customParams.outputFormat = fmt.value"
          >{{ fmt.label }}</view>
        </view>
      </view>

      <view class="form-row">
        <view class="form-item half">
          <text class="form-label">DPI</text>
          <u-input v-model="customParams.dpi" type="number" placeholder="默认 350" />
        </view>
        <view class="form-item half">
          <text class="form-label">最小/最大 KB</text>
          <view class="kb-row">
            <u-input v-model="customParams.minKb" type="number" placeholder="0" style="flex:1" />
            <text style="margin: 0 8rpx; color: #999">-</text>
            <u-input v-model="customParams.maxKb" type="number" placeholder="100" style="flex:1" />
          </view>
        </view>
      </view>

      <!-- 背景色 -->
      <view class="section-title">背景色</view>
      <view class="color-options">
        <view
          v-for="color in colorOptions"
          :key="color.value"
          class="color-item"
          :class="{ active: customParams.bgColor === color.value }"
          @click="customParams.bgColor = color.value"
        >
          <view class="color-dot" :style="{ background: color.dotColor }" />
          <text class="color-label">{{ color.label }}</text>
        </view>
      </view>

      <!-- 美颜设置 -->
      <view class="section-title">美颜设置</view>
      <view class="form-item">
        <view class="slider-row">
          <text class="form-label">美颜等级</text>
          <text class="slider-value">{{ beautyLevelLabel }}</text>
        </view>
        <u-slider
          v-model="customParams.beautifyLevel"
          :min="0"
          :max="3"
          :step="1"
          :block-size="22"
          activeColor="#4F6EF7"
        />
      </view>
      <view class="form-row beauty-toggles">
        <view class="beauty-toggle" :class="{ active: customParams.beautifySmooth }" @click="customParams.beautifySmooth = !customParams.beautifySmooth">
          <text>磨皮</text>
        </view>
        <view class="beauty-toggle" :class="{ active: customParams.beautifyBrighten }" @click="customParams.beautifyBrighten = !customParams.beautifyBrighten">
          <text>提亮</text>
        </view>
        <view class="beauty-toggle" :class="{ active: customParams.beautifyBlemish }" @click="customParams.beautifyBlemish = !customParams.beautifyBlemish">
          <text>去瑕疵</text>
        </view>
      </view>

      <!-- 证件照选项 -->
      <view class="section-title">证件照选项</view>
      <view class="form-row">
        <view class="form-item half">
          <text class="form-label">性别</text>
          <view class="chip-group">
            <view class="chip" :class="{ active: customParams.gender === 'male' }" @click="customParams.gender = 'male'">男</view>
            <view class="chip" :class="{ active: customParams.gender === 'female' }" @click="customParams.gender = 'female'">女</view>
            <view class="chip" :class="{ active: !customParams.gender }" @click="customParams.gender = ''">不限</view>
          </view>
        </view>
        <view class="form-item half">
          <text class="form-label">人脸对齐</text>
          <u-switch v-model="customParams.idPhotoAlign" activeColor="#4F6EF7" />
        </view>
      </view>
    </view>

    <!-- 背景色选择（预设模式） -->
    <view v-if="!customMode && selectedId" class="bg-color-section">
      <text class="section-title">选择背景色</text>
      <view class="color-options">
        <view
          v-for="color in allowedColors"
          :key="color.value"
          class="color-item"
          :class="{ active: bgColor === color.value }"
          @click="bgColor = color.value"
        >
          <view class="color-dot" :style="{ background: color.dotColor }" />
          <text class="color-label">{{ color.label }}</text>
        </view>
      </view>

      <!-- 预设模式下的美颜 -->
      <view class="section-title" style="margin-top: 20rpx">美颜设置</view>
      <view class="form-item">
        <view class="slider-row">
          <text class="form-label">美颜等级</text>
          <text class="slider-value">{{ beautyLevelLabel }}</text>
        </view>
        <u-slider
          v-model="presetBeautifyLevel"
          :min="0"
          :max="3"
          :step="1"
          :block-size="22"
          activeColor="#4F6EF7"
        />
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, reactive, watch, onMounted, computed } from 'vue'
import { getTemplates } from '@/api/template'

const emit = defineEmits(['change'])

const customMode = ref(false)
const selectedId = ref(null)
const selectedTemplate = ref(null)
const loading = ref(false)
const templates = ref([])
const bgColor = ref('white')
const presetBeautifyLevel = ref(1)

const customParams = reactive({
  width: '',
  height: '',
  resizeMode: 'crop',
  dpi: '350',
  minKb: '0',
  maxKb: '100',
  bgColor: 'white',
  outputFormat: 'JPEG',
  beautifyLevel: 1,
  beautifySmooth: true,
  beautifyBrighten: true,
  beautifyBlemish: true,
  gender: '',
  idPhotoAlign: false,
  upscale: true,
})

const resizeModes = [
  { value: 'crop', label: '智能裁剪' },
  { value: 'exact', label: '精确缩放' },
  { value: 'fit', label: '等比留白' },
  { value: 'fill', label: '等比填充' },
  { value: 'by_width', label: '按宽度' },
  { value: 'by_height', label: '按高度' },
]

const outputFormats = [
  { value: 'JPEG', label: 'JPG' },
  { value: 'PNG', label: 'PNG' },
  { value: 'PDF', label: 'PDF' },
  { value: 'WEBP', label: 'WebP' },
  { value: 'BMP', label: 'BMP' },
]

const colorOptions = [
  { value: 'keep', label: '原色', dotColor: 'transparent' },
  { value: 'white', label: '白底', dotColor: '#FFFFFF' },
  { value: 'blue', label: '蓝底', dotColor: '#438EDB' },
  { value: 'red', label: '红底', dotColor: '#D52B1E' },
  { value: 'light_blue', label: '浅蓝', dotColor: '#ADD8E6' },
  { value: 'dark_blue', label: '深蓝', dotColor: '#193780' },
  { value: 'gray', label: '灰底', dotColor: '#C0C0C0' },
  { value: 'green', label: '绿底', dotColor: '#008000' },
  { value: 'pink', label: '粉底', dotColor: '#FFC0CB' },
  { value: 'navy', label: '藏青', dotColor: '#000080' },
  { value: 'maroon', label: '深红', dotColor: '#800000' },
  { value: 'sky_blue', label: '天蓝', dotColor: '#87CEEB' },
]

const beautyLevelLabel = computed(() => {
  const labels = { 0: '关闭', 1: '轻度', 2: '中度', 3: '高度' }
  return labels[customMode.value ? customParams.beautifyLevel : presetBeautifyLevel.value] || '轻度'
})

const allowedColors = computed(() => {
  if (!selectedTemplate.value) return []
  const allowed = selectedTemplate.value.allowed_bg_colors || ['white']
  return colorOptions.filter((c) => c.value === 'keep' || allowed.includes(c.value))
})

async function loadTemplates() {
  loading.value = true
  try {
    const res = await getTemplates()
    if (res.code === 200) {
      templates.value = res.data.templates || res.data || []
    }
  } catch {
    templates.value = [
      { id: 1, name: '教师招聘2寸', width_px: 413, height_px: 579, dpi: 350, min_kb: 0, max_kb: 100, allowed_bg_colors: ['blue', 'white'], physical_size_mm: '35×45mm', remark: '适用教师考编报名' },
      { id: 2, name: '征兵照片', width_px: 358, height_px: 441, dpi: 350, min_kb: 20, max_kb: 100, allowed_bg_colors: ['red', 'white', 'blue'], remark: '全国征兵网标准' },
      { id: 3, name: '公务员1寸', width_px: 295, height_px: 413, dpi: 350, min_kb: 20, max_kb: 45, allowed_bg_colors: ['white', 'blue'], physical_size_mm: '25×35mm', remark: '公务员/事业编报考' },
      { id: 4, name: '通用扫描件', width_px: 0, height_px: 0, dpi: 300, min_kb: 0, max_kb: 2048, allowed_bg_colors: ['keep'], remark: '不调整像素仅压缩至≤2MB' },
    ]
  } finally {
    loading.value = false
  }
}

function selectTemplate(tpl) {
  selectedId.value = tpl.id
  selectedTemplate.value = tpl
  bgColor.value = (tpl.allowed_bg_colors && tpl.allowed_bg_colors[0]) ? tpl.allowed_bg_colors[0] : 'white'
  emitChange()
}

function emitChange() {
  if (customMode.value) {
    emit('change', {
      mode: 'custom',
      params: {
        ...customParams,
        beautifyLevel: customParams.beautifyLevel,
        beautifySmooth: customParams.beautifySmooth,
        beautifyBrighten: customParams.beautifyBrighten,
        beautifyBlemish: customParams.beautifyBlemish,
        idPhotoAlign: customParams.idPhotoAlign,
        gender: customParams.gender || undefined,
        outputFormat: customParams.outputFormat,
        resizeMode: customParams.resizeMode,
      },
    })
  } else if (selectedTemplate.value) {
    emit('change', {
      mode: 'template',
      template: selectedTemplate.value,
      bgColor: bgColor.value,
      beautifyLevel: presetBeautifyLevel.value,
    })
  }
}

watch(customParams, () => { if (customMode.value) emitChange() }, { deep: true })
watch(bgColor, () => { if (!customMode.value && selectedId.value) emitChange() })
watch(presetBeautifyLevel, () => { if (!customMode.value && selectedId.value) emitChange() })
watch(customMode, () => { selectedId.value = null; selectedTemplate.value = null; emitChange() })

onMounted(() => { loadTemplates() })
</script>

<style lang="scss" scoped>
.template-selector { padding: 0; }

.mode-tabs {
  display: flex; background: #F1F5F9; border-radius: $radius-md;
  padding: 4rpx; margin-bottom: $spacing-md;
}
.mode-tab {
  flex: 1; text-align: center; padding: 16rpx 0; font-size: 28rpx;
  color: $text-secondary; border-radius: $radius-sm; transition: all 0.2s;
  font-weight: 500;
  &.active { background: #fff; color: $brand-primary; font-weight: 700; box-shadow: $shadow-sm; }
}

.template-list { display: flex; flex-wrap: wrap; gap: $spacing-sm; }
.template-card {
  width: calc(50% - 8rpx); background: #F8FAFC; border-radius: $radius-lg;
  padding: $spacing-md; border: 2rpx solid transparent; transition: all 0.2s;
  position: relative;
  &.selected {
    border-color: $brand-primary;
    background: linear-gradient(135deg, rgba(79, 110, 247, 0.04), rgba(123, 92, 247, 0.04));
    box-shadow: 0 0 0 1rpx $brand-primary;
  }
}
.tpl-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: $spacing-xs; }
.tpl-name { font-size: 28rpx; font-weight: 700; color: $text-primary; }
.tpl-check {
  width: 36rpx; height: 36rpx; border-radius: 50%; background: $brand-gradient;
  color: #fff; font-size: 20rpx; display: flex; align-items: center; justify-content: center;
  font-weight: 700;
}
.tpl-info { display: flex; gap: $spacing-sm; margin-bottom: 4rpx; }
.tpl-spec { font-size: 24rpx; color: $brand-primary; font-weight: 600; }
.tpl-size { font-size: 24rpx; color: $text-hint; }
.tpl-desc { font-size: 22rpx; color: $text-secondary; display: block; }
.tpl-remark { font-size: 22rpx; color: $u-warning; margin-top: 4rpx; display: block; font-weight: 500; }

.loading-wrap { width: 100%; display: flex; flex-direction: column; align-items: center; padding: $spacing-xl; gap: $spacing-sm; }
.loading-spinner {
  width: 40rpx; height: 40rpx; border-radius: 50%;
  border: 3rpx solid $border-color; border-top-color: $brand-primary;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.loading-text { font-size: 26rpx; color: $text-hint; }

.custom-form { background: #F8FAFC; border-radius: $radius-lg; padding: $spacing-md; }
.section-title { display: block; font-size: 28rpx; font-weight: 700; color: $text-primary; margin: 24rpx 0 $spacing-sm; padding-bottom: 8rpx; border-bottom: 2rpx solid #E2E8F0; }
.section-title:first-child { margin-top: 0; }

.form-item { margin-bottom: $spacing-md; &.half { flex: 1; margin-bottom: 0; } }
.form-label { display: block; font-size: 26rpx; color: $text-primary; margin-bottom: $spacing-xs; font-weight: 600; }
.form-row { display: flex; gap: $spacing-md; margin-bottom: $spacing-md; }

.kb-row { display: flex; align-items: center; }

.chip-group { display: flex; gap: 12rpx; flex-wrap: wrap; }
.chip {
  padding: 10rpx 22rpx; border-radius: 32rpx; font-size: 24rpx; color: $text-secondary;
  background: #fff; transition: all 0.2s; border: 2rpx solid $border-color;
  font-weight: 500;
  &.active { background: $brand-primary; color: #fff; font-weight: 600; border-color: $brand-primary; }
}

.color-options { display: flex; gap: $spacing-sm; flex-wrap: wrap; }
.color-item {
  display: flex; flex-direction: column; align-items: center; gap: 6rpx;
  padding: 12rpx 14rpx; border-radius: $radius-sm; border: 2rpx solid $border-color; transition: all 0.2s;
  background: #fff;
  &.active { border-color: $brand-primary; background: rgba(79, 110, 247, 0.04); }
}
.color-dot { width: 40rpx; height: 40rpx; border-radius: $radius-round; border: 2rpx solid $border-color; }
.color-label { font-size: 20rpx; color: $text-secondary; font-weight: 500; }

.slider-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4rpx; }
.slider-value { font-size: 24rpx; color: $brand-primary; font-weight: 700; }

.beauty-toggles { justify-content: space-around; }
.beauty-toggle {
  flex: 1; text-align: center; padding: 14rpx 0; border-radius: 10rpx;
  font-size: 24rpx; color: $text-secondary; background: #fff; transition: all 0.2s;
  border: 2rpx solid $border-color; font-weight: 500;
  &.active { background: rgba(79, 110, 247, 0.08); color: $brand-primary; font-weight: 700; border-color: $brand-primary; }
}

.bg-color-section { margin-top: $spacing-md; }
</style>