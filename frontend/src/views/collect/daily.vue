<script setup lang="ts">
// 今日商机批次：销售每天直接收到一批值得联系的中国出海企业（业务主线交付层）
import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NDataTable, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import * as collectApi from '@/api/collect'
import type { DailyBatch, Lead } from '@/api/collect'
import { EVENT_TYPE_LABELS, gradeTagType } from '@/api/collect'
import { formatTime } from '@/utils/format'
import { message } from '@/utils/feedback'

const router = useRouter()
const batch = ref<DailyBatch | null>(null)
const loading = ref(false)

async function fetchData() {
  loading.value = true
  try {
    batch.value = await collectApi.getDailyBatch()
  } finally {
    loading.value = false
  }
}

async function handleClaim(row: Lead) {
  const lead = await collectApi.claimLead(row.id)
  message.success(`已领取「${lead.name}」，请尽快跟进`)
  fetchData()
}

const columns: DataTableColumns<Lead> = [
  {
    title: '企业',
    key: 'name',
    minWidth: 240,
    render: (row) =>
      h('span', null, [
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/lead/${row.id}`) },
          () => row.name,
        ),
        row.fb_whatsapp
          ? h(NTag, { size: 'small', type: 'error', style: 'margin-left:6px' }, () => 'FB私域')
          : null,
        row.whatsapp_hit
          ? h(NTag, { size: 'small', type: 'success', style: 'margin-left:4px' }, () => 'WA')
          : null,
      ]),
  },
  {
    title: '等级',
    key: 'grade',
    width: 90,
    render: (row) => h(NTag, { size: 'small', type: gradeTagType(row.grade) }, () => `${row.grade} · ${row.score}`),
  },
  {
    title: '行业/市场',
    key: 'industry',
    width: 180,
    render: (row) =>
      row.industry || (row.target_countries.length ? row.target_countries.slice(0, 4).join(' / ') : '—'),
  },
  {
    title: '推荐产品',
    key: 'recommended_products',
    minWidth: 200,
    render: (row) => (row.recommended_products.length ? row.recommended_products.join('、') : '—'),
  },
  {
    title: '跟进人',
    key: 'owner_name',
    width: 110,
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
            { size: 'small', secondary: true, type: 'success', onClick: () => handleClaim(row) },
            () => '领取',
          )
        : h('span', { style: 'font-size:12px;color:#999' }, '已认领'),
  },
]

onMounted(fetchData)
</script>

<template>
  <div class="p-4">
    <n-card :bordered="false" class="mb-4">
      <div class="flex items-center gap-3">
        <span style="font-size: 18px; font-weight: 600">🔥 今日商机</span>
        <n-tag v-if="batch" :bordered="false" type="info">
          {{ batch.date }}
        </n-tag>
        <span v-if="batch" style="color: #666">
          新晋 S/A {{ batch.promoted.length }} 家 · 新增高分商机 {{ batch.new_leads.length }} 家 · 高价值预警
          {{ batch.alerts.length }} 条
        </span>
        <div class="flex-1" />
        <n-button quaternary :loading="loading" @click="fetchData">
          刷新
        </n-button>
      </div>
    </n-card>

    <n-card title="⬆️ 今日新晋 S/A（等级跃升，需求上升期）" :bordered="false" class="mb-4">
      <n-data-table
        :columns="columns"
        :data="batch?.promoted ?? []"
        :loading="loading"
        :row-key="(r: Lead) => r.id"
        :bordered="false"
        size="small"
      />
    </n-card>

    <n-card title="🆕 今日新增高分商机（qualified 且 ≥60 分）" :bordered="false" class="mb-4">
      <n-data-table
        :columns="columns"
        :data="batch?.new_leads ?? []"
        :loading="loading"
        :row-key="(r: Lead) => r.id"
        :bordered="false"
        size="small"
      />
    </n-card>

    <n-card title="⚡ 今日高价值预警事件" :bordered="false">
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
