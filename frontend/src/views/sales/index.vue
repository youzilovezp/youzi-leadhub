<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import * as salesApi from '@/api/sales'
import type { DataSourceStat } from '@/api/sales'
import { getStats, getCostHealth, gradeTagType } from '@/api/collect'
import type { CostHealth, CostHealthBucket } from '@/api/collect'
import { formatTime } from '@/utils/format'

const stats = ref<Awaited<ReturnType<typeof getStats>> | null>(null)
const sources = ref<DataSourceStat[]>([])
const costHealth = ref<CostHealth | null>(null)
const loading = ref(false)

async function fetchAll() {
  loading.value = true
  try {
    const results = await Promise.allSettled([
      getStats(),
      salesApi.getDataSources(),
      getCostHealth(),
    ])
    if (results[0].status === 'fulfilled') stats.value = results[0].value
    if (results[1].status === 'fulfilled') sources.value = results[1].value
    // cost-health 是 admin 端点——非管理员可能 403，无 token 也 401；都吃掉
    if (results[2].status === 'fulfilled') costHealth.value = results[2].value
  } finally {
    loading.value = false
  }
}

// ---------- 数据源（§33） ----------

/** 流水线阶段：依赖关系可视化——发现层产出种子，信号层补招聘信号；
 *  网站富化是内部复核步骤（非数据源），不出现在本列表（2026-09-13 口径）
 *  移除 web_search + meta_ads（无 token + Ads Library 审核中） */
const PIPELINE_STAGE: Record<string, { label: string; type: 'success' | 'info' | 'warning' }> = {
  b2b_supplier: { label: '① 发现层·线索', type: 'info' },
  job_posting: { label: '① 发现层·线索', type: 'info' },
  career_site: { label: '② 信号层·招聘页巡检', type: 'info' },
}

/** logic_note 是多段【标题】文本——逐段渲染成小标题+正文，可读性优于整坨 */
function renderLogicNote(note: string) {
  const blocks = (note || '').split('\n').filter(Boolean)
  return h(
    'div',
    { style: 'padding:4px 0;max-width:960px;line-height:1.8;font-size:13px' },
    blocks.map(b => {
      const m = b.match(/^【(.+?)】(.*)$/s)
      return m
        ? h('p', { style: 'margin:6px 0' }, [
            h('b', { style: 'color:var(--yz-primary,#2080f0)' }, `${m[1]}：`),
            m[2],
          ])
        : h('p', { style: 'margin:6px 0' }, b)
    }),
  )
}

const sourceColumns: DataTableColumns<DataSourceStat> = [
  {
    type: 'expand',
    renderExpand: (row) =>
      row.logic_note
        ? renderLogicNote(row.logic_note)
        : h('span', { style: 'color:#999' }, '暂无说明'),
  },
  { title: '数据源', key: 'title' },
  {
    title: '流水线阶段',
    key: 'stage',
    width: 150,
    render: (r) => {
      const s = PIPELINE_STAGE[r.collector]
      return s ? h(NTag, { size: 'small', type: s.type, bordered: false }, () => s.label) : '—'
    },
  },
  { title: '状态', key: 'status', width: 80, render: (r) => h(NTag, { size: 'small', type: r.status === 'active' ? 'success' : 'default' }, () => (r.status === 'active' ? '活跃' : '闲置')) },
  { title: '任务数', key: 'tasks', width: 80 },
  { title: '成功率', key: 'success_rate', width: 90, render: (r) => (r.success_rate === null ? '—' : `${r.success_rate}%`) },
  { title: '错误率', key: 'error_rate', width: 90, render: (r) => (r.error_rate === null ? '—' : `${r.error_rate}%`) },
  { title: '新增线索', key: 'leads_added', width: 100 },
  { title: '合并', key: 'leads_merged', width: 80 },
  { title: '最后运行', key: 'last_run_at', width: 160, render: (r) => (r.last_run_at ? formatTime(r.last_run_at) : '从未') },
]

// ---------- 采集健康度（2026-09-10 telemetry） ----------
const costHealthColumns: DataTableColumns<CostHealthBucket> = [
  {
    title: '企业',
    key: 'name',
    width: 200,
    fixed: 'left',
    render: (r) => h(NTag, { size: 'small', type: gradeTagType(r.grade), bordered: false }, () => `${r.grade} ${r.name}`),
  },
  {
    title: '结果',
    key: 'outcome',
    width: 80,
    render: (r) =>
      h(
        NTag,
        { size: 'small', type: r.outcome === 'success' ? 'success' : r.outcome === 'fail' ? 'error' : 'default' },
        () => r.outcome,
      ),
  },
  { title: 'HTTP', key: 'http_calls', width: 70 },
  { title: '渲染', key: 'render_calls', width: 70 },
  { title: '内页', key: 'inner_pages_fetched', width: 70 },
  { title: '信号数', key: 'signals_total', width: 80 },
  { title: '耗时(ms)', key: 'elapsed_ms', width: 100 },
  {
    title: '原因',
    key: 'reasons',
    render: (r) =>
      h(
        'div',
        { style: 'display:flex; flex-direction:column; gap:2px' },
        r.reasons.map((reason) =>
          h('span', { style: 'font-size:12px; color: var(--yz-warning, #f0a020)' }, `⚠️ ${reason}`),
        ),
      ),
  },
]

