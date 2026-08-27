// 路由配置
import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
  type NavigationGuardNext,
} from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/BasicLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '首页' },
      },
      {
        path: 'system/user',
        name: 'SystemUser',
        component: () => import('@/views/system/user/index.vue'),
        meta: { title: '用户管理', requiresAdmin: true },
      },
      {
        path: 'system/role',
        name: 'SystemRole',
        component: () => import('@/views/system/role/index.vue'),
        meta: { title: '角色管理', requiresAdmin: true },
      },
      {
        path: 'collect/lead',
        name: 'CollectLead',
        component: () => import('@/views/collect/lead/index.vue'),
        meta: { title: '线索列表' },
      },
      {
        path: 'collect/task',
        name: 'CollectTask',
        component: () => import('@/views/collect/task/index.vue'),
        meta: { title: '采集任务' },
      },
      {
        path: 'collect/task/:id',
        name: 'CollectTaskDetail',
        component: () => import('@/views/collect/task/detail.vue'),
        meta: { title: '任务详情', hidden: true },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/404.vue'),
    meta: { public: true, title: '404' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ---------- 全局守卫 ----------
router.beforeEach(async (to: RouteLocationNormalized, _from: RouteLocationNormalized, next: NavigationGuardNext) => {
  const userStore = useUserStore()

  // 设置页面标题
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} - ${import.meta.env.VITE_APP_TITLE}` : import.meta.env.VITE_APP_TITLE

  // 公开页面
  if (to.meta.public) {
    return next()
  }

  // 需要登录
  if (!userStore.isLogin) {
    return next({ name: 'Login', query: { redirect: to.fullPath } })
  }

  // 已登录但未拉取用户信息
  if (!userStore.userInfo) {
    try {
      await userStore.fetchProfile()
    } catch (e) {
      // 仅"未授权"才登出；网络抖动 / 5xx 不踢用户（否则一次超时就全员掉线）
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 401 || status === 403) {
        await userStore.logout()
        return next({ name: 'Login', query: { redirect: to.fullPath } })
      }
      // 拿不到用户信息就别放行 admin 页，普通页放行
      if (to.meta.requiresAdmin) {
        return next({ name: 'Login', query: { redirect: to.fullPath } })
      }
    }
  }

  // 需要管理员
  if (to.meta.requiresAdmin && !userStore.isSuperuser) {
    return next({ name: 'Dashboard' })
  }

  next()
})

export default router
