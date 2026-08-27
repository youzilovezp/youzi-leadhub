/**
 * stores/user — token 持久化 + logout 清空 验证。
 *
 * 必须 mock @/api/auth，否则 logout() 会真实请求后端导致测试 hang。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// mock 整个 auth API 模块——logout 等不再发真实请求
vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  getMe: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined),
}))

// import 必须在 mock 之后
import { useUserStore } from '@/stores/user'

describe('user store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('starts with empty token', () => {
    const store = useUserStore()
    expect(store.token).toBe('')
    expect(store.isLogin).toBe(false)
  })

  it('logout clears token and localStorage', async () => {
    const store = useUserStore()
    store.token = 'fake-jwt'
    localStorage.setItem('access_token', 'fake-jwt')
    await store.logout()
    expect(store.token).toBe('')
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('isLogin is true when token is set', () => {
    const store = useUserStore()
    store.token = 'fake-jwt'
    expect(store.isLogin).toBe(true)
  })
})
