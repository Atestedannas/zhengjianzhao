import { post } from './index'

export interface LoginParams {
  username: string
  password: string
}

export interface LoginResult {
  access_token: string
  refresh_token: string
  username: string
  role: 'super_admin' | 'normal_admin'
  expires_in: number
}

export function adminLogin(params: LoginParams): Promise<LoginResult> {
  return post('/login', params)
}

export function adminLogout(): Promise<void> {
  return post('/logout')
}

export function refreshToken(
  refreshToken: string,
): Promise<{ access_token: string; refresh_token: string; expires_in: number }> {
  return post('/refresh', { refresh_token: refreshToken })
}
