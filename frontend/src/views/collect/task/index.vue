<script setup lang="ts">
import { h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NIcon, NProgress, NTag, NTooltip, type DataTableColumns } from 'naive-ui'
import { HelpCircleOutline } from '@vicons/ionicons5'
import * as collectApi from '@/api/collect'
import type { CollectTask, CollectorInfo, CollectorParam, GeoOptions } from '@/api/collect'
import { useUserStore } from '@/stores/user'
import { formatTime } from '@/utils/format'
import { message, confirm } from '@/utils/feedback'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const tableData = ref<CollectTask[]>([])
const total = ref(0)
const collectors = ref<CollectorInfo[]>([])
const geo = ref<GeoOptions>({ countries: [], cities_by_country: {} })

/** cities 参数的城市建议：按当前表单里选的国家取（depends_on 指向的字段值） */
function cityOptionsFor(p: CollectorParam) {
  const country = p.depends_on ? String(paramForm.value[p.depends_on] ?? '') : ''
  return (geo.value.cities_by_country[country] || []).map((c) => ({ label: c, value: c }))
}

const query = reactive({
  page: 1,
  page_size: 20,
  // n-select 初值必须 null：naive-ui 只在 null/undefined 时显示 placeholder
  collector: null as string | null,
  status: null as string | null,
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
  cron_expr: '',
})
/** 参数表单值：控件类型决定值的形态（tags/multiselect→数组、switch→布尔、number→数字） */
const paramForm = ref<Record<string, unknown>>({})

function currentCollector(): CollectorInfo | undefined {
  return collectors.value.find((c) => c.name === form.collector)
}

/** 按 param_schema 初始化参数值（default 按 type 反序列化） */
function initParamForm() {
  const info = currentCollector()
  const model: Record<string, unknown> = {}
  if (info) {
    for (const p of info.params) {
      const d = (p.default ?? '').trim()
      if (p.type === 'tags' || p.type === 'multiselect' || p.type === 'cities') {
        model[p.key] = d ? d.split(',').map((s) => s.trim()).filter(Boolean) : []
      } else if (p.type === 'switch') {
        model[p.key] = d !== 'false'
      } else if (p.type === 'number') {
        model[p.key] = d ? Number(d) : null
      } else {
        model[p.key] = d
      }
    }
  }
  paramForm.value = model
}

/** 表单值 → 提交给后端的字符串（数组 join 逗号、布尔转 true/false、数字转字符串） */
function serializeParam(v: unknown): string | undefined {
  if (Array.isArray(v)) return v.length ? v.join(',') : undefined
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'number') return String(v)
  const s = String(v ?? '').trim()
  return s ? s : undefined
}

function requiredMissing(info: CollectorInfo): CollectorParam | undefined {
  return info.params.find((p) => p.required && !serializeParam(paramForm.value[p.key]))
}

