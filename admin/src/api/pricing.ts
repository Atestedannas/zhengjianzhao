import { get, put } from './index'

export interface PricingConfig {
  unit_price: number
  register_bonus: number
  daily_bonus_enabled: boolean
  daily_bonus_count: number
}

export function getPricing(): Promise<PricingConfig> {
  return get('/pricing')
}

export function updatePricing(data: PricingConfig): Promise<void> {
  return put('/pricing', data)
}
