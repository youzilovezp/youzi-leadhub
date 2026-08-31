import { defineStore } from 'pinia'
import { computed, watch, type Ref } from 'vue'
import { useDark, useStorage, type BasicColorSchema } from '@vueuse/core'
import { darkTheme, type GlobalThemeOverrides } from 'naive-ui'

/** 主题色预设（与设计文档一致） */
export interface ThemePreset {
  name: string
  color: string
}

export const THEME_PRESETS: ThemePreset[] = [
  { name: '翡翠绿', color: '#10b981' },
  { name: '柚子橙', color: '#f59e0b' },
  { name: '品牌蓝', color: '#2080f0' },
  { name: '紫罗兰', color: '#7c3aed' },
  { name: '赤霞红', color: '#ef4444' },
  { name: '黛青蓝', color: '#0ea5e9' },
]

/** 启动默认主题色：翡翠绿 */
export const DEFAULT_PRIMARY = THEME_PRESETS[0]?.color ?? '#10b981'

// 主题色持久化 key v2：v1（youzi-app-primary）时期默认色曾是柚子橙，
// 老浏览器里残留的 #f59e0b 会盖过代码默认——版本化换 key 让旧值一次性失效
const PRIMARY_KEY = 'youzi-app-primary@2'
const LEGACY_PRIMARY_KEY = 'youzi-app-primary'

/** 合法主题色：仅 6 位 hex（非法输入忽略，防 NaN 通道写坏变量/overrides） */
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/

function hex2rgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}

/** 与另一色混合（ratio 为自身占比）；Naive hover/pressed 由主色派生，保持 EP 时代的观感连续 */
function mix(color: string, target: string, ratio: number): string {
  const [r1, g1, b1] = hex2rgb(color)
  const [r2, g2, b2] = hex2rgb(target)
  const m = (a: number, b: number) =>
    Math.round(a * ratio + b * (1 - ratio)).toString(16).padStart(2, '0')
  return `#${m(r1, r2)}${m(g1, g2)}${m(b1, b2)}`
}

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = useStorage('youzi-app-sidebar-collapsed', false)

  // vueuse useDark：读写 html.dark class + localStorage('youzi-app-theme')
  const themeStorage = useStorage<BasicColorSchema>(
    'youzi-app-theme',
    'auto',
    undefined,
    { flush: 'sync' },
  )
  const isDark = useDark({
    storageKey: 'youzi-app-theme',
    storageRef: computed({
      get: () => themeStorage.value,
      set: (v: BasicColorSchema) => {
        themeStorage.value = v
      },
    }) as Ref<BasicColorSchema>,
  })
  // useDark 内部 class 切换是 post-flush（微任务），补一个 sync watch 保证同步可见
  watch(
    isDark,
    (v) => {
      document.documentElement.classList.toggle('dark', v)
    },
    { immediate: true, flush: 'sync' },
  )
  // 一次性清理 v1 残留（旧默认柚子橙等），此后所有浏览器默认翡翠绿
  if (localStorage.getItem(LEGACY_PRIMARY_KEY) !== null) {
    localStorage.removeItem(LEGACY_PRIMARY_KEY)
  }
  const primaryColor = useStorage(PRIMARY_KEY, DEFAULT_PRIMARY, undefined, {
    flush: 'sync',
  })

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }
  function toggleDark() {
    isDark.value = !isDark.value
  }
  function setDark(v: boolean) {
    isDark.value = v
  }
  function setPrimaryColor(color: string) {
    if (!HEX_COLOR_RE.test(color)) return
    primaryColor.value = color
  }

  // ---------- Naive UI 主题（App.vue 的 n-config-provider 消费） ----------
  const naiveTheme = computed(() => (isDark.value ? darkTheme : undefined))
  const naiveThemeOverrides = computed<GlobalThemeOverrides>(() => {
    const c = primaryColor.value
    if (!HEX_COLOR_RE.test(c)) return {}
    return {
      common: {
        primaryColor: c,
        primaryColorHover: mix(c, '#ffffff', 0.8),
        primaryColorPressed: mix(c, '#000000', 0.8),
        primaryColorSuppl: mix(c, '#ffffff', 0.8),
        borderRadius: '6px',
      },
    }
  })

  // 自建令牌 --yz-primary 写到 documentElement（tailwind/登录页渐变/图表引用）
  watch(
    [primaryColor, isDark],
    ([c]) => {
      if (HEX_COLOR_RE.test(c)) {
        document.documentElement.style.setProperty('--yz-primary', c)
      }
    },
    { immediate: true, flush: 'sync' },
  )

  return {
    sidebarCollapsed,
    toggleSidebar,
    isDark,
    toggleDark,
    setDark,
    primaryColor,
    setPrimaryColor,
    naiveTheme,
    naiveThemeOverrides,
  }
})
