import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  processPhoto,
  getTemplates,
  getSizePresets,
  getOutputFormats,
  getBgColors,
  getHistory,
  type TemplateItem,
  type SizePreset,
  type OutputFormat,
  type BgColor,
  type ProcessParams,
  type ProcessResult,
  type HistoryItem,
} from '@/api'
import { useAuthStore } from './auth'

export const usePhotoStore = defineStore('photo', () => {
  const authStore = useAuthStore()

  // ===== 状态 =====
  const templates = ref<TemplateItem[]>([])
  const sizePresets = ref<SizePreset[]>([])
  const outputFormats = ref<OutputFormat[]>([])
  const bgColors = ref<BgColor[]>([])
  const historyList = ref<HistoryItem[]>([])

  const originalImage = ref<string | null>(null)
  const originalFile = ref<File | null>(null)
  const processing = ref(false)
  const processResult = ref<ProcessResult | null>(null)

  // 当前选中的参数
  const selectedTemplate = ref<number | null>(null)
  const selectedBgColor = ref('white')
  // 0=关闭原图直出, 1=自然微调（唯一对外档位，后端强制合规红线）
  const beautifyLevel = ref(1)
  const beautifyOptions = ref({
    smooth: true,
    brighten: true,
    blemish: true,
  })
  const idPhotoAlign = ref(false)
  const gender = ref<'male' | 'female' | ''>('')
  const outputFormat = ref('JPEG')
  const dpi = ref(300)
  const customWidth = ref(0)
  const customHeight = ref(0)
  const resizeMode = ref('crop')

  // ===== 计算属性 =====
  const currentTemplate = computed(() => {
    if (!selectedTemplate.value) return null
    return templates.value.find((t) => t.id === selectedTemplate.value) || null
  })

  const availableBgColors = computed(() => {
    if (currentTemplate.value) {
      const allowed = currentTemplate.value.allowed_bg_colors
      return bgColors.value.filter((c) => allowed.includes(c.name))
    }
    return bgColors.value
  })

  // ===== 方法 =====

  /** 加载模板列表 */
  async function loadTemplates() {
    if (templates.value.length > 0) return
    try {
      const result = await getTemplates()
      templates.value = result.items
    } catch {
      // ignore
    }
  }

  /** 加载尺寸预设 */
  async function loadSizePresets() {
    if (sizePresets.value.length > 0) return
    try {
      const result = await getSizePresets()
      sizePresets.value = result.presets
    } catch {
      // ignore
    }
  }

  /** 加载输出格式 */
  async function loadOutputFormats() {
    if (outputFormats.value.length > 0) return
    try {
      const result = await getOutputFormats()
      outputFormats.value = result.formats
    } catch {
      // ignore
    }
  }

  /** 加载背景色 */
  async function loadBgColors() {
    if (bgColors.value.length > 0) return
    try {
      const result = await getBgColors()
      bgColors.value = result.colors
    } catch {
      // 预设默认背景色
      bgColors.value = [
        { name: 'white', rgb: [255, 255, 255] },
        { name: 'blue', rgb: [43, 117, 217] },
        { name: 'red', rgb: [217, 43, 43] },
        { name: 'gray', rgb: [128, 128, 128] },
        { name: 'black', rgb: [0, 0, 0] },
      ]
    }
  }

  /** 加载历史记录 */
  async function loadHistory() {
    try {
      const result = await getHistory()
      historyList.value = result.items
    } catch {
      // ignore
    }
  }

  /** 设置原始图片 */
  function setOriginalImage(file: File) {
    originalFile.value = file
    const reader = new FileReader()
    reader.onload = (e) => {
      originalImage.value = e.target?.result as string
    }
    reader.readAsDataURL(file)
    processResult.value = null
  }

  /** 清除图片 */
  function clearImage() {
    originalImage.value = null
    originalFile.value = null
    processResult.value = null
  }

  /** 处理照片 */
  async function processCurrentPhoto(): Promise<ProcessResult> {
    if (!originalFile.value) {
      throw new Error('请先上传照片')
    }

    processing.value = true
    try {
      const params: ProcessParams = {
        template_id: selectedTemplate.value || undefined,
        width: customWidth.value || undefined,
        height: customHeight.value || undefined,
        resize_mode: resizeMode.value,
        upscale: true,
        dpi: dpi.value,
        bg_color: selectedBgColor.value,
        output_format: outputFormat.value,
        beautify_level: beautifyLevel.value,
        beautify_smooth: beautifyOptions.value.smooth,
        beautify_brighten: beautifyOptions.value.brighten,
        beautify_blemish: beautifyOptions.value.blemish,
        id_photo_align: idPhotoAlign.value,
        gender: gender.value || undefined,
      }

      const result = await processPhoto(originalFile.value, params)
      processResult.value = result

      // 更新免费次数
      authStore.updateFreeCount(result.remaining_free_count)

      // 刷新历史记录
      loadHistory()

      return result
    } finally {
      processing.value = false
    }
  }

  /** 重置参数 */
  function resetParams() {
    selectedTemplate.value = null
    selectedBgColor.value = 'white'
    beautifyLevel.value = 1
    beautifyOptions.value = {
      smooth: true,
      brighten: true,
      blemish: true,
    }
    idPhotoAlign.value = false
    gender.value = ''
    outputFormat.value = 'JPEG'
    dpi.value = 300
    customWidth.value = 0
    customHeight.value = 0
    resizeMode.value = 'crop'
  }

  /** 选择模板 */
  function selectTemplate(templateId: number | null) {
    selectedTemplate.value = templateId
    if (templateId && currentTemplate.value) {
      const tmpl = currentTemplate.value
      dpi.value = tmpl.dpi
      outputFormat.value = tmpl.output_format
      customWidth.value = tmpl.width_px
      customHeight.value = tmpl.height_px
      // 设置默认背景色为模板允许的第一个
      const allowed = tmpl.allowed_bg_colors
      if (allowed && allowed.length > 0) {
        selectedBgColor.value = allowed[0]
      }
    }
  }

  return {
    // 状态
    templates,
    sizePresets,
    outputFormats,
    bgColors,
    historyList,
    originalImage,
    originalFile,
    processing,
    processResult,
    selectedTemplate,
    selectedBgColor,
    beautifyLevel,
    beautifyOptions,
    idPhotoAlign,
    gender,
    outputFormat,
    dpi,
    customWidth,
    customHeight,
    resizeMode,
    // 计算属性
    currentTemplate,
    availableBgColors,
    // 方法
    loadTemplates,
    loadSizePresets,
    loadOutputFormats,
    loadBgColors,
    loadHistory,
    setOriginalImage,
    clearImage,
    processCurrentPhoto,
    resetParams,
    selectTemplate,
  }
})
