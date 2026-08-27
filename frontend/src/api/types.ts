// API 通用类型

/** 统一响应结构 */
export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
}

/** 分页参数 */
export interface PageParams {
  page?: number
  page_size?: number
}

/** 分页响应 */
export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/** 登录请求 */
export interface LoginPayload {
  username: string
  password: string
}

/** 登录响应 */
export interface LoginResult {
  access_token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

/** 用户信息 */
export interface UserInfo {
  id: number
  username: string
  nickname?: string
  email?: string
  phone?: string
  avatar?: string
  is_active: boolean
  is_superuser: boolean
  role_id?: number
  role_name?: string
  created_at: string
}
