<script setup lang="ts">
import { h, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, type DataTableColumns } from 'naive-ui'
import * as collectApi from '@/api/collect'
import type { CollectTask, CollectorInfo } from '@/api/collect'
import { formatTime } from '@/utils/format'
import { message, confirm } from '@/utils/feedback'

const router = useRouter()
const loading = ref(false)
const tableData = ref<CollectTask[]>([])
const total = ref(0)
const collectors = ref<CollectorInfo[]>([])

const query = reactive({
  page: 1,
  page_size: 20,
  collector: '' as string,
  status: '' as string,
})

const statusOptions = [
  { label: '排队中', value: 'queued' },
  { label: '运行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
  { label: '待执行', value: 'pending' },
]

async function fetchData() {
  loading.value = true
  try {
    const data = await collectApi.listTasks({
      ...query,
      collector: query.collector || undefined,
      status: query.status || undefined,
    })
    tableData.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

// 有活跃任务时轮询刷新
let timer: ReturnType<typeof setInterval> | null = null
function schedulePolling() {
  const active = tableData.value.some((t) => ['queued', 'running'].includes(t.status))
  if (active && !timer) timer = setInterval(fetchData, 3000)
  if (!active && timer) {
    clearInterval(timer)
    timer = null
  }
}

// ---------- 创建任务 ----------
const dialogVisible = ref(false)
const form = reactive({
  collector: 'job_posting',
  name: '',
  keywords: '',
  max_pages: '3',
  country: '',
  cities: '',
  cron_expr: '',
})
/** 当前选中采集器的参数表单字段（除通用 keywords/max_pages 外按 param_schema 过滤） */
const GENERIC_KEYS = ['keywords', 'max_pages']

function currentCollector(): CollectorInfo | undefined {
  return collectors.value.find((c) => c.name === form.collector)
}

async function handleCreate() {
  const info = currentCollector()
  if (!info) return
  const params: Record<string, string> = {}
  for (const p of info.params) {
    const v = (form as Record<string, unknown>)[p.key]
    if (v !== undefined && String(v).trim() !== '') params[p.key] = String(v).trim()
  }
  const task = await collectApi.createTask({
    collector: form.collector,
    name: form.name || undefined,
    params,
    cron_expr: form.cron_expr || undefined,
  })
  message.success(task.cron_expr ? '定时任务已创建' : '任务已创建并开始排队执行')
  dialogVisible.value = false
  fetchData()
}

function openCreate() {
  Object.assign(form, {
    collector: collectors.value[0]?.name || 'job_posting',
    name: '', keywords: '', max_pages: '3', country: '', cities: '', cron_expr: '',
  })
  dialogVisible.value = true
}

// ---------- 操作 ----------
async function handleRun(row: CollectTask) {
  await collectApi.runTask(row.id)
  message.success('已入队')
  fetchData()
}

async function handleCancel(row: CollectTask) {
  await collectApi.cancelTask(row.id)
  message.success('已请求取消')
  fetchData()
}

async function handleDelete(row: CollectTask) {
  if (!(await confirm({ title: '提示', content: `确定删除任务「${row.name}」吗？（执行日志一并删除）`, positiveText: '删除' })))
    return
  await collectApi.deleteTask(row.id)
  message.success('已删除')
  fetchData()
}

const statusTag: Record<string, { type: 'default' | 'info' | 'success' | 'error' | 'warning'; label: string }> = {
  pending: { type: 'default', label: '待执行' },
  queued: { type: 'info', label: '排队中' },
  running: { type: 'warning', label: '运行中' },
  completed: { type: 'success', label: '已完成' },
  failed: { type: 'error', label: '失败' },
  cancelled: { type: 'default', label: '已取消' },
}

const columns: DataTableColumns<CollectTask> = [
  { title: 'ID', key: 'id', width: 64 },
  {
    title: '任务',
    key: 'name',
    minWidth: 180,
    ellipsis: { tooltip: true },
    render: (row) =>
      h('span', null, [
        row.name,
        row.is_implicit ? h(NTag, { size: 'tiny', style: 'margin-left:6px' }, { default: () => '手动' }) : null,
      ]),
  },
  { title: '采集器', key: 'collector', width: 130 },
  {
    title: '定时',
    key: 'cron_expr',
    width: 100,
    render: (row) => row.cron_expr || '—',
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row) => {
      const s = statusTag[row.status] || { type: 'default' as const, label: row.status }
      return h(NTag, { type: s.type, size: 'small' }, { default: () => s.label })
    },
  },
  {
    title: '进度',
    key: 'progress',
    width: 120,
    render: (row) => {
      if (row.status === 'running' && row.progress_total > 0) {
        return h(NTag, { size: 'small', type: 'info' }, { default: () => `${row.progress_done}/${row.progress_total}` })
      }
      return `${row.progress_done}/${row.progress_total}`
    },
  },
  {
    title: '线索(新/合)',
    key: 'leads',
    width: 100,
    render: (row) => `${row.leads_added}/${row.leads_merged}`,
  },
  { title: '最近执行', key: 'last_run_at', width: 160, render: (row) => (row.last_run_at ? formatTime(row.last_run_at) : '—') },
  {
    title: '操作',
    key: 'actions',
    width: 220,
    render(row) {
      const buttons = [
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/task/${row.id}`) },
          { default: () => '详情' }
        ),
      ]
      if (['pending', 'queued', 'running'].includes(row.status)) {
        buttons.push(
          h(NButton, { size: 'small', quaternary: true, type: 'warning', onClick: () => handleCancel(row) }, { default: () => '取消' })
        )
      } else {
        buttons.push(
          h(NButton, { size: 'small', quaternary: true, type: 'primary', onClick: () => handleRun(row) }, { default: () => '执行' })
        )
      }
      buttons.push(
        h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => handleDelete(row) }, { default: () => '删除' })
      )
      return buttons
    },
  },
]

function handlePageChange(p: number) {
  query.page = p
  fetchData()
}

onMounted(async () => {
  collectors.value = await collectApi.listCollectors()
  await fetchData()
  schedulePolling()
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="page">
    <n-card size="small" class="mb-4">
      <div class="flex flex-wrap items-center gap-3">
        <n-select
          v-model:value="query.collector"
          :options="collectors.map((c) => ({ label: c.title, value: c.name }))"
          placeholder="采集器"
          clearable
          style="width: 200px"
        />
        <n-select v-model:value="query.status" :options="statusOptions" placeholder="状态" clearable style="width: 120px" />
        <n-button type="primary" secondary @click="() => { query.page = 1; fetchData() }">查询</n-button>
        <div class="flex-1" />
        <n-button type="primary" @click="openCreate">新建任务</n-button>
      </div>
    </n-card>

    <n-data-table
      :columns="columns"
      :data="tableData"
      :loading="loading"
      :row-key="(row: CollectTask) => row.id"
      :pagination="{
        page: query.page,
        pageSize: query.page_size,
        itemCount: total,
        onChange: handlePageChange,
      }"
    />

    <n-modal v-model:show="dialogVisible" preset="card" title="新建采集任务" style="width: 560px">
      <n-form label-placement="left" label-width="100">
        <n-form-item label="采集器">
          <n-select
            v-model:value="form.collector"
            :options="collectors.map((c) => ({ label: c.title, value: c.name }))"
          />
        </n-form-item>
        <n-form-item label="任务名">
          <n-input v-model:value="form.name" placeholder="留空用采集器名" />
        </n-form-item>
        <template v-if="currentCollector()">
          <n-form-item
            v-for="p in currentCollector()!.params.filter((x) => !GENERIC_KEYS.includes(x.key))"
            :key="p.key"
            :label="p.label"
          >
            <n-input v-model:value="(form as any)[p.key]" :placeholder="p.placeholder || (p.required ? '必填' : '选填')" />
          </n-form-item>
        </template>
        <n-form-item v-if="form.collector === 'job_posting'" label="关键词">
          <n-input v-model:value="form.keywords" placeholder="whatsapp（默认），逗号分隔多个" />
        </n-form-item>
        <n-form-item v-if="form.collector === 'job_posting'" label="翻页数">
          <n-input v-model:value="form.max_pages" placeholder="3" />
        </n-form-item>
        <n-form-item label="定时 cron">
          <n-input v-model:value="form.cron_expr" placeholder="留空=手动执行；如 0 9 * * *" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="dialogVisible = false">取消</n-button>
          <n-button type="primary" @click="handleCreate">创建</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>
