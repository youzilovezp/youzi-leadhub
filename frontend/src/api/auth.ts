// 认证相关 API
import request from './request'
import type { LoginPayload, LoginResult, UserInfo } from './types'

/** 登录 */
export function login(payload: LoginPayload) {
  return request.post<LoginResult, LoginResult>('/auth/login', payload)
}

/** 当前用户 */
export function getMe() {
  return request.get<UserInfo, UserInfo>('/auth/me')
}

/** 登出 */
export function logout() {
  return request.post<unknown, unknown>('/auth/logout')
}
