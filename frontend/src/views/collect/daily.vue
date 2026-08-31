<script setup lang="ts">
// 今日商机批次：销售每天直接收到一批值得联系的中国出海企业（业务主线交付层）。
// 三问上一线：为什么值得联系 / 应该卖什么 / 应该找谁 三列直读
// 后端逐行挂载的 three_questions（不齐备的行已被后端过滤）。
// 批次空转时给出根因诊断（管道健康度）。
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NCard, NDataTable, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import * as collectApi from '@/api/collect'
import type { DailyBatch, DailyBatchRow } from '@/api/collect'
import { EVENT_TYPE_LABELS, gradeTagType } from '@/api/collect'
import { formatTime } from '@/utils/format'
import { message } from '@/utils/feedback'

const router = useRouter()
const batch = ref<DailyBatch | null>(null)
const loading = ref(false)
const claimingId = ref<number | null>(null)
const health = ref<collectApi.CollectStats['pipeline_health'] | null>(null)

const batchEmpty = computed(
  () =>
    batch.value &&
    batch.value.promoted.length === 0 &&
    batch.value.new_leads.length === 0 &&
    batch.value.alerts.length === 0,
)

async function fetchData() {
  loading.value = true
  try {
    batch.value = await collectApi.getDailyBatch()
    // 批次为空时顺带取管道健康度——把「为什么是空的」讲清楚，而不是留白
    if (batchEmpty.value) {
      try {
        health.value = (await collectApi.getStats()).pipeline_health
      } catch {
        health.value = null
      }
    }
  } finally {
    loading.value = false
  }
}

async function handleClaim(row: DailyBatchRow) {
  claimingId.value = row.id
  try {
    const lead = await collectApi.claimLead(row.id)
    message.success(`已领取「${lead.name}」，请尽快跟进`)
    fetchData()
  } finally {
    claimingId.value = null
  }
}

/** 「为什么值得联系」：三问之 why（意向分命中最强信号，读 three_questions，无额外请求） */
function keySignalChips(row: DailyBatchRow): Array<{ label: string; type: 'error' | 'warning' | 'success' | 'info' }> {
  return (row.three_questions?.why ?? []).map((w) => ({ label: w.label, type: 'success' as const }))
}

