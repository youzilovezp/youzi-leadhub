<script setup lang="ts">
// 近 7 日登录趋势（占位数据，接入真实日志后替换）
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useAppStore } from '@/stores/app'
import { useChartColors } from './chart-theme'

use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const appStore = useAppStore()
const colors = useChartColors()
const days = [...Array(7)].map((_, i) => {
  const d = new Date(Date.now() - (6 - i) * 86400000)
  return `${d.getMonth() + 1}/${d.getDate()}`
})
const data = [12, 18, 9, 24, 30, 21, 26] // 占位数据，接入真实日志后替换

const option = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 40, right: 16, top: 20, bottom: 28 },
  xAxis: {
    type: 'category',
    data: days,
    boundaryGap: false,
    axisLabel: { color: colors.value.text },
    axisLine: { lineStyle: { color: colors.value.line } },
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: colors.value.text },
    splitLine: { lineStyle: { color: colors.value.line } },
  },
  series: [
    {
      type: 'line',
      smooth: true,
      data,
      areaStyle: { opacity: 0.15 },
      lineStyle: { width: 2.5 },
      itemStyle: { color: appStore.primaryColor },
    },
  ],
}))
</script>

<template>
  <VChart :option="option" autoresize style="height: 280px" />
</template>