async function handleCreate() {
  const info = currentCollector()
  if (!info) return
  const missing = requiredMissing(info)
  if (missing) {
    message.warning(`请填写「${missing.label}」`)
    return
  }
  const params: Record<string, string> = {}
  for (const p of info.params) {
    const v = serializeParam(paramForm.value[p.key])
    if (v !== undefined) params[p.key] = v
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
  Object.assign(form, { collector: collectors.value[0]?.name || 'job_posting', name: '', cron_expr: '' })
  initParamForm()
  dialogVisible.value = true
}

watch(() => form.collector, initParamForm)

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

/** 表头带悬浮说明（❓图标 hover 出解释） */
function headerWithTip(title: string, tip: string) {
  return () =>
    h('span', null, [
      title,
      h(
        NTooltip,
        {},
        {
          trigger: () =>
            h(NIcon, { size: 13, style: 'margin-left:3px;color:#999;cursor:help;vertical-align:-2px' }, { default: () => h(HelpCircleOutline) }),
          default: () => tip,
        }
      ),
    ])
}

/** 线索数字：>0 按语义着色，0 弱化 */
function leadNum(n: number, color: string) {
  return h('span', { style: n > 0 ? `color:${color};font-weight:600` : 'color:#c2c5cc' }, String(n))
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
        h('span', { style: 'font-weight:500' }, row.name),
        row.is_implicit ? h(NTag, { size: 'tiny', style: 'margin-left:6px' }, { default: () => '手动' }) : null,
      ]),
  },
  {
    title: '采集器',
    key: 'collector',
    width: 170,
    render: (row) => {
      const title = collectors.value.find((c) => c.name === row.collector)?.title
      return h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => title ?? row.collector })
    },
  },
  {
    title: '操作人',
    key: 'created_by_name',
    width: 100,
    render: (row) =>
      row.created_by_name
        ? h('span', null, row.created_by_name)
        : h('span', { style: 'color:#c2c5cc' }, '—'),
  },
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
    width: 150,
    render: (row) => {
      const { progress_done: done, progress_total: total } = row
      if (!total) return h('span', { style: 'color:#c2c5cc' }, '—')
      const pct = Math.min(100, Math.round((done / total) * 100))
      const progressStatusMap: Record<string, 'info' | 'success' | 'error' | 'warning' | 'default'> = {
        running: 'info',
        completed: 'success',
        failed: 'error',
        cancelled: 'warning',
      }
      const status = progressStatusMap[row.status] || 'default'
      return h('div', { style: 'padding-right:12px' }, [
        h(NProgress, { type: 'line', percentage: pct, height: 6, showIndicator: false, status }),
        h('span', { style: 'font-size:12px;color:var(--yz-text-secondary,#666)' }, `${done}/${total}`),
      ])
    },
  },
  {
    title: headerWithTip('新增线索', '本次采到、库里之前没有的商家 → 新建 1 条线索'),
    key: 'leads_added',
    width: 100,
    render: (row) => leadNum(row.leads_added, '#18a058'),
  },
  {
    title: headerWithTip('合并线索', '采到但库里已有同一家（域名/电话/名称+城市 命中）→ 只补全字段，不新建。新增+合并 = 本次处理的商家总数'),
    key: 'leads_merged',
    width: 100,
    render: (row) => leadNum(row.leads_merged, '#f0a020'),
  },
  {
    title: '最近执行',
    key: 'last_run_at',
    width: 160,
    render: (row) =>
      h(
        'span',
        { style: `font-size:12px;color:var(--yz-text-secondary,#666)` },
        row.last_run_at ? formatTime(row.last_run_at) : '—'
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 220,
    fixed: 'right', // 列多时固定右侧，横向滚动也不丢操作按钮
    render(row) {
      const buttons = [
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/task/${row.id}`) },
          { default: () => '详情' }
        ),
      ]
      // 建任务/执行/取消/删除是管理员操作，销售只读（看进度）
      if (userStore.isSuperuser) {
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
      }
      return buttons
    },
  },
]

function handlePageChange(p: number) {
  query.page = p
  fetchData()
}

function handlePageSizeChange(s: number) {
  query.page_size = s
  query.page = 1
  fetchData()
}

onMounted(async () => {
  collectors.value = await collectApi.listCollectors()
  geo.value = await collectApi.getGeoOptions()
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
        <n-button v-if="userStore.isSuperuser" type="primary" @click="openCreate">新建任务</n-button>
      </div>
    </n-card>

    <n-data-table
      remote
      :scroll-x="1450"
      :columns="columns"
      :data="tableData"
      :loading="loading"
      :row-key="(row: CollectTask) => row.id"
      :pagination="{
        page: query.page,
        pageSize: query.page_size,
        itemCount: total,
        showSizePicker: true,
        pageSizes: [10, 20, 50, 100],
        onChange: handlePageChange,
        onPageSizeChange: handlePageSizeChange,
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
          <n-form-item v-for="p in currentCollector()!.params" :key="p.key" :label="p.label">
            <!-- 国家：可搜索下拉，也允许手输列表外的 ISO2 -->
            <n-select
              v-if="p.type === 'select'"
              v-model:value="(paramForm as any)[p.key]"
              :options="(p.options as any)"
              :placeholder="p.placeholder || '请选择'"
              filterable
              tag
            />
            <!-- 城市：与国家联动，选国家后出建议；可搜索可手输任意城市 -->
            <n-select
              v-else-if="p.type === 'cities'"
              v-model:value="(paramForm as any)[p.key]"
              :options="cityOptionsFor(p)"
              multiple
              filterable
              tag
              :max-tag-count="'responsive'"
              :placeholder="p.placeholder || '先选国家'"
            />
            <!-- 关键词：标签输入，回车追加 -->
            <n-dynamic-tags
              v-else-if="p.type === 'tags'"
              v-model:value="(paramForm as any)[p.key]"
              :placeholder="p.placeholder || '输入后回车添加'"
            />
            <!-- 行业等多选：预设选项多选框 -->
            <n-select
              v-else-if="p.type === 'multiselect'"
              v-model:value="(paramForm as any)[p.key]"
              :options="(p.options as any)"
              multiple
              :max-tag-count="'responsive'"
              :placeholder="p.placeholder || '请选择（可多选）'"
            />
            <!-- 布尔开关 -->
            <div v-else-if="p.type === 'switch'" class="flex items-center gap-2">
              <n-switch v-model:value="(paramForm as any)[p.key]" />
              <span class="text-xs text-gray-400">{{ p.placeholder }}</span>
            </div>
            <!-- 数字 -->
            <n-input-number
              v-else-if="p.type === 'number'"
              v-model:value="(paramForm as any)[p.key]"
              :precision="0"
              :min="1"
              :placeholder="p.placeholder"
              class="w-full"
            />
            <!-- 默认文本 -->
            <n-input v-else v-model:value="(paramForm as any)[p.key]" :placeholder="p.placeholder || (p.required ? '必填' : '选填')" />
          </n-form-item>
        </template>
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
