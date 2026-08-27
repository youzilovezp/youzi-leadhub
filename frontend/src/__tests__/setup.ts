/**
 * Vitest 全局 setup：happy-dom 在某些版本下 window.localStorage 默认未挂载，
 * 这里手动补上最常用的浏览器 API，确保测试可重复运行。
 */
import { vi } from 'vitest'

// localStorage 兜底：部分 happy-dom 版本在 import 阶段 window 还没准备好
if (typeof globalThis.localStorage === 'undefined') {
  const store = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, String(v)),
      removeItem: (k: string) => store.delete(k),
      clear: () => store.clear(),
      key: (i: number) => Array.from(store.keys())[i] ?? null,
      get length() {
        return store.size
      },
    },
    configurable: true,
  })
}

vi.stubEnv('VITE_TOKEN_KEY', 'access_token')
vi.stubEnv('VITE_APP_TITLE', 'Youzi Admin')
vi.stubEnv('VITE_API_BASE_URL', '/api/v1')
