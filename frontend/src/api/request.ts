/**
 * Axios 实例 + 拦截器。
 *
 * - 请求拦截：自动注入 token
 * - 响应拦截：统一处理 code、错误提示
 * - 错误处理：401 自动跳登录页（仅"会话过期"场景；登录失败走正常 message 错误）
 */
import axios, { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { message, dialogWarning } from '@/utils/feedback'
import type { ApiResponse } from './types'
import { TOKEN_KEY } from '@/config'

// 让 axios 的 get/post/put/delete 都接受 silent 字段
// 同时给本项目用的 RequestOptions 加上中文 JSDoc 提示
declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 静默模式：不显示全局错误提示 */
    silent?: boolean
  }
}

export interface RequestOptions extends AxiosRequestConfig {
  /** 静默模式：不显示全局错误提示（与 AxiosRequestConfig.silent 同义，保留以兼容老调用） */
  silent?: boolean
}

const baseURL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

// 401 弹窗单例化：避免并发请求触发多个弹窗堆叠
let authDialogShown = false

/**
 * 跳登录页并携带当前位置（与路由守卫的 ?redirect= 语义对齐，登录成功后回到原页面）。
 * sanitizeRedirect 只放行 / 开头的站内路径，encodeURIComponent 保证 query 串不被拆散。
 */
function gotoLoginWithRedirect(): void {
  const current = window.location.pathname + window.location.search
  window.location.href = `/login?redirect=${encodeURIComponent(current)}`
}

function showSessionExpiredDialog() {
  if (authDialogShown) return
  authDialogShown = true
  // 用户已确认退出前**不**清 token —— 让当前页面的请求还能跑完，否则会陷入"清掉 → 再 401 → 再弹"的死循环
  try {
    dialogWarning({
      title: '提示',
      content: '登录已过期，请重新登录',
      positiveText: '重新登录',
      negativeText: '取消',
      onPositive: () => {
        localStorage.removeItem(TOKEN_KEY)
        gotoLoginWithRedirect()
      },
      onClose: () => {
        authDialogShown = false
      },
    })
  } catch {
    // 组件库未就绪等异常 → 直接走清 token + 跳登录
    authDialogShown = false
    localStorage.removeItem(TOKEN_KEY)
    gotoLoginWithRedirect()
  }
}

const request = axios.create({
  baseURL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ---------- 请求拦截 ----------
request.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// ---------- 响应拦截 ----------
request.interceptors.response.use(
  (response) => {
    const data = response.data
    // data 为 null/undefined 时直接返回原响应（避免 typeof null 抛错）
    if (data === null || data === undefined) return response.data
    if (typeof data !== 'object' || !('code' in data)) return response.data

    const wrapped = data as ApiResponse
    if (wrapped.code === 0) {
      return wrapped.data
    }
    // 业务错误：静默模式（silent=true）下不弹全局错误
    const silent = (response.config as RequestOptions).silent
    if (!silent) {
      message.error(wrapped.message || '请求失败')
    }
    return Promise.reject(new Error(wrapped.message || '请求失败'))
  },
  async (error: AxiosError<ApiResponse>) => {
    const silent = (error.config as RequestOptions | undefined)?.silent
    const status = error.response?.status
    // 超时/断网给中文提示（原始英文 axios 文案对用户不友好）
    let msg = error.response?.data?.message || error.message || '网络异常'
    if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) msg = '请求超时，请检查网络后重试'
    else if (!error.response) msg = '无法连接服务器，请确认服务已启动'
    const url = error.config?.url || ''

    // 401 处理：登录失败（/auth/login 命中） vs 会话过期（其他接口）分开
    if (status === 401) {
      if (url.includes('/auth/login')) {
        // 登录失败：业务错误，直接 toast 让用户知道密码错
        if (!silent) message.error(msg || '登录失败')
        return Promise.reject(error)
      }
      // 会话过期：弹窗，让用户选择
      showSessionExpiredDialog()
      return Promise.reject(error)
    }

    if (!silent) {
      message.error(msg)
    }
    return Promise.reject(error)
  }
)

export default request
