/**
 * UI 预览模式 mock 后端（零依赖，vite dev middleware）。
 *
 * 仅在 .env.development 里 VITE_USE_MOCK=true 时由 vite.config.ts 启用
 * （即 --only ui 生成的项目）：没有 FastAPI 后端也能 pnpm dev + admin/admin 登录。
 * admin 模式有真后端，绝不启用（mock 中间件会截走 /api 请求）。
 *
 * 端点覆盖登录全链路 + dashboard / 用户 / 角色页数据源。
 */
import type { Plugin, Connect } from 'vite'

const MOCK_USER = {
  id: 1,
  username: 'admin',
  nickname: '管理员',
  email: 'admin@example.com',
  phone: '',
  avatar: '',
  is_active: true,
  is_superuser: true,
  role_id: 1,
  role_name: '超级管理员',
  created_at: '2026-01-01T00:00:00Z',
}

const MOCK_ROLES = [
  { id: 1, name: '超级管理员', code: 'superadmin', remark: '全部权限', created_at: '2026-01-01T00:00:00Z' },
  { id: 2, name: '运营', code: 'operator', remark: '日常运营', created_at: '2026-01-05T00:00:00Z' },
  { id: 3, name: '访客', code: 'guest', remark: '只读', created_at: '2026-02-01T00:00:00Z' },
]

const MOCK_USERS = [
  MOCK_USER,
  { id: 2, username: 'zhangsan', nickname: '张三', email: 'zhangsan@example.com', phone: '', avatar: '', is_active: true, is_superuser: false, role_id: 2, role_name: '运营', created_at: '2026-03-12T08:00:00Z' },
  { id: 3, username: 'lisi', nickname: '李四', email: 'lisi@example.com', phone: '', avatar: '', is_active: true, is_superuser: false, role_id: 2, role_name: '运营', created_at: '2026-05-20T08:00:00Z' },
  { id: 4, username: 'wangwu', nickname: '王五', email: 'wangwu@example.com', phone: '', avatar: '', is_active: false, is_superuser: false, role_id: 3, role_name: '访客', created_at: '2026-07-08T08:00:00Z' },
]

function json(res: Connect.ServerResponse, status: number, body: unknown) {
  const payload = JSON.stringify(body)
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.end(payload)
}

/** 读取请求 body（JSON） */
function readBody(req: Connect.IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (chunk: Buffer) => (raw += chunk.toString()))
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch {
        resolve({})
      }
    })
  })
}

function hasToken(req: Connect.IncomingMessage): boolean {
  return (req.headers.authorization || '').startsWith('Bearer ')
}

export function uiMockPlugin(): Plugin {
  return {
    name: 'youzi-ui-mock',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url || '').split('?')[0]

        // 只拦 /api/v1 下已 mock 的端点，其余放行（404 由后续逻辑处理）
        if (!url.startsWith('/api/v1/')) return next()

        // ---------- 登录 ----------
        if (url === '/api/v1/auth/login' && req.method === 'POST') {
          readBody(req).then(({ username, password }) => {
            if (username === 'admin' && password === 'admin') {
              json(res, 200, {
                access_token: 'mock-token-for-ui-preview',
                token_type: 'bearer',
                expires_in: 86400,
                user: MOCK_USER,
              })
            } else {
              json(res, 401, { detail: '用户名或密码错误（预览账号 admin/admin）' })
            }
          })
          return
        }

        // ---------- 当前用户 ----------
        if (url === '/api/v1/auth/me' && req.method === 'GET') {
          if (!hasToken(req)) return json(res, 401, { detail: 'Not authenticated' })
          return json(res, 200, MOCK_USER)
        }

        // ---------- 登出 ----------
        if (url === '/api/v1/auth/logout' && req.method === 'POST') {
          return json(res, 200, {})
        }

        // ---------- 用户列表（dashboard 统计 + 用户管理页共用） ----------
        if (url === '/api/v1/users' && req.method === 'GET') {
          if (!hasToken(req)) return json(res, 401, { detail: 'Not authenticated' })
          const q = new URLSearchParams((req.url || '').split('?')[1] || '')
          let items = MOCK_USERS
          if (q.get('is_active') === 'true') items = items.filter((u) => u.is_active)
          const page = Number(q.get('page') || 1)
          const pageSize = Number(q.get('page_size') || 20)
          const start = (page - 1) * pageSize
          return json(res, 200, {
            items: items.slice(start, start + pageSize),
            total: items.length,
            page,
            page_size: pageSize,
          })
        }

        // ---------- 角色列表 ----------
        if (url === '/api/v1/roles' && req.method === 'GET') {
          if (!hasToken(req)) return json(res, 401, { detail: 'Not authenticated' })
          return json(res, 200, MOCK_ROLES)
        }

        // 未覆盖的 /api/v1/*：返回统一空成功，避免页面报错刷屏
        return json(res, 200, { code: 0, message: 'ok (ui 预览 mock 未覆盖此端点)', data: null })
      })

      server.httpServer?.once('listening', () => {
        console.log('\n🔧 UI 预览模式：mock API 已启用（无后端）。登录账号 admin / admin\n')
      })
    },
  }
}
