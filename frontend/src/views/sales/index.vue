<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import * as salesApi from '@/api/sales'
import type { DataSourceStat } from '@/api/sales'
import { getStats } from '@/api/collect'
import { formatTime } from '@/utils/format'

const stats = ref<Awaited<ReturnType<typeof getStats>> | null>(null)
const sources = ref<DataSourceStat[]>([])
const loading = ref(false)

async function fetchAll() {
  loading.value = true
  try {
    ;[stats.value, sources.value] = await Promise.all([getStats(), salesApi.getDataSources()])
  } finally {
    loading.value = false
  }
}

// ---------- 数据源（§33） ----------

const sourceColumns: DataTableColumns<DataSourceStat> = [
  { title: '数据源', key: 'title' },
  { title: '状态', key: 'status', width: 80, render: (r) => h(NTag, { size: 'small', type: r.status === 'active' ? 'success' : 'default' }, () => (r.status === 'active' ? '活跃' : '闲置')) },
  { title: '任务数', key: 'tasks', width: 80 },
  { title: '成功率', key: 'success_rate', width: 90, render: (r) => (r.success_rate === null ? '—' : `${r.success_rate}%`) },
  { title: '错误率', key: 'error_rate', width: 90, render: (r) => (r.error_rate === null ? '—' : `${r.error_rate}%`) },
  { title: '新增线索', key: 'leads_added', width: 100 },
  { title: '合并', key: 'leads_merged', width: 80 },
  { title: '最后运行', key: 'last_run_at', width: 160, render: (r) => (r.last_run_at ? formatTime(r.last_run_at) : '从未') },
]

onMounted(fetchAll)
</script>

<template>
  <div class="page">
    <n-spin :show="loading">
      <!-- 月度指标（§39） -->
      <div
        class="stat-cards grid gap-4 mb-4"
        style="grid-template-columns: repeat(6, 1fr)"
      >
        <n-card size="small">
          <div class="metric">
            <div class="metric-value">
              {{ stats?.total_leads ?? 0 }}
            </div>
            <div class="metric-label">
              企业总数
            </div>
          </div>
        </n-card>
        <n-card size="small">
          <div class="metric">
            <div class="metric-value stat-cn">
              {{ stats?.cn_leads ?? 0 }}
            </div>
            <div class="metric-label">
              中国出海
            </div>
          </div>
        </n-card>
        <n-card size="small">
          <div class="metric">
            <div class="metric-value stat-wa">
              {{ stats?.grade_counts?.S ?? 0 }}
            </div>
            <div class="metric-label">
              S 级 Lead
            </div>
          </div>
        </n-card>
        <n-card size="small">
          <div class="metric">
            <div class="metric-value stat-wa">
              {{ stats?.grade_counts?.A ?? 0 }}
            </div>
            <div class="metric-label">
              A 级 Lead
            </div>
          </div>
        </n-card>
        <n-card size="small">
          <div class="metric">
            <div class="metric-value">
              {{ stats?.month_new_leads ?? 0 }}
            </div>
            <div class="metric-label">
              本月新增
            </div>
          </div>
        </n-card>
        <n-card size="small">
          <div class="metric">
            <div class="metric-value">
              {{ stats?.month_won_count ?? 0 }}
            </div>
            <div class="metric-label">
              本月成交
            </div>
          </div>
        </n-card>
      </div>

      <!-- 数据源管理（§33） -->
      <n-card
        size="small"
        title="数据源管理"
      >
        <n-data-table
          :columns="sourceColumns"
          :data="sources"
          :row-key="(r: DataSourceStat) => r.collector"
          size="small"
        />
      </n-card>
    </n-spin>
  </div>
</template>

<style scoped>
.metric {
  text-align: center;
}
.metric-value {
  font-size: 22px;
  font-weight: 600;
}
.metric-label {
  font-size: 12px;
  color: var(--yz-text-secondary, #888);
  margin-top: 2px;
}
.stat-wa {
  color: #18a058;
}
.stat-cn {
  color: #2080f0;
}
@media (max-width: 1100px) {
  .stat-cards {
    grid-template-columns: repeat(3, 1fr) !important;
  }
}
</style>
