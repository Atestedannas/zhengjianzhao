import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  processPhoto,
  getTemplates,
  getSizePresets,
  getOutputFormats,
  getBgColors,
  getHistory,
  getProcessStatus,
  type TemplateItem,
  type SizePreset,
  type OutputFormat,
  type BgColor,
  type ProcessParams,
  type ProcessResult,
  type ProcessResultFields,
  type HistoryItem,
} from '@/api'
import { useAuthStore } from './auth'

// ===== 异步处理轮询参数 =====
/** 轮询间隔：1.5 秒 */
const POLL_INTERVAL_MS = 1500
/** 最大轮询次数：120 次（约 3 分钟） */
const POLL_MAX_ATTEMPTS = 120
/** 轮询超时文案 */
const POLL_TIMEOUT_MESSAGE = '处理时间较长，请稍后在历史记录中查看'

/** 后端处理失败 / 轮询超时：message 可直接展示给用户 */
export class ProcessPollError extends Error {
  /** true 表示轮询超时（不是后端处理失败） */
  readonly timeout: boolean

  constructor(message: string, timeout = false) {
    super(message)
    this.name = 'ProcessPollError'
    this.timeout = timeout
  }
}

/** 轮询被主动取消（组件卸载 / 重新提交 / 用户取消）：调用方应静默忽略 */
export class ProcessCancelledError extends Error {
  constructor() {
    super('已取消处理')
    this.name = 'ProcessCancelledError'
  }
}

/** 一次轮询的运行上下文：新提交或取消会让旧上下文失效 */
interface PollRun {
  cancelled: boolean
  timer: ReturnType<typeof setTimeout> | null
  wake: (() => void) | null
}

/** 当前正在进行的轮询（模块级，避免被 pinia 代理包装定时器） */
let currentRun: PollRun | null = null

/** 停止当前轮询并清掉定时器，允许被重复调用 */
function stopPollingRun() {
  const run = currentRun
  if (!run) return
  run.cancelled = true
  if (run.timer) {
    clearTimeout(run.timer)
    run.timer = null
  }
  if (run.wake) {
    const wake = run.wake
    run.wake = null
    wake()
  }
  currentRun = null
}

/** 可被取消立即打断的间隔等待 */
function waitInterval(run: PollRun): Promise<void> {
  return new Promise((resolve) => {
    run.wake = () => resolve()
    run.timer = setTimeout(() => {
      run.timer = null
      run.wake = null
      resolve()
    }, POLL_INTERVAL_MS)
  })
}

/** 状态接口 / 同步返回的字段 → store 内的 ProcessResult */
function toProcessResult(data: ProcessResultFields & { record_id: number }, freeUsed: boolean): ProcessResult {
  return {
    record_id: data.record_id,
    result_url: data.result_url || '',
    download_url: data.download_url || '',
    file_size_kb: data.file_size_kb ?? 0,
    pixels: data.pixels || '',
    dpi: data.dpi ?? 0,
    output_format: data.output_format || '',
    mime_type: data.mime_type || '',
    warnings: data.warnings || [],
    faces_detected: data.faces_detected ?? 0,
    processing_time_ms: data.processing_time_ms ?? 0,
    free_used: freeUsed,
    remaining_free_count: data.remaining_free_count ?? -1,
  }
}

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
    // 重新选图意味着放弃上一次处理，先停掉轮询
    cancelPolling()
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
    cancelPolling()
    originalImage.value = null
    originalFile.value = null
    processResult.value = null
  }

  /**
   * 停止轮询（组件卸载 / 重新提交 / 用户取消时调用）。
   * 会清掉定时器并让在跑的轮询静默结束，避免定时器泄漏和重复请求。
   */
  function cancelPolling() {
    const had = currentRun !== null
    stopPollingRun()
    if (had) {
      processing.value = false
    }
  }

  /** 成功分支：更新预览结果、免费次数并刷新历史记录（同步返回与轮询成功共用） */
  function applyProcessSuccess(result: ProcessResult): ProcessResult {
    processResult.value = result
    // 更新免费次数
    authStore.updateFreeCount(result.remaining_free_count)
    // 刷新历史记录
    loadHistory()
    return result
  }

  /** 轮询状态接口，直到 success / failed / 超时 */
  async function pollProcessStatus(
    recordId: number,
    freeUsed: boolean,
    run: PollRun,
  ): Promise<ProcessResult> {
    for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
      await waitInterval(run)
      if (run.cancelled) throw new ProcessCancelledError()

      const data = await getProcessStatus(recordId)
      if (run.cancelled) throw new ProcessCancelledError()

      // 状态接口也会带回最新次数（失败时后端已退回），先同步展示
      if (typeof data.remaining_free_count === 'number') {
        authStore.updateFreeCount(data.remaining_free_count)
      }

      if (data.status === 'success') {
        return applyProcessSuccess(toProcessResult(data, freeUsed))
      }

      if (data.status === 'failed') {
        loadHistory()
        throw new ProcessPollError(data.error || '处理失败，请重试')
      }
      // pending / processing：继续等待
    }

    // 超时：记录可能仍在处理，让用户去历史记录里看
    loadHistory()
    throw new ProcessPollError(POLL_TIMEOUT_MESSAGE, true)
  }

  /** 处理照片 */
  async function processCurrentPhoto(): Promise<ProcessResult> {
    if (!originalFile.value) {
      throw new Error('请先上传照片')
    }

    // 重新提交：先取消上一次轮询，避免重复请求
    cancelPolling()
    const run: PollRun = { cancelled: false, timer: null, wake: null }
    currentRun = run

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

      const submit = await processPhoto(originalFile.value, params)
      if (run.cancelled) throw new ProcessCancelledError()

      // 提交后后端已按次扣费，先同步剩余次数
      if (typeof submit.remaining_free_count === 'number') {
        authStore.updateFreeCount(submit.remaining_free_count)
      }

      // 兼容同步返回：没有 status 或已是 success 时直接走成功分支
      if (!submit.status || submit.status === 'success') {
        return applyProcessSuccess(toProcessResult(submit, submit.free_used ?? true))
      }

      // 异步：清掉上一次的结果，进入「处理中」加载态后开始轮询
      processResult.value = null
      return await pollProcessStatus(submit.record_id, submit.free_used ?? true, run)
    } finally {
      // 只有自己仍是当前轮询时才收尾，避免影响后续提交
      if (currentRun === run) {
        stopPollingRun()
        processing.value = false
      }
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
    cancelPolling,
    resetParams,
    selectTemplate,
  }
})