const columns: DataTableColumns<DailyBatchRow> = [
  {
    title: '企业',
    key: 'name',
    minWidth: 220,
    render: (row) =>
      h('span', null, [
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/lead/${row.id}`) },
          () => row.name,
        ),
      ]),
  },
  {
    title: '等级',
    key: 'grade',
    width: 86,
    render: (row) => h(NTag, { size: 'small', type: gradeTagType(row.grade) }, () => `${row.grade} · ${row.score}`),
  },
  {
    title: '为什么值得联系',
    key: 'key_signals',
    minWidth: 260,
    render: (row) => {
      const chips = keySignalChips(row)
      return chips.length
        ? h(
            'div',
            { style: 'display:flex;flex-wrap:wrap;gap:4px' },
            chips.map((c) => h(NTag, { size: 'small', type: c.type, bordered: false }, () => c.label)),
          )
        : h('span', { style: 'color:#999' }, '—')
    },
  },
  {
    title: '应该卖什么',
    key: 'recommended_products',
    minWidth: 180,
    render: (row) => {
      const names = (row.three_questions?.what.products ?? []).map((p) => p.name)
      return names.length ? names.join('、') : '—'
    },
  },
  {
    title: '应该找谁',
    key: 'contacts',
    width: 200,
    render: (row) => {
      const who = row.three_questions?.who
      // 真实联系人 / WA 号码优先；没有则按信号派生的目标角色（销售至少知道该找什么职位）
      if (who && (who.contacts.length || who.whatsapp_numbers.length)) {
        return h('div', { style: 'font-size:12px;line-height:1.7' }, [
          ...who.contacts.map((c) =>
            h('div', { key: c.email || c.name }, `${c.name}${c.title ? `（${c.title}）` : ''}`),
          ),
          ...who.whatsapp_numbers.map((n) => h('div', { key: n }, `WA：${n}`)),
          who.whatsapp_url
            ? h(
                NButton,
                { size: 'tiny', quaternary: true, type: 'primary', onClick: () => window.open(who.whatsapp_url!, '_blank') },
                () => 'WA 建联',
              )
            : null,
        ])
      }
      const roles = who?.roles.map((r) => r.role) ?? []
      return h(
        'span',
        { style: 'font-size:12px;color:#999' },
        roles.length ? `建议找：${roles.join(' / ')}` : '联系人待补',
      )
    },
  },
  {
    title: '跟进人',
    key: 'owner_name',
    width: 100,
    render: (row) => row.owner_name || '共享池',
  },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    render: (row) =>
      !row.owner_id
        ? h(
            NButton,
            {
              size: 'small',
              secondary: true,
              type: 'success',
              loading: claimingId.value === row.id,
              disabled: claimingId.value !== null,
              onClick: () => handleClaim(row),
            },
            () => '领取',
          )
        : h('span', { style: 'font-size:12px;color:#999' }, '已认领'),
  },
]

onMounted(fetchData)
</script>

<template>
  <div class="p-4">
    <n-card
      :bordered="false"
      class="mb-4"
    >
      <div class="flex items-center gap-3">
        <span style="font-size: 18px; font-weight: 600">🔥 今日商机</span>
        <n-tag
          v-if="batch"
          :bordered="false"
          type="info"
        >
          {{ batch.date }}
        </n-tag>
        <span
          v-if="batch"
          style="color: #666"
        >
          新晋 S/A {{ batch.promoted.length }} 家 · 新增高分商机 {{ batch.new_leads.length }} 家 · 高价值预警
          {{ batch.alerts.length }} 条
        </span>
        <div class="flex-1" />
        <n-button
          quaternary
          :loading="loading"
          @click="fetchData"
        >
          刷新
        </n-button>
      </div>
    </n-card>

    <!-- 批次口径：常驻说明三切片怎么来的、进入条件是什么 -->
    <n-alert
      type="info"
      title="这份名单怎么来的"
      class="mb-4"
    >
      <ul style="margin: 0; padding-left: 18px; line-height: 1.9">
        <li>
          只收<b>中国出海企业（有出海证据）且总分 ≥60（S/A 级）</b>——分数不够的进培育池养着，
          非中国企业不进名单。
        </li>
        <li>
          <b>批次只收三问齐备的线索</b>（≥2 条证据 + 有推荐产品 + 有建联入口或明确角色）——
          每行都答得出「为什么值得联系 / 应该卖什么 / 应该找谁」。
        </li>
        <li>
          <b>① 新晋 S/A</b>：原来分数不高、今天因为新证据（发现 WhatsApp、广告在投、联系人补全等）升上来的——
          需求正在升温，最值得马上联系。<b>② 新增高分</b>：今天第一次入库就够 60 分。<b>③ 预警</b>：
          发现 WhatsApp 入口、Facebook 主页挂 WhatsApp 按钮、SaaS 需求信号等高价值动态。
        </li>
        <li>
          <b>怎么用</b>：看「为什么值得联系」→「应该卖什么」→「应该找谁」，领取后跟进；
          谈成后跟进状态选「成交」，首页的「本月成交」会自动统计。
        </li>
      </ul>
    </n-alert>

    <!-- 空批次时给出原因和下一步，不留白 -->
    <n-alert
      v-if="batchEmpty && !loading"
      type="warning"
      title="今天的名单是空的，怎么打开"
      class="mb-4"
    >
      <ul style="margin: 0; padding-left: 18px; line-height: 1.9">
        <li v-if="health && !health.meta_ads_ready">
          <b>还没接广告库</b>——高分商机主要靠它（谁在向海外投广告、主页有没有挂 WhatsApp）。
          在 backend/.env 配置 META_ADS_ACCESS_TOKEN 后重启即可，免费申请：
          <a
            href="https://www.facebook.com/ads/archive/api"
            target="_blank"
            rel="noopener"
          >facebook.com/ads/archive/api</a>
        </li>
        <li v-if="health && !health.scheduler_enabled">
          <b>定时任务还没开</b>：采集不会每天自动跑。可在「采集任务」里手动执行，
          或把 backend/.env 的 SCHEDULER_ENABLED 改为 true 让它每天自己转
        </li>
        <li v-if="health && health.qualified_leads === 0">
          <b>池子里还没有中国出海企业</b>：先在「采集任务」跑一次招聘监控（用默认关键词即可），
          系统会自动补官网和信号
        </li>
        <li v-if="!health || (health.meta_ads_ready && health.scheduler_enabled && health.qualified_leads > 0)">
          今天确实没有新变化——分数不够的线索都在培育池养着，去
          <router-link to="/collect/lead">
            线索列表
          </router-link> 按等级筛选查看
        </li>
      </ul>
    </n-alert>

    <n-card
      title="⬆️ 今日新晋 S/A（等级跃升，需求上升期）"
      :bordered="false"
      class="mb-4"
    >
      <n-data-table
        :columns="columns"
        :data="batch?.promoted ?? []"
        :loading="loading"
        :row-key="(r: DailyBatchRow) => r.id"
        :bordered="false"
        size="small"
      />
    </n-card>

    <n-card
      title="🆕 今日新增高分商机（qualified 且 ≥60 分）"
      :bordered="false"
      class="mb-4"
    >
      <n-data-table
        :columns="columns"
        :data="batch?.new_leads ?? []"
        :loading="loading"
        :row-key="(r: DailyBatchRow) => r.id"
        :bordered="false"
        size="small"
      />
    </n-card>

    <n-card
      title="⚡ 今日高价值预警事件"
      :bordered="false"
    >
      <n-data-table
        :columns="[
          { title: '时间', key: 'created_at', width: 160, render: (r: any) => formatTime(r.created_at) },
          {
            title: '企业',
            key: 'lead_name',
            minWidth: 200,
            render: (r: any) =>
              h(NButton, { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/lead/${r.lead_id}`) }, () => r.lead_name),
          },
          { title: '事件', key: 'event_type', width: 130, render: (r: any) => EVENT_TYPE_LABELS[r.event_type] ?? r.event_type },
          { title: '说明', key: 'note' },
        ]"
        :data="batch?.alerts ?? []"
        :loading="loading"
        :row-key="(r: any) => `${r.lead_id}-${r.created_at}`"
        :bordered="false"
        size="small"
      />
    </n-card>
  </div>
</template>
