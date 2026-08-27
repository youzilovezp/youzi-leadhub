// 图表暗色适配：从自建设计令牌读取文字/网格线颜色（html.dark 下变量整套覆盖）
import { computed } from 'vue'
import { useAppStore } from '@/stores/app'

export function useChartColors() {
  const appStore = useAppStore()
  return computed(() => {
    // 读取 isDark 建立响应依赖，主题切换时重算 option
    void appStore.isDark
    const style = getComputedStyle(document.documentElement)
    const read = (name: string, fallback: string) =>
      style.getPropertyValue(name).trim() || fallback
    return {
      text: read('--yz-text-secondary', '#86909c'),
      line: read('--yz-border', '#e5e6eb'),
    }
  })
}
