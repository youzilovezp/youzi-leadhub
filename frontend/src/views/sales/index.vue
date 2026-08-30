<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import * as salesApi from '@/api/sales'
import type { DataSourceStat, FunnelStats, LeaderboardRow } from '@/api/sales'
import { getStats } from '@/api/collect'
import { FUNNEL_STAGES, followStatusLabel } from '@/api/collect'
import { formatTime } from '@/utils/format'

const stats = ref<Awaited<ReturnType<typeof getStats>> | null>(null)
const funnel = ref<FunnelStats | null>(null)
const leaderboard = ref<LeaderboardRow[]>([])
const sources = ref<DataSourceStat[]>([])
const loading = ref(false)

async function fetchAll() {
  loading.value = true
  try {
    ;[stats.value, funnel.value, leaderboard.value, sources.value] = await Promise.all([
      getStats(),
      salesApi.getFunnel(),
      salesApi.getLeaderboard(),
      salesApi.getDataSources(),
    ])
  } finally {
    loading.value = false
  }
}

// ---------- 漏斗（§38）：各阶段线索数 → 转化率 ----------

const funnelRows = computed(() => {
  if (!funnel.value) return []
  const stages = funnel.value.stages || {}
  const top = FUNNEL_STAGES.slice(0, 4).reduce((m, s) => Math.max(m, stages[s] ?? 0), 0) // 前 4 阶段为「进入漏斗」基数
  const base = top || 1
  return FUNNEL_STAGES.map((s) => {
    const count = stages[s] ?? 0
    return {
      stage: s,
      label: followStatusLabel(s),
      count,
      percent: Math.round((count * 100) / base),
    }
  })
})

const oppStageRows = computed(() => {
  if (!funnel.value) return []
  const opps = funnel.value.opportunities || {}
  return ['opportunity', 'quote', 'negotiation', 'won', 'lost'].map((s) => ({
    stage: s,
    label: salesApi.opportunityStageLabel(s),
    count: opps[s]?.count ?? 0,
    amount: opps[s]?.amount ?? 0,
  }))
})

function fmtAmount(n: number): string {
  return n >= 10000 ? `${(n / 10000).toFixed(1)} 万` : String(n)
}

// ---------- 排行榜（§40） ----------

const boardColumns: DataTableColumns<LeaderboardRow> = [
  { title: '#', key: 'rank', width: 50, render: (_r, i) => String((i ?? 0) + 1) },
  { title: '销售', key: 'owner_name', render: (r) => r.owner_name || `#${r.owner_id}` },
  { title: '持有线索', key: 'leads', width: 90 },
  { title: '商机', key: 'opportunities', width: 80 },
  { title: '成交', key: 'won', width: 80 },
  {
    title: '成交金额',
    key: 'won_amount',
    width: 110,
    render: (r) => (r.won_amount ? `¥${fmtAmount(r.won_amount)}` : '—'),
  },
]

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
              本月成交（¥{{ fmtAmount(stats?.month_won_amount ?? 0) }}）
            </div>
          </div>
        </n-card>
      </div>

      <div
        class="grid gap-4"
        style="grid-template-columns: 1fr 1fr"
      >
        <!-- 销售漏斗（§38） -->
        <n-card
          size="small"
          title="销售漏斗（线索阶段）"
        >
          <div
            v-for="row in funnelRows"
            :key="row.stage"
            class="dim-row"
          >
            <span class="dim-label">{{ row.label }}</span>
            <n-progress
              type="line"
              :percentage="row.percent"
              :height="10"
              :color="row.stage === 'won' ? '#18a058' : undefined"
              class="dim-bar"
            />
            <span class="dim-score">{{ row.count }}<span class="dim-weight">（{{ row.percent }}%）</span></span>
          </div>
          <n-divider style="margin: 10px 0" />
          <div class="opp-grid">
            <div
              v-for="row in oppStageRows"
              :key="row.stage"
              class="opp-cell"
            >
              <div class="opp-count">
                {{ row.count }}
              </div>
              <div class="opp-label">
                {{ row.label }}
              </div>
              <div
                v-if="row.amount"
                class="opp-amount"
              >
                ¥{{ fmtAmount(row.amount) }}
              </div>
            </div>
          </div>
          <div class="opp-summary">
            成交金额 ¥{{ fmtAmount(funnel?.won_amount ?? 0) }} · ARPU ¥{{ fmtAmount(funnel?.arpu ?? 0) }}
          </div>
        </n-card>

        <!-- 排行榜（§40） -->
        <n-card
          size="small"
          title="销售排行榜"
        >
          <n-data-table
            :columns="boardColumns"
            :data="leaderboard"
            :row-key="(r: LeaderboardRow) => r.owner_id"
            size="small"
          />
        </n-card>
      </div>

      <!-- 数据源管理（§33） -->
      <n-card
        size="small"
        title="数据源管理"
        class="mt-4"
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
.dim-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 0;
}
.dim-label {
  width: 82px;
  flex-shrink: 0;
  font-size: 13px;
}
.dim-bar {
  flex: 1;
}
.dim-score {
  width: 96px;
  text-align: right;
  font-size: 13px;
  flex-shrink: 0;
}
.dim-weight {
  color: var(--yz-text-secondary, #999);
  font-size: 12px;
}
.opp-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
}
.opp-cell {
  text-align: center;
  padding: 8px 4px;
  background: var(--yz-bg-page, #f7f7f7);
  border-radius: 4px;
}
.opp-count {
  font-size: 18px;
  font-weight: 600;
}
.opp-label {
  font-size: 12px;
  color: var(--yz-text-secondary, #888);
}
.opp-amount {
  font-size: 11px;
  color: #18a058;
}
.opp-summary {
  margin-top: 10px;
  font-size: 12px;
  color: var(--yz-text-secondary, #888);
}
@media (max-width: 1100px) {
  .stat-cards {
    grid-template-columns: repeat(3, 1fr) !important;
  }
}
</style>
