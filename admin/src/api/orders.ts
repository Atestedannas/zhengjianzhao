import { get, post } from './index'

export interface Order {
  id: number
  order_no: string
  user_id: number
  user_nickname: string
  amount: number
  pay_method: 'wechat_jsapi' | 'wechat_native' | 'alipay' | null
  trade_no: string | null
  status: 'pending' | 'paid' | 'refunded' | 'closed'
  paid_at: string | null
  refund_amount: number | null
  refund_at: string | null
  created_at: string
}

export interface OrderListParams {
  page?: number
  page_size?: number
  status?: string
  pay_method?: string
  start_date?: string
  end_date?: string
}

export interface OrderListResult {
  total: number
  items: Order[]
}

export function getOrders(params: OrderListParams): Promise<OrderListResult> {
  return get('/orders', params)
}

export function refundOrder(id: number): Promise<void> {
  return post(`/orders/${id}/refund`)
}
