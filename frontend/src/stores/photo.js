/**
 * 照片处理状态管理
 * state: originalPath, croppedPath, selectedTemplate, customParams, resultUrl, resultInfo, isProcessing
 */
import { defineStore } from 'pinia'
import { processPhoto as apiProcessPhoto, getProcessStatus } from '@/api/process'
import { useUserStore } from '@/stores/user'

// ===== 异步处理轮询参数 =====
/** 轮询间隔：1.5 秒 */
const POLL_INTERVAL_MS = 1500
/** 最大轮询次数：120 次（约 3 分钟） */
const POLL_MAX_ATTEMPTS = 120
/** 轮询超时文案 */
const POLL_TIMEOUT_MESSAGE = '处理时间较长，请稍后在历史记录中查看'

// ===== 「生成照片」卡死的三道保险（与 PC 端同一套策略）=====
// 用户反馈：点「开始处理」后一直转圈，无法进行下一步。根因是 loading 只依赖
// 「请求自己结束」，只要有一个请求挂住就永远为 true。这里加三道互相独立的保险：
//   1) 单次请求超时：提交 30s / 查询状态 10s；
//   2) 客户端硬超时：90 秒到点强制解除 loading 并提示可重试；
//   3) 用户主动取消：cancelProcess() 立刻停轮询 + 恢复按钮。
const SUBMIT_TIMEOUT_MS = 30000
const STATUS_TIMEOUT_MS = 10000
const HARD_TIMEOUT_MS = 90000
const HARD_TIMEOUT_MESSAGE = '处理超时（超过 90 秒未完成），已停止等待，请重试'

// 轮询控制放在 store 外部，避免定时器被 pinia 代理包装
let currentPollRun = null

/** 取消当前轮询并清掉定时器，允许重复调用 */
function stopPollingRun() {
  const run = currentPollRun
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
  currentPollRun = null
}

/** 开始一轮新的轮询（会先取消旧的，避免重复请求） */
function createPollRun() {
  stopPollingRun()
  const run = {
    cancelled: false,
    timer: null,
    wake: null,
    deadline: null,
    startedAt: Date.now(),
    timedOut: false,
  }
  currentPollRun = run
  return run
}

/**
 * 给请求加「单次超时」：到点必定 reject，绝不无限挂起.
 * uni.request 的 timeout 在部分平台（小程序真机）不生效，
 * 这里用 Promise.race 兜住，保证 loading 一定会被解除。
 */
