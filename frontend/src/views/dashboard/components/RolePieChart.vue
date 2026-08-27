<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useAppStore } from '@/stores/app'
import { useChartColors } from './chart-theme'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ data: { name: string; value: number }[] }>()
const appStore = useAppStore()
const colors = useChartColors()

// 以主题色为基色的扩展色板
const palette = computed(() => {
  const base = appStore.primaryColor
  return [base, '#94a3b8', '#f472b6', '#34d399', '#fbbf24', '#60a5fa'].slice(
    0,
    Math.max(props.data.length, 1),
  )
})

const option = computed(() => ({
  tooltip: { trigger: 'item' },
  legend: { bottom: 0, textStyle: { color: colors.value.text } },
  series: [
    {
      type: 'pie',
      radius: ['40%', '65%'],
      data: props.data,
      label: { show: false },
    },
  ],
  color: palette.value,
}))
</script>

<template>
  <VChart :option="option" autoresize style="height: 280px" />
</template>
