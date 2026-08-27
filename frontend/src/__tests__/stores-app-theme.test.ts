import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAppStore, THEME_PRESETS } from '@/stores/app'

describe('app store 主题', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('默认亮色 + 默认主题色为柚子橙（品牌色）', () => {
    const app = useAppStore()
    expect(app.isDark).toBe(false)
    expect(app.primaryColor).toBe('#f59e0b')
  })

  it('setDark 同步 html class 与 localStorage', () => {
    const app = useAppStore()
    app.setDark(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('youzi-app-theme')).toContain('dark')
    app.setDark(false)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('setPrimaryColor 更新 --yz-primary 变量、naive overrides 与 localStorage', () => {
    const app = useAppStore()
    app.setPrimaryColor('#2080f0')
    const v = getComputedStyle(document.documentElement).getPropertyValue('--yz-primary')
    expect(v.trim().toLowerCase()).toBe('#2080f0')
    expect(app.naiveThemeOverrides.common?.primaryColor).toBe('#2080f0')
    expect(localStorage.getItem('youzi-app-primary')).toBe('#2080f0')
  })

  it('setPrimaryColor 忽略非法 hex（3 位缩写 / rgb() 不写入不覆盖）', () => {
    const app = useAppStore()
    app.setPrimaryColor('#7c3aed')
    app.setPrimaryColor('#f00')
    app.setPrimaryColor('rgb(0, 0, 0)')
    expect(app.primaryColor).toBe('#7c3aed')
    expect(localStorage.getItem('youzi-app-primary')).toBe('#7c3aed')
    expect(
      getComputedStyle(document.documentElement).getPropertyValue('--yz-primary').trim().toLowerCase(),
    ).toBe('#7c3aed')
  })

  it('色板预设包含品牌蓝且无重复色值', () => {
    const colors = THEME_PRESETS.map((p) => p.color)
    expect(colors).toContain('#2080f0')
    expect(new Set(colors).size).toBe(colors.length)
  })

  it('naive 主题跟随暗色：亮色 undefined / 暗色 darkTheme', () => {
    const app = useAppStore()
    app.setDark(false)
    expect(app.naiveTheme).toBeUndefined()
    app.setDark(true)
    expect(app.naiveTheme).toBeDefined()
    expect(app.naiveTheme!.name).toBe('dark')
  })

  it('hover/pressed 由主色派生（亮色 hover 偏白、pressed 偏黑）', () => {
    const app = useAppStore()
    app.setPrimaryColor('#f59e0b')
    const c = app.naiveThemeOverrides.common!
    expect(c.primaryColorHover).not.toBe('#f59e0b')
    expect(c.primaryColorPressed).not.toBe('#f59e0b')
  })
})
