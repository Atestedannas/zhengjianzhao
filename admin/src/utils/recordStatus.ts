/** 处理记录状态 → 展示文案 / el-tag 类型.
 *
 * 背景：/api/v1/process/ 改成 Celery 异步后，记录会先出现
 * pending / processing 两个中间态。以前各视图直接写
 * `status === 'success' ? '成功' : '失败'`，会把「处理中」渲染成红色「失败」，
 * 所以统一走这里。
 */

export type RecordStatusTagType = 'success' | 'warning' | 'danger'

/** 是否处理中（pending / processing） */
export function isRecordProcessing(status: string): boolean {
  return status === 'pending' || status === 'processing'
}

/** 状态文案：成功 / 处理中 / 失败 */
export function recordStatusText(status: string): string {
  if (status === 'success') return '成功'
  if (isRecordProcessing(status)) return '处理中'
  return '失败'
}

/** el-tag 类型：成功=绿 / 处理中=橙 / 失败=红 */
export function recordStatusTagType(status: string): RecordStatusTagType {
  if (status === 'success') return 'success'
  if (isRecordProcessing(status)) return 'warning'
  return 'danger'
}
