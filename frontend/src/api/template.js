/**
 * 模板 API
 */
import { request } from './request'

export function getTemplates() {
  return request({
    url: '/api/v1/templates',
    method: 'GET',
  })
}

export function getTemplateDetail(id) {
  return request({
    url: `/api/v1/templates/${id}`,
    method: 'GET',
  })
}
