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
 * 最大轮询次数：180 次（1.5s × 180 = 270 秒）。
 * 必须**大于** HARD_TIMEOUT_MS（240s），否则轮询先放弃、硬超时永远不触发，
 * 用户看到的是「处理时间较长」而不是准确的「超时」文案。
 * 也要小于后端兜底判失败的 PROCESS_STUCK_MINUTES=15，避免和它抢结论。
 */
const POLL_MAX_ATTEMPTS = 180
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
 *
 * 【为什么从 90 秒放宽到 240 秒】线上抠图引擎从 u2net（320×320）切到了
 * BiRefNet（1024×1024），质量换时间：实测单张 1680×2240 抠图耗时 56~61 秒，
 * 加上排队、美颜、压缩与 JPEG 编码，端到端 70~120 秒是常态。
 * 90 秒会直接把正常出图判成「超时」——用户看到报错但后台其实已成功，
 * 还会白白消耗一次次数。放宽到 240 秒，配合后端 nginx 的 300 秒读超时，
 * 保证「客户端不会先于服务端放弃」。
 *
 * 注意必须与 nginx proxy_read_timeout（300s）保持 客户端 < 服务端，
 * 否则网络慢时前端先断开、后端仍在跑，用户体验最差（报错但扣次数）。
 */
const HARD_TIMEOUT_MS = 240_000
/** 硬超时文案 */
const HARD_TIMEOUT_MESSAGE = '处理超时（超过 4 分钟未完成），已停止等待，请重试'

/**
 * 证件照常用背景色（面板默认展示这批）.
 *
 * 【为什么从「白蓝红 3 个」扩到 14 个】上一版把面板砍到只剩白/蓝/红，
 * 理由是「证件照 99% 就是这三个色」。但用户实打实的反馈是：报名公告里写的是
 * 「蓝底 #0066CC」这种精确色号，白蓝红只给一个近似色，用户没法对照。
 * 现在按《证件照常用背景色 RGB 参考》收录有明确使用场景的标准色号：
 *   白  #FFFFFF
 *   蓝  #438EDB（通用天蓝）/ #0066CC（考公深蓝）/ #1D8DD5 / #00BFF3 / #64C5FF（学历浅蓝）
 *   红  #FF0000（正红）/ #DC143C（深红）/ #CC0000（正深红）/ #CE0000 / #950208（暗红）
 *   灰  #F0F0F0（签证/职称）/ #E6E6E6 / #D3D3D3
 * 顺序即面板顺序（白→蓝→红→灰），组头由后端 group 字段给出。
 * 后端还有 30+ 扩展色，不在 UI 铺开：那是「功能列表」不是「可选答案」。
 */
const PRIMARY_BG_COLORS = [
  'white',
  'blue_std', 'blue_gov', 'blue_alt', 'blue_cyan', 'sky_light',
  'red_std', 'red_deep', 'red_crimson', 'red_alt', 'red_dark',
  'gray_light', 'gray_mid', 'gray_deep',
]

