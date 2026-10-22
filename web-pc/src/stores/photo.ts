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
/**
 * 最大轮询次数：120 次（约 3 分钟）。
 * 注意这是「后端兜底判失败（PROCESS_STUCK_MINUTES=15）」之前的客户端上限，
 * 真正让按钮恢复的是下面的 HARD_TIMEOUT_MS —— 用户不能对着转圈干等 3 分钟。
 */
const POLL_MAX_ATTEMPTS = 120
/** 轮询超时文案 */
const POLL_TIMEOUT_MESSAGE = '处理时间较长，请稍后在历史记录中查看'

// ===== 「开始处理」卡死的三道保险 =====
// 【历史问题】用户反馈点「开始处理」后按钮一直转圈，无法进行下一步。
// 根因是 loading 状态只依赖「请求/轮询自己结束」：只要有一个请求挂住
// （后端排队、nginx 超时、token 刷新死循环），processing 就永远为 true。
// 现在用三道互相独立的保险，任何一道生效都能把按钮恢复：
//   1) 单次请求超时：提交 30s / 查询状态 10s —— 挂住的请求一定会 reject；
//   2) 客户端硬超时：HARD_TIMEOUT_MS 到点强制解除 loading 并给出可重试文案；
//   3) 用户主动取消：cancelProcess() 立刻停轮询 + 恢复按钮。
/** 单次「提交处理」请求超时（上传大图 + 入队，给足 30 秒） */
const SUBMIT_TIMEOUT_MS = 30_000
/** 单次「查询状态」请求超时（状态接口很轻，10 秒不返回就是链路有问题） */
const STATUS_TIMEOUT_MS = 10_000
/**
 * 客户端硬超时：超过它一定解除 loading。
 * 取 90 秒：正常出图 2~15 秒，抠图 + 美颜 + 压缩的慢机器也能在 60 秒内完成；
 * 再久就是异常，必须让用户拿到「可以重试」的确定状态，而不是无限转圈。
 */
const HARD_TIMEOUT_MS = 90_000
/** 硬超时文案 */
const HARD_TIMEOUT_MESSAGE = '处理超时（超过 90 秒未完成），已停止等待，请重试'

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

/** 硬超时触发：不是后端失败，而是客户端不再等（按钮必须恢复可用） */
export class ProcessTimeoutError extends Error {
  constructor(message: string = HARD_TIMEOUT_MESSAGE) {
    super(message)
    this.name = 'ProcessTimeoutError'
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
  /** 硬超时定时器：到点强制结束等待并解除 loading */
  deadline: ReturnType<typeof setTimeout> | null
  /** 开始时间（用于展示已等待秒数） */
  startedAt: number
  /** 硬超时是否已经触发（避免与正常结束互相覆盖） */
  timedOut: boolean
}

/** 当前正在进行的轮询（模块级，避免被 pinia 代理包装定时器） */
let currentRun: PollRun | null = null

/** 停止当前轮询并清掉定时器（含硬超时定时器），允许被重复调用 */
function stopPollingRun() {
  const run = currentRun
  if (!run) return
  run.cancelled = true
  if (run.timer) {
    clearTimeout(run.timer)
    run.timer = null
  }
  if (run.deadline) {
    clearTimeout(run.deadline)
    run.deadline = null
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

/**
 * 给请求加「单次超时」：到点必定 reject，绝不无限挂起.
 *
 * 【为什么不用 axios 的 timeout】全局实例的 timeout=60s 是给所有接口用的，
 * 处理接口一旦卡住就是 60 秒，用户对着转圈没有任何反馈。这里给提交/查询
 * 分别设更短的上限，并用 Promise.race 保证**即使底层请求不响应**
 * （DNS 挂起、连接半开、token 刷新死循环）也能在到点时 reject，
 * loading 状态必然被解除。
 */
function withTimeout<T>(p: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      reject(new ProcessTimeoutError(message))
    }, ms)
    p.then(
      (v) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve(v)
      },
      (e) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        reject(e)
      },
    )
  })
}

