/**
 * 全局反馈 API（Naive UI createDiscreteApi，懒加载单例）。
 *
 * 供 setup 之外使用（axios 拦截器 / store / 工具函数），setup 内同样可用。
 * 懒加载原因：request.ts 的 import 链早于 app.use(pinia)——
 * 顶层 useAppStore() 会抛 "no active Pinia"，首次调用时（请求发出时）pinia 必然就绪。
 * 主题（暗色 + 主题色）通过响应式 configProviderProps 跟随 app store。
 */
import { computed } from 'vue'
import { createDiscreteApi, darkTheme } from 'naive-ui'
import { useAppStore } from '@/stores/app'

type DiscreteApi = ReturnType<typeof createDiscreteApi>
let api: DiscreteApi | null = null

function ensure(): DiscreteApi {
  if (api) return api
  const appStore = useAppStore()
  const configProviderProps = computed(() => ({
    theme: appStore.isDark ? darkTheme : undefined,
    themeOverrides: appStore.naiveThemeOverrides,
  }))
  // naive 类型声明含 null 联合（实际传了组件必非空），断言收窄
  api = createDiscreteApi(['message', 'dialog'], { configProviderProps }) as DiscreteApi
  return api
}

type MsgFn = (content: string) => void
export const message: Record<'success' | 'error' | 'warning' | 'info', MsgFn> = {
  success: (c) => ensure().message.success(c),
  error: (c) => ensure().message.error(c),
  warning: (c) => ensure().message.warning(c),
  info: (c) => ensure().message.info(c),
}

interface DialogOptions {
  title: string
  content: string
  positiveText?: string
  negativeText?: string
}

/**
 * 确认弹窗（替代 EP 的 ElMessageBox.confirm）。
 * naive 的 dialog.warning 返回 DialogReactive（非 Promise）——
 * 这里包成 Promise<boolean>：确认 true / 取亮/关闭 false。
 */
export function confirm(o: DialogOptions): Promise<boolean> {
  return new Promise((resolve) => {
    ensure().dialog.warning({
      title: o.title,
      content: o.content,
      positiveText: o.positiveText ?? '确定',
      negativeText: o.negativeText ?? '取消',
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
      onMaskClick: () => resolve(false),
    })
  })
}

/** 警告弹窗（fire-and-forget，不做确认语义）；onClose 在任意方式关闭时回调 */
export function dialogWarning(o: DialogOptions & { onPositive?: () => void; onClose?: () => void }) {
  ensure().dialog.warning({
    title: o.title,
    content: o.content,
    positiveText: o.positiveText ?? '确定',
    negativeText: o.negativeText ?? '取消',
    onPositiveClick: o.onPositive,
    onClose: o.onClose,
  })
}
