import { get, post, patch } from './index'

export interface User {
  id: number
  openid: string
  platform: string
  nickname: string
  avatar_url: string
  free_count: number
  free_count_total: number
  balance: number
  total_spent: number
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface UserListParams {
  page?: number
  page_size?: number
  keyword?: string
  start_date?: string
  end_date?: string
}

export interface UserListResult {
  total: number
  items: User[]
}

export function getUsers(params: UserListParams): Promise<UserListResult> {
  return get('/users', params)
}

export function getUserDetail(id: number): Promise<User & { records: any[]; orders: any[] }> {
  return get(`/users/${id}`)
}

/**
 * 调整用户免费次数.
 *
 * mode="set"：count 是目标值 —— 与后台弹窗「调整为 N」的文案一致。
 * 历史 bug：接口默认是增量语义（free_count += count），
 * 于是「当前 5，调整为 10」会变成 15。后端仍保留 delta 模式兼容旧调用。
 */
export function adjustFreeCount(id: number, count: number): Promise<void> {
  return post(`/users/${id}/free-count`, { count, mode: 'set' })
}

export function toggleUser(id: number, isActive: boolean): Promise<void> {
  return patch(`/users/${id}/toggle`, { is_active: isActive })
}
