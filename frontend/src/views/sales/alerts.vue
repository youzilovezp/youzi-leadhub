<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import * as salesApi from '@/api/sales'
import type { LeadAlert } from '@/api/sales'
import { EVENT_TYPE_LABELS, gradeTagType } from '@/api/collect'
import { formatTime } from '@/utils/format'

const router = useRouter()
const alerts = ref<LeadAlert[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)

async function fetchAlerts() {
  loading.value = true
  try {
    const data = await salesApi.listAlerts(page.value, pageSize)
    alerts.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function handlePageChange(p: number) {
  page.value = p
  fetchAlerts()
}

const columns: DataTableColumns<LeadAlert> = [
  {
    title: '时间',
    key: 'created_at',
    width: 160,
    render: (r) => formatTime(r.created_at),
  },
  {
    title: '企业',
    key: 'lead_name',
    minWidth: 200,
    render: (r) =>
      h(
        NButton,
        { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/lead/${r.lead_id}`) },
        () => r.lead_name,
      ),
  },
  {
    title: '等级',
    key: 'lead_grade',
    width: 70,
    render: (r) => h(NTag, { size: 'small', type: gradeTagType(r.lead_grade) }, () => r.lead_grade),
  },
  {
    title: '事件',
    key: 'event_type',
    width: 140,
    render: (r) => h(NTag, { size: 'small', type: 'warning', bordered: false }, () => EVENT_TYPE_LABELS[r.event_type] ?? r.event_type),
  },
  { title: '详情', key: 'note', ellipsis: { tooltip: true } },
]

onMounted(fetchAlerts)
</script>

<template>
  <div class="page">
    <n-card
      size="small"
      class="mb-4"
    >
      <div class="flex items-center gap-3">
        <h2 class="page-title">
          🔥 高价值客户预警
        </h2>
        <span class="hint">发现 WhatsApp · SaaS 需求信号 · 等级升至 S/A —— 建议立即跟进</span>
      </div>
    </n-card>
    <n-data-table
      remote
      :columns="columns"
      :data="alerts"
      :loading="loading"
      :row-key="(r: LeadAlert) => r.id"
      :pagination="{
        page: page,
        pageSize: pageSize,
        itemCount: total,
        onChange: handlePageChange,
      }"
    />
  </div>
</template>

<style scoped>
.page-title {
  font-size: 16px;
  margin: 0;
}
.hint {
  font-size: 12px;
  color: var(--yz-text-secondary, #888);
}
</style>
