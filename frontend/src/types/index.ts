// 全局类型声明
// 注：vite/client 已提供 *.vue / *.svg / *.png 等资源模块声明，无需重复
// 这里补充项目自定义的 VITE_* 环境变量类型 + 路由 meta 类型

interface ImportMetaEnv {
  readonly VITE_APP_TITLE: string
  readonly VITE_API_BASE_URL: string
  readonly VITE_TOKEN_KEY?: string
  readonly VITE_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

// 路由 meta 类型扩展：让 vue-tsc 不报 "to.meta.public" 是 unknown
// 否则 router.beforeEach 里访问 meta.public/meta.title 会编译失败
declare module 'vue-router' {
  interface RouteMeta {
    /** 是否公开页（true = 无需登录） */
    public?: boolean
    /** 页面标题（用于浏览器 tab 标题 + 面包屑） */
    title?: string
    /** 菜单图标（Naive UI 图标组件名） */
    icon?: string
    /** 是否需要 superuser 权限 */
    requiresAdmin?: boolean
  }
}
