import { get } from './index'

export interface DashboardStats {
  today_processed: number
  total_processed: number
  today_revenue: number
  paid_orders_count: number
  free_usage_ratio: number
  rembg_fail_rate: number
}

export interface TrendData {
  dates: string[]
  process_counts: number[]
  revenue_amounts: number[]
}

export interface TemplateUsage {
  names: string[]
  counts: number[]
}

export function getStats(): Promise<DashboardStats> {
  return get('/dashboard/stats')
}

export function getTrend(days?: number): Promise<TrendData> {
  return get('/dashboard/trend', { days })
}

export function getTemplateUsage(): Promise<TemplateUsage> {
  return get('/dashboard/template-usage')
}