onMounted(fetchAll)
</script>

<template>
  <div class="page">
    <n-spin :show="loading">
      <!-- 依赖关系可视化：流水线怎么转、哪步是自动的 -->
      <n-alert
        type="info"
        title="线索是怎么进来的（全自动流水线）"
        class="mb-4"
      >
        <div style="line-height: 2">
          🔍 <b>① 找线索</b>（B2B 出口目录 / 搜索引擎 / 招聘监控 / 广告库）
          <span style="color: #18a058">→ 自动 →</span>
          🔄 <b>② 补信息</b>：搜官网、识别 WhatsApp 与出海信号、重新评分
          <span style="color: #18a058">→</span>
          ⚖️ <b>③ 准入排序</b>：中国出海企业才进池，按购买意向打分分级
          <span style="color: #18a058">→</span>
          🔥 <b>④ 交付</b>：今日商机 / 高价值预警 → 领取跟进 → 成交
          <div style="font-size: 12px; color: #888; margin-top: 2px">
            ② 无需手动执行：① 的任务跑完系统自动补信息（一小时内已补过则跳过），并按等级定期复查。
          </div>
        </div>
      </n-alert>
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
        <template #header-extra>
          <span style="font-size: 12px; color: var(--yz-text-secondary, #888)">
            点每行左侧箭头展开「爬取逻辑与循环复核说明」（抓什么 / 怎么滤 / 准确机制 / 复核节奏 / 已知边界）
          </span>
        </template>
        <n-data-table
          :columns="sourceColumns"
          :data="sources"
          :row-key="(r: DataSourceStat) => r.collector"
          size="small"
        />
      </n-card>

      <!-- 采集健康度（2026-09-10 telemetry，admin 端） -->
      <n-card
        size="small"
        title="采集健康度"
        class="mt-4"
      >
        <template #header-extra>
          <span style="font-size: 12px; color: var(--yz-text-secondary, #888)">
            运维口径：富化成本/信号命中/需人工巡检的线索 ——
            <n-text code style="font-size: 11px">{{ costHealth?.summary_text }}</n-text>
          </span>
        </template>
        <div class="grid gap-3 cost-metrics mb-3">
          <div class="cost-metric">
            <div class="cost-metric-value">
              {{ costHealth?.with_cost ?? 0 }} / {{ costHealth?.total_leads ?? 0 }}
            </div>
            <div class="cost-metric-label">
              已富化 / 总线索
            </div>
          </div>
          <div class="cost-metric">
            <div class="cost-metric-value stat-success">
              {{ costHealth?.success_count ?? 0 }}
            </div>
            <div class="cost-metric-label">
              富化成功
            </div>
          </div>
          <div class="cost-metric">
            <div class="cost-metric-value stat-fail">
              {{ costHealth?.fail_count ?? 0 }}
            </div>
            <div class="cost-metric-label">
              富化失败
            </div>
          </div>
          <div class="cost-metric">
            <div class="cost-metric-value">
              {{ costHealth?.avg_http_calls ?? 0 }}
            </div>
            <div class="cost-metric-label">
              均 HTTP 调用
            </div>
          </div>
          <div class="cost-metric">
            <div class="cost-metric-value">
              {{ costHealth?.avg_render_calls ?? 0 }}
            </div>
            <div class="cost-metric-label">
              均浏览器渲染
            </div>
          </div>
          <div class="cost-metric">
            <div class="cost-metric-value">
              {{ costHealth?.needs_inspection?.length ?? 0 }}
            </div>
            <div class="cost-metric-label">
              需巡检
            </div>
          </div>
        </div>
        <n-data-table
          v-if="(costHealth?.needs_inspection?.length ?? 0) > 0"
          :columns="costHealthColumns"
          :data="costHealth!.needs_inspection"
          :row-key="(r: CostHealthBucket) => r.lead_id"
          size="small"
          :max-height="400"
        />
        <n-empty
          v-else
          description="当前没有需人工巡检的线索（cost telemetry 累计后会显示）"
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

/* 采集健康度（2026-09-10） */
.cost-metrics {
  grid-template-columns: repeat(6, 1fr);
}
.cost-metric {
  text-align: center;
  padding: 6px 0;
}
.cost-metric-value {
  font-size: 18px;
  font-weight: 600;
}
.cost-metric-label {
  font-size: 11px;
  color: var(--yz-text-secondary, #888);
  margin-top: 2px;
}
.stat-success {
  color: #18a058;
}
.stat-fail {
  color: #d03050;
}
@media (max-width: 1100px) {
  .stat-cards,
  .cost-metrics {
    grid-template-columns: repeat(3, 1fr) !important;
  }
}
</style>
