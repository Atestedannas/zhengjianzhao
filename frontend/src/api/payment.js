/**
 * 支付 API
 */
import { request } from './request'

export function createPayment(payMethod) {
  return request({
    url: '/api/v1/payment/create',
    method: 'POST',
    data: { pay_method: payMethod },
  })
}

export function queryOrderStatus(orderNo) {
  return request({
    url: `/api/v1/payment/query/${orderNo}`,
    method: 'GET',
  })
}
