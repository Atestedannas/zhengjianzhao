/**
 * 用户 API
 */
import { request } from './request'

export function getProfile() {
  return request({
    url: '/api/v1/user/profile',
    method: 'GET',
  })
}

export function getFreeCount() {
  return request({
    url: '/api/v1/user/free-count',
    method: 'GET',
  })
}
