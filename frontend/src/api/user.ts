// 用户管理 API
import request from './request'
import type { PageParams, PageResult, UserInfo } from './types'

export interface UserListParams extends PageParams {
  username?: string
  /** 1/0，FastAPI 查询参数自动转 bool */
  is_active?: number
}

export interface UserCreatePayload {
  username: string
  password: string
  nickname?: string
  email?: string
  phone?: string
  role_id?: number
  is_active?: boolean
}

export interface UserUpdatePayload {
  nickname?: string
  email?: string
  phone?: string
  avatar?: string
  role_id?: number
  is_active?: boolean
}

export interface UserPasswordPayload {
  old_password: string
  new_password: string
}

/** 用户列表 */
export function listUsers(params: UserListParams = {}) {
  return request.get<PageResult<UserInfo>, PageResult<UserInfo>>('/users', { params })
}

/** 创建用户 */
export function createUser(payload: UserCreatePayload) {
  return request.post<UserInfo, UserInfo>('/users', payload)
}

/** 用户详情 */
export function getUser(id: number) {
  return request.get<UserInfo, UserInfo>(`/users/${id}`)
}

/** 更新用户 */
export function updateUser(id: number, payload: UserUpdatePayload) {
  return request.put<UserInfo, UserInfo>(`/users/${id}`, payload)
}

/** 删除用户 */
export function deleteUser(id: number) {
  return request.delete<unknown, unknown>(`/users/${id}`)
}

/** 管理员修改密码 */
export function adminChangePassword(id: number, payload: UserPasswordPayload) {
  return request.post<unknown, unknown>(`/users/${id}/password`, payload)
}
