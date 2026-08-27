<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, type DataTableColumns, type FormInst, type FormRules } from 'naive-ui'
import * as collectApi from '@/api/collect'
import type { Lead, GeoOptions } from '@/api/collect'
import { formatTime } from '@/utils/format'
import { message, confirm } from '@/utils/feedback'

const router = useRouter()
const loading = ref(false)
const tableData = ref<Lead[]>([])
const total = ref(0)
const checkedKeys = ref<number[]>([])
const geo = ref<GeoOptions>({ countries: [], cities_by_country: {} })
const industryOptions = ref<{ label: string; value: string }[]>([])

async function fetchIndustryOptions() {
  industryOptions.value = await collectApi.getIndustryOptions()
}

const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  country: '',
  industry: '',
  source: '' as string,
  min_score: null as number | null,
  // naive-ui select 的 boolean 值类型不兼容，用字符串承载
  whatsapp: '' as '' | 'hit' | 'miss',
})

function whatsappFilter(): boolean | undefined {
  return query.whatsapp === '' ? undefined : query.whatsapp === 'hit'
}

const sourceOptions = [
  { label: 'OpenStreetMap', value: 'osm_overpass' },
  { label: 'Google Maps', value: 'google_maps' },
  { label: '招聘监控', value: 'job_posting' },
  { label: '富化检测', value: 'website_enrich' },
  { label: '手工录入', value: 'manual' },
]

/** 手工录入：国家选中的城市建议（可搜索可手输） */
const cityOptions = computed(() =>
  (geo.value.cities_by_country[form.country] || []).map((c) => ({ label: c, value: c }))
)

const stats = ref({ total_leads: 0, whatsapp_leads: 0, high_intent_leads: 0, active_tasks: 0 })