/** 机型兜底色板（后端 /bg-colors 不可达时用，色值必须与后端一致） */
const FALLBACK_BG_COLORS: BgColor[] = [
  { name: 'keep', rgb: [-1, -1, -1], group: '其他', label: '保留原背景', hex: '' },
  { name: 'white', rgb: [255, 255, 255], group: '白', label: '标准白', hex: '#FFFFFF' },
  { name: 'blue_std', rgb: [67, 142, 219], group: '蓝', label: '标准天蓝', hex: '#438EDB' },
  { name: 'blue_gov', rgb: [0, 102, 204], group: '蓝', label: '考公深蓝', hex: '#0066CC' },
  { name: 'blue_alt', rgb: [29, 141, 213], group: '蓝', label: '蓝色变体', hex: '#1D8DD5' },
  { name: 'blue_cyan', rgb: [0, 191, 243], group: '蓝', label: '青蓝', hex: '#00BFF3' },
  { name: 'sky_light', rgb: [100, 197, 255], group: '蓝', label: '学历浅蓝', hex: '#64C5FF' },
  { name: 'red_std', rgb: [255, 0, 0], group: '红', label: '正红', hex: '#FF0000' },
  { name: 'red_deep', rgb: [220, 20, 60], group: '红', label: '深红', hex: '#DC143C' },
  { name: 'red_crimson', rgb: [204, 0, 0], group: '红', label: '正深红', hex: '#CC0000' },
  { name: 'red_alt', rgb: [206, 0, 0], group: '红', label: '红色变体', hex: '#CE0000' },
  { name: 'red_dark', rgb: [149, 2, 8], group: '红', label: '暗红', hex: '#950208' },
  { name: 'gray_light', rgb: [240, 240, 240], group: '灰', label: '浅灰', hex: '#F0F0F0' },
  { name: 'gray_mid', rgb: [230, 230, 230], group: '灰', label: '中浅灰', hex: '#E6E6E6' },
  { name: 'gray_deep', rgb: [211, 211, 211], group: '灰', label: '灰', hex: '#D3D3D3' },
  { name: 'custom', rgb: [-1, -1, -1], group: '其他', label: '自定义', hex: '' },
]

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
  // 0=关闭原图直出, 1=自然微调（证件照默认档，后端强制合规红线）,
  // 4=艺术档（仅非证件照）, 5=影楼精修（强磨皮/匀肤/塑形/白净，仅非报名场景）
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

  /**
   * 用户实际会用到的背景色（标准色号 + 保留原背景 + 自定义），按色调分组.
   *
   * 【为什么不把后端 30+ 色全铺】全量是「功能列表」而不是「可选答案」。
   * 这里只取 PRIMARY_BG_COLORS（有明确使用场景的标准色号，见文件顶部注释），
   * 再按后端 group 字段切成 白/蓝/红/灰 四组返回，前端画组头 + HEX + 场景说明。
   *
   * 【模板模式怎么过滤】模板里存的是「组级意图」（如 allowed=['blue','white']），
   * 而面板里给了 5 种蓝。所以按**分组**过滤而不是字面色名：
   * 模板允许蓝底 → 5 种蓝全部可选（用户按报考公告挑 HEX）；
   * 模板只允许白底 → 蓝/红/灰都不展示。后端 process.py 用同一套分组判定，
   * 前后端不会出现「界面能点、提交 400」的错位。
   */
  const availableBgColors = computed(() => {
    const byName = new Map(bgColors.value.map((c) => [c.name, c]))
    const keep: BgColor = byName.get('keep') || {
      name: 'keep', rgb: [-1, -1, -1], group: '其他', label: '保留原背景', hex: '',
    }
    const custom: BgColor = byName.get('custom') || {
      name: 'custom', rgb: [-1, -1, -1], group: '其他', label: '自定义', hex: '',
    }
    const common = PRIMARY_BG_COLORS
      .map((name) => byName.get(name))
      .filter((c): c is BgColor => !!c)

    const allowed = currentTemplate.value?.allowed_bg_colors
    if (allowed) {
      // 把模板允许的「组级色名」映射成分组集合，再据此过滤标准色号
      const allowedGroups = new Set(
        allowed
          .map((n) => byName.get(n)?.group)
          .filter((g): g is string => !!g && g !== '其他'),
      )
      const shown = common.filter((c) => allowed.includes(c.name)
        || (c.group && allowedGroups.has(c.group)))
      return [
        ...(allowed.includes('keep') ? [keep] : []),
        ...shown,
        custom,
      ]
    }
    return [keep, ...common, custom]
  })

  /**
   * 分组后的色板（前端按组渲染标题）.
   * 顺序固定「白→蓝→红→灰→其他」，组内保持 PRIMARY_BG_COLORS 的业务顺序，
   * 「保留原背景」永远排第一组，「自定义」永远排最后。
   */
  const groupedBgColors = computed(() => {
    const list = availableBgColors.value
    const order = ['白', '蓝', '红', '灰', '其他']
    const groups = new Map<string, BgColor[]>()
    // 特殊项先单独摘出来，避免被排进「其他」组中间
    const specials = list.filter((c) => c.name === 'keep' || c.name === 'custom')
    const keep = specials.find((c) => c.name === 'keep')
    const custom = specials.find((c) => c.name === 'custom')

    for (const c of list) {
      if (c.name === 'keep' || c.name === 'custom') continue
      const g = c.group || '其他'
      if (!groups.has(g)) groups.set(g, [])
      groups.get(g)!.push(c)
    }
    const out: { group: string; colors: BgColor[] }[] = []
    if (keep) out.push({ group: '原图', colors: [keep] })
    for (const g of order) {
      const colors = groups.get(g)
      if (colors && colors.length) out.push({ group: g, colors })
    }
    if (custom) out.push({ group: '自定义', colors: [custom] })
    return out
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
      // 优先用后端下发的 standard（有明确场景的标准色号），
      // 拿不到时退回全量（老后端没有 standard 字段）。
      bgColors.value = result.standard?.length ? result.standard : result.colors
      // 标准色板缺失时补全：老版本后端只下发全量色值，standard 里可能缺
      // blue_gov 这类新键，缺了就补 fallback，避免面板少项、用户选不到。
      const have = new Set(bgColors.value.map((c) => c.name))
      const missing = FALLBACK_BG_COLORS.filter((c) => !have.has(c.name))
      if (missing.length) bgColors.value = [...bgColors.value, ...missing]
    } catch {
      // 接口不可达：用与后端逐色一致的内置色板兜底（离线预览也能选全）
      bgColors.value = FALLBACK_BG_COLORS.map((c) => ({ ...c }))
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
      // 默认背景色：优先用模板允许的色（同分组也算，见 availableBgColors）。
      // 【为什么不能直接用 allowed[0]】模板可能写组级色名（"blue"），也可能写
      // 非常用色（pearl_white）。直接取 allowed[0]，UI 上就没有任何一项是选中态，
      // 用户会以为「没选颜色」。所以先把组级色名映射成面板里真实存在的标准色号，
      // 再取第一个命中项；全都没命中才退回 allowed[0]。
      const allowed: string[] = tmpl.allowed_bg_colors || []
      const byName = new Map(bgColors.value.map((c) => [c.name, c]))
      const allowedGroups = new Set(
        allowed
          .map((n) => byName.get(n)?.group)
          .filter((g): g is string => !!g && g !== '其他'),
      )
      const preferred = PRIMARY_BG_COLORS.find((name) => {
        if (!byName.has(name)) return false
        if (allowed.includes(name)) return true
        const g = byName.get(name)?.group
        return !!g && allowedGroups.has(g)
      })
      selectedBgColor.value = preferred || (allowed.length > 0 ? allowed[0] : 'white')
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
    groupedBgColors,
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
