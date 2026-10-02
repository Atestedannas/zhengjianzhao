/**
 * 照片处理状态管理
 * state: originalPath, croppedPath, selectedTemplate, customParams, resultUrl, resultInfo, isProcessing
 */
import { defineStore } from 'pinia'
import { processPhoto as apiProcessPhoto } from '@/api/process'

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
     */
    async processPhoto(filePath) {
      this.isProcessing = true
      try {
        const params = this.effectiveParams
        const res = await apiProcessPhoto(filePath, params)

        if (res.code === 200) {
          this.resultUrl = res.data.result_url || res.data.download_url
          this.resultInfo = {
            record_id: res.data.record_id,
            file_size_kb: res.data.file_size_kb,
            pixels: res.data.pixels,
            dpi: res.data.dpi,
            output_format: res.data.output_format,
            mime_type: res.data.mime_type,
            faces_detected: res.data.faces_detected,
            processing_time_ms: res.data.processing_time_ms,
            warnings: res.data.warnings || [],
            free_used: res.data.free_used,
            remaining_free_count: res.data.remaining_free_count,
          }
          this.isProcessing = false
          return { success: true, data: res.data }
        }

        if (res.code === 402) {
          this.isProcessing = false
          return {
            needPay: true,
            orderNo: res.data.order_no,
            amount: res.data.amount,
            orderId: res.data.order_id,
          }
        }

        this.isProcessing = false
        return { success: false, message: res.message || '处理失败' }
      } catch (e) {
        this.isProcessing = false
        uni.showToast({ title: '处理失败，请重试', icon: 'none' })
        return { success: false, message: e.message }
      }
    },

    /**
     * 重置所有状态
     */
    reset() {
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
