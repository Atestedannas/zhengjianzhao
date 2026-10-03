import { get } from './index'

export interface ProcessRecord {
  id: number
  user_id: number | null
  template_id: number | null
  template_name: string
  user_nickname: string
  is_paid: boolean
  paid_amount: number | null
  original_size: number
  result_size: number
  result_pixels: string | null
  result_dpi: number | null
  bg_color: string | null
  processing_time_ms: number
  status: 'pending' | 'processing' | 'success' | 'failed'
  error_message: string | null
  created_at: string
}

export interface RecordListParams {
  page?: number
  page_size?: number
  start_date?: string
  end_date?: string
  template_id?: number
  status?: string
  is_paid?: boolean
}

export interface RecordListResult {
  total: number
  items: ProcessRecord[]
}

export function getRecords(params: RecordListParams): Promise<RecordListResult> {
  return get('/records', params)
}

export function getRecordDetail(id: number): Promise<ProcessRecord & { request_params: any }> {
  return get(`/records/${id}`)
}

export function exportRecords(params: RecordListParams): Promise<Blob> {
  // 通过 config 指定 responseType: 'blob'，拦截器会直接返回 Blob 原始数据
  return get('/records/export', params, { responseType: 'blob' })
}