function withTimeout(promise, ms, message) {
  return new Promise((resolve, reject) => {
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      const err = new Error(message)
      err.timeout = true
      reject(err)
    }, ms)
    promise.then(
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

/** 可被取消立即打断的间隔等待 */
function waitInterval(run) {
  return new Promise((resolve) => {
    run.wake = () => resolve()
    run.timer = setTimeout(() => {
      run.timer = null
      run.wake = null
      resolve()
    }, POLL_INTERVAL_MS)
  })
}

/** 轮询被取消（页面卸载 / 重新提交）时抛出的错误，调用方静默忽略 */
function cancelledError() {
  const err = new Error('已取消处理')
  err.cancelled = true
  return err
}

/** 处理失败 / 轮询超时错误，message 可直接展示 */
function pollError(message, timeout) {
  const err = new Error(message)
  err.pollError = true
  err.timeout = !!timeout
  return err
}

/** 用接口返回的剩余次数刷新用户展示（失败时后端已退回，也要同步） */
function syncFreeCount(remaining) {
  if (typeof remaining !== 'number') return
  try {
    useUserStore().freeCount = remaining
  } catch {
    // store 未就绪时忽略
  }
}

export const usePhotoStore = defineStore('photo', {
  state: () => ({
    originalPath: '',
    croppedPath: '',
    selectedTemplate: null,
    customParams: {
      width: null,
      height: null,
      resizeMode: 'crop',
      upscale: true,
      dpi: 350,
      minKb: 0,
      maxKb: 100,
      bgColor: 'white',
      outputFormat: 'JPEG',
      beautifyLevel: 1,
      beautifySmooth: true,
      beautifyBrighten: true,
      beautifyBlemish: true,
      gender: '',
      idPhotoAlign: false,
    },
    resultUrl: '',
    resultInfo: {},
    isProcessing: false,
  }),

  getters: {
    /** 当前有效的处理参数 */
    effectiveParams: (state) => {
      if (state.selectedTemplate) {
        const t = state.selectedTemplate
        return {
          template_id: t.id,
          width: t.width_px,
          height: t.height_px,
          dpi: t.dpi,
          minKb: t.min_kb,
          maxKb: t.max_kb,
          bgColor: state.customParams.bgColor,
          outputFormat: state.customParams.outputFormat,
          beautifyLevel: state.customParams.beautifyLevel,
          beautifySmooth: state.customParams.beautifySmooth,
          beautifyBrighten: state.customParams.beautifyBrighten,
          beautifyBlemish: state.customParams.beautifyBlemish,
          gender: state.customParams.gender || undefined,
          idPhotoAlign: state.customParams.idPhotoAlign,
        }
      }
      return state.customParams
    },

    /** 当前裁剪比例 */
    cropRatio: (state) => {
      if (state.selectedTemplate) {
        return `${state.selectedTemplate.width_px}:${state.selectedTemplate.height_px}`
      }
      if (state.customParams.width && state.customParams.height) {
        return `${state.customParams.width}:${state.customParams.height}`
      }
      return '3:4'
    },
  },

  actions: {
    /**
     * 上传并调用处理接口
     * 后端已改为异步：提交后拿到 record_id 轮询状态接口，
     * 状态为 success 时复用原有成功逻辑（同步返回 success 也兼容）。
     */
    async processPhoto(filePath) {
      const run = createPollRun()
      this.isProcessing = true

      // 【第二道保险】客户端硬超时：到点强制解除 loading（不依赖任何请求回调）
      run.deadline = setTimeout(() => {
        if (run.cancelled) return
        run.timedOut = true
        if (run.wake) {
          const wake = run.wake
          run.wake = null
          wake()
        }
        this.isProcessing = false
      }, HARD_TIMEOUT_MS)

      try {
        const params = this.effectiveParams
        // 【第一道保险】提交请求 30 秒上限
        const res = await withTimeout(
          apiProcessPhoto(filePath, params),
          SUBMIT_TIMEOUT_MS,
          '提交超时（30 秒未响应），请检查网络后重试',
        )
        if (run.timedOut) throw pollError(HARD_TIMEOUT_MESSAGE, true)

        if (res.code === 402) {
          this.isProcessing = false
          return {
            needPay: true,
            orderNo: res.data.order_no,
            amount: res.data.amount,
            orderId: res.data.order_id,
          }
        }

        if (res.code !== 200) {
          this.isProcessing = false
          return { success: false, message: res.message || '处理失败' }
        }

        const submit = res.data || {}
        // 提交后后端已按次扣费，先同步剩余次数
        syncFreeCount(submit.remaining_free_count)

        // 兼容同步返回：没有 status 或已是 success 时直接走成功分支
        if (!submit.status || submit.status === 'success') {
          this.applyResult(submit, submit.free_used)
          this.isProcessing = false
          return { success: true, data: submit }
        }

        // 异步：清掉上一次结果，进入处理中状态后轮询
        this.resultUrl = ''
        this.resultInfo = {}
        return await this.pollResult(run, submit)
      } catch (e) {
        this.isProcessing = false
        // 页面卸载 / 重新提交导致的取消：静默返回
        if (e && e.cancelled) {
          return { success: false, cancelled: true }
        }
        // 处理失败 / 超时：提示文案并停止轮询
        if (e && e.pollError) {
          uni.showToast({ title: e.message, icon: 'none' })
          return { success: false, message: e.message, timeout: !!e.timeout }
        }
        // 单次请求超时（提交/查询）：同样要给用户明确的可重试提示
        if (e && e.timeout) {
          uni.showToast({ title: e.message || '请求超时，请重试', icon: 'none' })
          return { success: false, message: e.message, timeout: true }
        }
        uni.showToast({ title: '处理失败，请重试', icon: 'none' })
        return { success: false, message: e.message }
      } finally {
        // 只有自己仍是当前轮询时才收尾，避免影响后续提交
        if (currentPollRun === run) {
          stopPollingRun()
          this.isProcessing = false
        }
      }
    },

    /**
     * 把处理结果写入 state（同步返回与轮询成功共用）
     */
    applyResult(data, freeUsed) {
      this.resultUrl = data.result_url || data.download_url || ''
      this.resultInfo = {
        record_id: data.record_id,
        file_size_kb: data.file_size_kb,
        pixels: data.pixels,
        dpi: data.dpi,
        output_format: data.output_format,
        mime_type: data.mime_type,
        faces_detected: data.faces_detected,
        processing_time_ms: data.processing_time_ms,
        warnings: data.warnings || [],
        free_used: freeUsed !== undefined ? freeUsed : data.free_used,
        remaining_free_count: data.remaining_free_count,
      }
    },

    /**
     * 轮询状态接口，直到 success / failed / 超时
     */
    async pollResult(run, submit) {
      for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
        if (run.cancelled) throw cancelledError()
        if (run.timedOut) throw pollError(HARD_TIMEOUT_MESSAGE, true)

        await waitInterval(run)
        if (run.cancelled) throw cancelledError()
        if (run.timedOut) throw pollError(HARD_TIMEOUT_MESSAGE, true)

        // 单次状态查询也要有超时：链路卡住时不能把整轮等待一起拖死
        const res = await withTimeout(
          getProcessStatus(submit.record_id),
          STATUS_TIMEOUT_MS,
          '查询处理状态超时，请重试',
        )
        if (run.cancelled) throw cancelledError()

        const data = (res && res.data) || {}
        // 状态接口也会带回最新次数（失败时后端已退回），先同步展示
        syncFreeCount(data.remaining_free_count)

        if (data.status === 'success') {
          const merged = { ...submit, ...data }
          this.applyResult(merged, submit.free_used)
          this.isProcessing = false
          return { success: true, data: merged }
        }

        if (data.status === 'failed') {
          throw pollError(data.error || '处理失败，请重试', false)
        }
        // pending / processing：继续等待
      }

      throw pollError(POLL_TIMEOUT_MESSAGE, true)
    },

    /**
     * 停止轮询（页面卸载 / 重新提交 / 用户取消时调用）
     */
    cancelPolling() {
      stopPollingRun()
      this.isProcessing = false
    },

    /**
     * 用户主动取消：立刻停轮询 + 恢复按钮（后端任务可能仍在跑，历史记录里能看到）
     */
    cancelProcess() {
      stopPollingRun()
      this.isProcessing = false
    },

    /**
     * 重置所有状态
     */
    reset() {
      // 重置时停掉轮询，避免后台继续请求
      stopPollingRun()
      this.originalPath = ''
      this.croppedPath = ''
      this.selectedTemplate = null
      this.customParams = {
        width: null,
        height: null,
        resizeMode: 'crop',
        upscale: true,
        dpi: 350,
        minKb: 0,
        maxKb: 100,
        bgColor: 'white',
        outputFormat: 'JPEG',
        beautifyLevel: 1,
        beautifySmooth: true,
        beautifyBrighten: true,
        beautifyBlemish: true,
        gender: '',
        idPhotoAlign: false,
      }
      this.resultUrl = ''
      this.resultInfo = {}
      this.isProcessing = false
    },
  },
})