async function fetchData() {
  loading.value = true
  try {
    const data = await collectApi.listLeads({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      country: query.country || undefined,
      industry: query.industry || undefined,
      source: query.source || undefined,
      min_score: query.min_score ?? undefined,
      whatsapp_hit: whatsappFilter(),
    })
    tableData.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function fetchStats() {
  stats.value = await collectApi.getStats()
}

let statsTimer: ReturnType<typeof setInterval> | null = null

// ---------- 手工录入 ----------
const dialogVisible = ref(false)
const formRef = ref<FormInst>()
const formRules: FormRules = { name: [{ required: true, message: '请输入企业名称', trigger: 'blur' }] }
const form = reactive({ name: '', country: '', city: '', industry: '', phone: '', website: '', email: '' })

function openCreate() {
  Object.assign(form, { name: '', country: '', city: '', industry: '', phone: '', website: '', email: '' })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  await collectApi.createLead({
    name: form.name,
    country: form.country || undefined,
    city: form.city || undefined,
    industry: form.industry || undefined,
    phone: form.phone || undefined,
    website: form.website || undefined,
    email: form.email || undefined,
  })
  message.success('已录入（命中去重键会合并到已有线索）')
  dialogVisible.value = false
  fetchData()
  fetchIndustryOptions()
}

async function handleDelete(row: Lead) {
  if (!(await confirm({ title: '提示', content: `确定删除线索「${row.name}」吗？`, positiveText: '删除' })))
    return
  await collectApi.deleteLead(row.id)
  message.success('已删除')
  fetchData()
  fetchIndustryOptions()
}

// ---------- 批量检测 WhatsApp（隐式任务） ----------
async function handleCheckWhatsApp() {
  const ids = checkedKeys.value
  if (!ids.length) {
    message.warning('请先勾选线索')
    return
  }
  const task = await collectApi.checkWhatsApp(ids)
  message.success(`已创建检测任务 #${task.id}，进度在任务详情页查看`)
  router.push(`/collect/task/${task.id}`)
}

// ---------- 表格 ----------
function scoreTagType(score: number): 'success' | 'warning' | 'info' {
  if (score >= 70) return 'success'
  if (score >= 40) return 'warning'
  return 'info'
}

const columns: DataTableColumns<Lead> = [
  { type: 'selection' },
  { title: 'ID', key: 'id', width: 64 },
  {
    title: '企业',
    key: 'name',
    minWidth: 200,
    ellipsis: { tooltip: true },
    render: (row) =>
      h('span', null, [
        row.name,
        row.whatsapp_hit
          ? h(NTag, { size: 'small', type: 'success', style: 'margin-left:6px' }, { default: () => 'WA' })
          : null,
        row.whatsapp_job
          ? h(NTag, { size: 'small', type: 'warning', style: 'margin-left:4px' }, { default: () => '在招' })
          : null,
      ]),
  },
  { title: '国家/城市', key: 'country', width: 120, render: (row) => [row.country, row.city].filter(Boolean).join(' · ') },
  { title: '行业', key: 'industry', width: 110, ellipsis: { tooltip: true } },
  {
    title: '评分',
    key: 'score',
    width: 90,
    sorter: 'default',
    render: (row) => h(NTag, { type: scoreTagType(row.score), size: 'small' }, { default: () => String(row.score) }),
  },
  {
    title: '联系方式',
    key: 'contact',
    width: 180,
    ellipsis: { tooltip: true },
    render: (row) =>
      [row.phone_e164 || row.phone_raw, row.email].filter(Boolean).join(' / ') || '—',
  },
  {
    title: '官网',
    key: 'website',
    width: 90,
    render: (row) =>
      row.website
        ? h('a', { href: row.website, target: '_blank', rel: 'noopener' }, row.domain || '链接')
        : '—',
  },
  {
    title: '来源',
    key: 'sources',
    width: 130,
    ellipsis: { tooltip: true },
    render: (row) => (row.sources || []).map((s) => s.source).join(','),
  },
  { title: '采集时间', key: 'created_at', width: 160, render: (row) => formatTime(row.created_at) },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    render(row) {
      return h(
        NButton,
        { size: 'small', quaternary: true, type: 'error', onClick: () => handleDelete(row) },
        { default: () => '删除' }
      )
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

function resetQuery() {
  Object.assign(query, { page: 1, keyword: '', country: '', industry: '', source: '', min_score: null, whatsapp: '' })
  fetchData()
}

onMounted(async () => {
  fetchData()
  fetchStats()
  statsTimer = setInterval(fetchStats, 15000)
  geo.value = await collectApi.getGeoOptions()
  fetchIndustryOptions()
})
onUnmounted(() => {
  if (statsTimer) clearInterval(statsTimer)
})
</script>

<template>
  <div class="page">
    <!-- 统计条 -->
    <div class="stat-bar">
      <n-card size="small">
        <div class="stat-row">
          <span>线索总数 <b>{{ stats.total_leads }}</b></span>
          <n-divider vertical />
          <span>检测到 WhatsApp <b class="stat-wa">{{ stats.whatsapp_leads }}</b></span>
          <n-divider vertical />
          <span>高意向（≥40 分） <b>{{ stats.high_intent_leads }}</b></span>
          <n-divider vertical />
          <span>进行中任务 <b>{{ stats.active_tasks }}</b></span>
        </div>
      </n-card>
    </div>

    <!-- 筛选 -->
    <n-card size="small" class="mb-4">
      <div class="flex flex-wrap items-center gap-3">
        <n-input v-model:value="query.keyword" placeholder="名称/邮箱/域名/电话/城市" clearable style="width: 220px" @keyup.enter="() => { query.page = 1; fetchData() }" />
        <n-select
          v-model:value="query.country"
          :options="(geo.countries as any)"
          placeholder="国家"
          clearable
          filterable
          tag
          style="width: 170px"
        />
        <n-select
          v-model:value="query.industry"
          :options="(industryOptions as any)"
          placeholder="行业"
          clearable
          filterable
          style="width: 150px"
        />
        <n-select v-model:value="query.source" :options="sourceOptions" placeholder="来源" clearable style="width: 130px" />
        <n-input-number v-model:value="query.min_score" placeholder="最低分" clearable style="width: 110px" :min="0" />
        <n-select
          v-model:value="query.whatsapp"
          :options="[
            { label: '已检测到', value: 'hit' },
            { label: '未检测到', value: 'miss' },
          ]"
          placeholder="WhatsApp 检测"
          clearable
          style="width: 130px"
        />
        <n-button type="primary" secondary @click="() => { query.page = 1; fetchData() }">查询</n-button>
        <n-button quaternary @click="resetQuery">重置</n-button>
        <div class="flex-1" />
        <n-button :disabled="!checkedKeys.length" type="warning" secondary @click="handleCheckWhatsApp">
          检测 WhatsApp（{{ checkedKeys.length }}）
        </n-button>
        <n-button type="primary" @click="openCreate">手工录入</n-button>
      </div>
    </n-card>

    <n-data-table
      v-model:checked-row-keys="checkedKeys"
      remote
      :columns="columns"
      :data="tableData"
      :loading="loading"
      :row-key="(row: Lead) => row.id"
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

    <n-modal
      v-model:show="dialogVisible"
      preset="card"
      title="手工录入线索"
      style="width: 520px"
    >
      <n-form ref="formRef" :model="form" :rules="formRules" label-placement="left" label-width="80">
        <n-form-item label="企业名称" path="name">
          <n-input v-model:value="form.name" placeholder="必填" />
        </n-form-item>
        <n-form-item label="国家">
          <n-select
            v-model:value="form.country"
            :options="(geo.countries as any)"
            placeholder="选择或输入 2 位国家码"
            clearable
            filterable
            tag
          />
        </n-form-item>
        <n-form-item label="城市">
          <n-select
            v-model:value="form.city"
            :options="(cityOptions as any)"
            :placeholder="form.country ? '选择建议城市或输入自定义' : '先选国家出建议，也可直接输入'"
            clearable
            filterable
            tag
          />
        </n-form-item>
        <n-form-item label="行业">
          <n-input v-model:value="form.industry" />
        </n-form-item>
        <n-form-item label="电话">
          <n-input v-model:value="form.phone" placeholder="带国家码更准，如 +60123456789" />
        </n-form-item>
        <n-form-item label="官网">
          <n-input v-model:value="form.website" placeholder="https://..." />
        </n-form-item>
        <n-form-item label="邮箱">
          <n-input v-model:value="form.email" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="dialogVisible = false">取消</n-button>
          <n-button type="primary" @click="handleSubmit">提交</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.stat-bar {
  margin-bottom: 12px;
}
.stat-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--yz-text-secondary, #666);
}
.stat-wa {
  color: #18a058;
}
</style>
