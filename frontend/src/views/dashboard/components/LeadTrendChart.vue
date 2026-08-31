<script setup lang="ts">
// 近 7 日新增线索趋势（真实数据：/collect/leads/trend）。
// 线索增长是销售系统首页该看的曲线；数据来自 /collect/leads/trend。
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { use } from 'echarts/core'
import * as collectApi from '@/api/collect'
import { useAppStore } from '@/stores/app'
import { useChartColors } from './chart-theme'

use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const appStore = useAppStore()
const colors = useChartColors()
const rows = ref<Array<{ date: string; total: number; qualified: number }>>([])

onMounted(async () => {
  try {
    rows.value = await collectApi.getLeadTrend(7)
  } catch {
    rows.value = []
  }
})

const option = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['新增线索', '其中中国出海'], textStyle: { color: colors.value.text } },
  grid: { left: 40, right: 16, top: 32, bottom: 28 },
  xAxis: {
    type: 'category',
    data: rows.value.map((r) => r.date.slice(5)),
    boundaryGap: false,
    axisLabel: { color: colors.value.text },
    axisLine: { lineStyle: { color: colors.value.line } },
  },
  yAxis: {
    type: 'value',
    minInterval: 1,
    axisLabel: { color: colors.value.text },
    splitLine: { lineStyle: { color: colors.value.line } },
  },
  series: [
    {
      name: '新增线索',
      type: 'line',
      smooth: true,
      data: rows.value.map((r) => r.total),
      areaStyle: { opacity: 0.12 },
      lineStyle: { width: 2.5 },
      itemStyle: { color: appStore.primaryColor },
    },
    {
      name: '其中中国出海',
      type: 'line',
      smooth: true,
      data: rows.value.map((r) => r.qualified),
      areaStyle: { opacity: 0.12 },
      lineStyle: { width: 2.5 },
      itemStyle: { color: '#18a058' },
    },
  ],
}))
</script>

<template>
  <VChart
    :option="option"
    autoresize
    style="height: 280px"
  />
</template>
