import { get, put } from './index'

export interface SystemSettings {
  upload_max_mb: number
  temp_file_ttl_minutes: number
  rate_limit_per_minute: number
  maintenance_mode: boolean
}

export function getSettings(): Promise<SystemSettings> {
  return get('/settings')
}

export function updateSettings(data: SystemSettings): Promise<void> {
  return put('/settings', data)
}
