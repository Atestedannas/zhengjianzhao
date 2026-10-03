/**
 * 照片处理 API（增强版）
 */
import { request } from './request'

/**
 * 上传并处理照片
 * @param {string} filePath - 本地图片路径
 * @param {object} params - 处理参数
 */
export function processPhoto(filePath, params = {}) {
  const data = {}

  // 尺寸
  if (params.template_id) data.template_id = params.template_id
  if (params.width) data.width = params.width
  if (params.height) data.height = params.height
  if (params.resizeMode !== undefined) data.resize_mode = params.resizeMode
  if (params.upscale !== undefined) data.upscale = params.upscale

  // 输出
  if (params.dpi) data.dpi = params.dpi
  if (params.minKb !== undefined) data.min_kb = params.minKb
  if (params.maxKb !== undefined) data.max_kb = params.maxKb
  if (params.bgColor) data.bg_color = params.bgColor
  if (params.outputFormat) data.output_format = params.outputFormat

  // 美颜
  if (params.beautifyLevel !== undefined) data.beautify_level = params.beautifyLevel
  if (params.beautifySmooth !== undefined) data.beautify_smooth = params.beautifySmooth
  if (params.beautifyBrighten !== undefined) data.beautify_brighten = params.beautifyBrighten
  if (params.beautifyBlemish !== undefined) data.beautify_blemish = params.beautifyBlemish

  // 证件照
  if (params.idPhotoAlign) data.id_photo_align = params.idPhotoAlign
  if (params.gender) data.gender = params.gender

  return request({
    url: '/api/v1/process',
    method: 'UPLOAD',
    filePath,
    name: 'file',
    data,
  })
}

/**
 * 获取尺寸预设列表
 */
export function getSizePresets() {
  return request({
    url: '/api/v1/process/presets',
    method: 'GET',
  })
}

/**
 * 获取支持的输出格式
 */
export function getOutputFormats() {
  return request({
    url: '/api/v1/process/formats',
    method: 'GET',
  })
}

/**
 * 获取支持的背景色
 */
export function getBgColors() {
  return request({
    url: '/api/v1/process/bg-colors',
    method: 'GET',
  })
}

export function getPreview(recordId) {
  return request({
    url: `/api/v1/process/${recordId}/preview`,
    method: 'GET',
  })
}

/**
 * 查询处理记录状态（异步处理轮询接口）
 * status: pending | processing | success | failed
 */
export function getProcessStatus(recordId) {
  return request({
    url: `/api/v1/process/${recordId}/status`,
    method: 'GET',
  })
}

export function downloadResult(recordId) {
  return request({
    url: `/api/v1/process/${recordId}/download`,
    method: 'GET',
  })
}

export function getHistory(limit = 10) {
  return request({
    url: '/api/v1/process/history',
    method: 'GET',
    data: { limit },
  })
}