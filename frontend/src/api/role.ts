// 角色管理 API
import request from './request'

export interface Role {
  id: number
  name: string
  code: string
  remark?: string
  created_at: string
}

export interface RoleCreatePayload {
  name: string
  code: string
  remark?: string
}

export interface RoleUpdatePayload {
  name?: string
  code?: string
  remark?: string
}

/** 角色列表 */
export function listRoles() {
  return request.get<Role[], Role[]>('/roles')
}

export function createRole(payload: RoleCreatePayload) {
  return request.post<Role, Role>('/roles', payload)
}

export function updateRole(id: number, payload: RoleUpdatePayload) {
  return request.put<Role, Role>(`/roles/${id}`, payload)
}

export function deleteRole(id: number) {
  return request.delete<unknown, unknown>(`/roles/${id}`)
}