/** 状态接口 / 同步返回的字段 → store 内的 ProcessResult */
function toProcessResult(data: ProcessResultFields & { record_id: number }, freeUsed: boolean): ProcessResult {
  return {
    record_id: data.record_id,
    result_url: data.result_url || '',
    download_url: data.download_url || '',
    // 对比基线：与效果图同裁剪/同缩放/同像素的原图；为空表示后端没给（老记录）
    baseline_url: data.baseline_url || '',
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
  /** 处理阶段的文案（给用户「现在在做什么」的确定反馈，而不是空转圈） */
  const processingStage = ref('')
  /** 已等待秒数（前端自己数，不依赖后端进度接口） */
  const processingElapsed = ref(0)
  /** 预计剩余秒数（按阶段 + 已用时间估算，仅用于安抚，不做精确承诺） */
  const processingEta = ref(0)

  // 当前选中的参数
  const selectedTemplate = ref<number | null>(null)
  // 默认「保留原背景」（keep）：原图蓝底/白底本身合规，只在用户主动选择时才换底，
  // 避免不必要的抠图白边与质感损失（用户实测反馈）
  const selectedBgColor = ref('keep')
  // 自定义背景色（取色器）：选中「自定义」色板时生效，最终以 custom:#RRGGBB 发给后端
  const customBgColor = ref('#409EFF')
  // 0=关闭原图直出, 1=自然微调（证件照默认档，后端强制合规红线）, 4=艺术档（仅非证件照）
  const beautifyLevel = ref(1)
  // 「还原度」滑杆：0~1，1=完整档位强度，0.7=轻修（默认，以真实为主），0=原图
  const retouchStrength = ref(0.7)
  const beautifyOptions = ref({
    smooth: true,
    brighten: true,
    blemish: true,
    // 分区域处理系统三开关（后端默认全开；关掉即退回整脸滤镜的旧行为）
    tone: true,
    texture: true,
    feature: true,
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
      // 模板模式：预置允许色 + 常驻「自定义」入口
      return [
        ...bgColors.value.filter((c) => allowed.includes(c.name)),
        { name: 'custom', rgb: [-1, -1, -1] },
      ]
    }
    return bgColors.value
  })

  /** 实际发给后端的 bg_color：选中自定义时组装为 custom:#RRGGBB */
  const effectiveBgColor = computed(() => {
    if (selectedBgColor.value === 'custom') {
      return `custom:${customBgColor.value}`
    }
    return selectedBgColor.value
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
        { name: 'keep', rgb: [-1, -1, -1] },
        { name: 'white', rgb: [255, 255, 255] },
        { name: 'pearl_white', rgb: [245, 245, 245] },
        { name: 'blue', rgb: [67, 142, 219] },
        { name: 'dark_blue', rgb: [25, 55, 128] },
        { name: 'light_blue', rgb: [173, 216, 230] },
        { name: 'sky_blue', rgb: [135, 206, 235] },
        { name: 'red', rgb: [213, 43, 30] },
        { name: 'wine_red', rgb: [158, 31, 53] },
        { name: 'maroon', rgb: [128, 0, 0] },
        { name: 'gray', rgb: [192, 192, 192] },
        { name: 'light_gray', rgb: [220, 220, 220] },
        { name: 'dark_gray', rgb: [96, 96, 96] },
        { name: 'green', rgb: [0, 128, 0] },
        { name: 'forest_green', rgb: [34, 85, 51] },
        { name: 'mint_green', rgb: [199, 237, 204] },
        { name: 'pink', rgb: [255, 192, 203] },
        { name: 'rose', rgb: [255, 228, 225] },
        { name: 'cream', rgb: [255, 253, 208] },
        { name: 'champagne', rgb: [247, 231, 206] },
        { name: 'navy', rgb: [0, 0, 128] },
        { name: 'teal', rgb: [0, 128, 128] },
        { name: 'lavender', rgb: [230, 230, 250] },
        { name: 'custom', rgb: [-1, -1, -1] },
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
      processingStage.value = ''
      processingElapsed.value = 0
      processingEta.value = 0
    }
  }

  /** 用户主动取消：立刻停轮询 + 恢复按钮（不必等后端任务结束） */
  function cancelProcess() {
    cancelPolling()
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
      if (run.cancelled) throw new ProcessCancelledError()
      if (run.timedOut) throw new ProcessTimeoutError()

      await waitInterval(run)
      if (run.cancelled) throw new ProcessCancelledError()
      if (run.timedOut) throw new ProcessTimeoutError()

      // 单次查询也要有超时：链路卡住时不能把整轮等待一起拖死
      const data = await withTimeout(
        getProcessStatus(recordId),
        STATUS_TIMEOUT_MS,
        '查询处理状态超时，请重试',
      )
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

    const run: PollRun = {
      cancelled: false,
      timer: null,
      wake: null,
      deadline: null,
      startedAt: Date.now(),
      timedOut: false,
    }
    currentRun = run

    processing.value = true
    processingStage.value = '正在上传照片…'
    processingElapsed.value = 0
    processingEta.value = 15

    // 【第二道保险】客户端硬超时：到点强制结束等待并解除 loading。
    // 它不依赖任何请求回调 —— 哪怕提交请求本身永远不返回，90 秒后按钮也会恢复。
    run.deadline = setTimeout(() => {
      if (run.cancelled) return
      run.timedOut = true
      // 立刻唤醒正在 waitInterval 的等待，让 for 循环尽快退出
      if (run.wake) {
        const wake = run.wake
        run.wake = null
        wake()
      }
      processing.value = false
      processingStage.value = ''
    }, HARD_TIMEOUT_MS)

    try {
      const params: ProcessParams = {
        template_id: selectedTemplate.value || undefined,
        width: customWidth.value || undefined,
        height: customHeight.value || undefined,
        resize_mode: resizeMode.value,
        upscale: true,
        dpi: dpi.value,
        bg_color: effectiveBgColor.value,
        output_format: outputFormat.value,
        beautify_level: beautifyLevel.value,
        beautify_smooth: beautifyOptions.value.smooth,
        beautify_brighten: beautifyOptions.value.brighten,
        beautify_blemish: beautifyOptions.value.blemish,
        beautify_tone: beautifyOptions.value.tone,
        beautify_texture: beautifyOptions.value.texture,
        beautify_feature: beautifyOptions.value.feature,
        retouch_strength: retouchStrength.value,
        id_photo_align: idPhotoAlign.value,
        gender: gender.value || undefined,
      }

      // 【第一道保险】提交请求单独设 30 秒上限
      const submit = await withTimeout(
        processPhoto(originalFile.value, params),
        SUBMIT_TIMEOUT_MS,
        '提交超时（30 秒未响应），请检查网络后重试',
      )
      if (run.cancelled) throw new ProcessCancelledError()
      if (run.timedOut) throw new ProcessTimeoutError()

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
      processingStage.value = 'AI 正在处理：人脸解析 → 分区修饰 → 背景合成…'
      return await pollProcessStatus(submit.record_id, submit.free_used ?? true, run)
    } finally {
      // 只有自己仍是当前轮询时才收尾，避免影响后续提交
      if (currentRun === run) {
        stopPollingRun()
        processing.value = false
        processingStage.value = ''
        processingElapsed.value = 0
        processingEta.value = 0
      }
    }
  }

  /** 重置参数 */
  function resetParams() {
    selectedTemplate.value = null
    selectedBgColor.value = 'white'
    beautifyLevel.value = 1
  retouchStrength.value = 0.7
    beautifyOptions.value = {
      smooth: true,
      brighten: true,
      blemish: true,
      tone: true,
      texture: true,
      feature: true,
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
    processingStage,
    processingElapsed,
    processingEta,
    processResult,
    selectedTemplate,
    selectedBgColor,
    customBgColor,
    effectiveBgColor,
    beautifyLevel,
    retouchStrength,
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
    cancelProcess,
    resetParams,
    selectTemplate,
  }
})
