<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, type DataTableColumns, type FormInst, type FormRules } from 'naive-ui'
import * as collectApi from '@/api/collect'
import { FOLLOW_STATUS_OPTIONS, EXPORT_FIELDS, followStatusLabel, followStatusTagType, gradeTagType } from '@/api/collect'
import { ICP_STATUS_LABELS } from '@/api/collect'
import type {
  FollowOptions,
  FollowStatus,
  FollowUpRecord,
  GeoOptions,
  Grade,
  IndustryOption,
  IcpStatus,
  Lead,
} from '@/api/collect'
import { useUserStore } from '@/stores/user'
import { formatTime, parseUtc } from '@/utils/format'
import { message, confirm } from '@/utils/feedback'
import { downloadFile } from '@/utils/download'
import { autoAssignLeads } from '@/api/sales'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const tableData = ref<Lead[]>([])
const total = ref(0)
const checkedKeys = ref<number[]>([])
const geo = ref<GeoOptions>({ countries: [], cities_by_country: {} })
const industryOptions = ref<IndustryOption[]>([])
/** 筛选下拉选项：中文名（数量）；表格行业列也用这份数据显示中文 */
const industrySelectOptions = computed(() =>
  industryOptions.value.map((i) => ({ label: `${i.label}（${i.count}）`, value: i.value }))
)

function industryLabel(value: string | null): string {
  if (!value) return '—'
  return industryOptions.value.find((i) => i.value === value)?.label ?? value
}

/** 国家码 → 「🇲🇾 马来西亚」（复用 geo-options 数据源；未知码原样显示） */
function countryLabel(code: string | null): string {
  if (!code) return ''
  const hit = geo.value.countries.find((c) => c.value === code.toUpperCase())
  return hit ? hit.label.replace(/\s*\([^)]*\)\s*$/, '') : code
}

async function fetchIndustryOptions() {
  industryOptions.value = await collectApi.getIndustryOptions()
}

const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  // n-select 初值必须 null：naive-ui 只在 null/undefined 时显示 placeholder，
  // 空串 '' 会被当成已选值（空白显示）
  country: null as string | null,
  industry: null as string | null,
  source: null as string | null,
  min_score: null as number | null,
  grade: null as Grade | null,
  // naive-ui select 的 boolean 值类型不兼容，用字符串承载
  whatsapp: null as 'hit' | 'miss' | null,
  follow_status: null as FollowStatus | null,
  owner_id: null as number | null,
  due_follow: false,
  is_cn: false,
  /** ICP 二重门筛选：null=默认（排除非中国企业） */
  icp: null as IcpStatus | 'all' | null,
})

/** 跟进弹窗选项（状态词表 + 跟进人下拉），onMounted 拉一次 */
const followOptions = ref<FollowOptions>({ statuses: FOLLOW_STATUS_OPTIONS, users: [] })

function whatsappFilter(): boolean | undefined {
  return !query.whatsapp ? undefined : query.whatsapp === 'hit'
}

/** ICP 二重门筛选项（null=默认排除非中国企业，见后端 icp 参数） */
const icpFilterOptions = [
  { label: '全部（不过滤）', value: 'all' },
  ...(['qualified', 'cn_domestic', 'foreign', 'unknown'] as const).map((v) => ({
    label: ICP_STATUS_LABELS[v] ?? v,
    value: v,
  })),
]

const sourceOptions = [
  { label: 'Meta 广告库', value: 'meta_ads' },
  { label: '开源地图', value: 'osm_overpass' },
  { label: '谷歌地图', value: 'google_maps' },
  { label: '招聘监控', value: 'job_posting' },
  { label: '网站富化', value: 'website_enrich' },
  { label: '手工录入', value: 'manual' },
]

/** 来源 token → 中文名（表格列与筛选用同一词表，未收录原样显示） */
function sourceLabel(token: string): string {
  return sourceOptions.find((s) => s.value === token)?.label ?? token
}

/** 来源标签配色：不同来源一眼可辨 */
const SOURCE_TAG_TYPES: Record<string, 'success' | 'warning' | 'info' | 'default' | 'error'> = {
  meta_ads: 'error',
  osm_overpass: 'success',
  google_maps: 'warning',
  job_posting: 'info',
  website_enrich: 'default',
  manual: 'default',
}

const MUTED = 'var(--yz-text-secondary,#666)'
const PLACEHOLDER_GRAY = '#c2c5cc'

/** 手工录入：国家选中的城市建议（可搜索可手输） */
const cityOptions = computed(() =>
  (geo.value.cities_by_country[form.country] || []).map((c) => ({ label: c, value: c }))
)

const stats = ref({
  total_leads: 0,
  whatsapp_leads: 0,
  high_intent_leads: 0,
  grade_counts: { S: 0, A: 0, B: 0, C: 0 } as Record<Grade, number>,
  active_tasks: 0,
  pending_leads: 0,
  due_follow_leads: 0,
  cn_leads: 0,
  fb_wa_leads: 0,
})

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
      grade: query.grade || undefined,
      whatsapp_hit: whatsappFilter(),
      follow_status: query.follow_status || undefined,
      owner_id: query.owner_id ?? undefined,
      due_follow: query.due_follow || undefined,
      is_cn: query.is_cn || undefined,
      icp: query.icp || undefined,
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

// ---------- 领取线索（共享池一键认领，撞单由后端 40001 兜底） ----------
async function handleClaim(row: Lead) {
  const lead = await collectApi.claimLead(row.id)
  message.success(`已领取「${lead.name}」，请尽快跟进`)
  fetchData()
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

// ---------- CSV 导出 ----------
const exportVisible = ref(false)
const exportSubmitting = ref(false)
/** 字段选择（默认全选），提交时逗号拼接传 fields */
const exportFields = ref<string[]>(EXPORT_FIELDS.map((f) => f.key))

function openExport() {
  exportFields.value = EXPORT_FIELDS.map((f) => f.key)
  exportVisible.value = true
}

async function handleExport() {
  if (!exportFields.value.length) {
    message.warning('请至少选择一个导出字段')
    return
  }
  exportSubmitting.value = true
  try {
    const d = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`
    await downloadFile(
      '/collect/leads/export',
      {
        keyword: query.keyword || undefined,
        country: query.country || undefined,
        industry: query.industry || undefined,
        source: query.source || undefined,
        min_score: query.min_score ?? undefined,
        grade: query.grade || undefined,
        whatsapp_hit: whatsappFilter(),
        follow_status: query.follow_status || undefined,
        owner_id: query.owner_id ?? undefined,
        due_follow: query.due_follow || undefined,
        is_cn: query.is_cn || undefined,
        fields: exportFields.value.join(','),
      },
      `leads_${stamp}.csv`,
    )
    message.success('导出完成')
    exportVisible.value = false
  } finally {
    exportSubmitting.value = false
  }
}

// ---------- 种子导入（Seed Pool，PRD §三 模块①） ----------
const importVisible = ref(false)
const importSubmitting = ref(false)
const importCsvText = ref('')
const importIsCn = ref(true)
const importFileRef = ref<HTMLInputElement | null>(null)
/** 与后端 LeadImportPayload.csv_text 上限一致（2MB），超限提前拦截不分发 */
const IMPORT_MAX_BYTES = 2 * 1024 * 1024

function openImport() {
  importCsvText.value = ''
  importIsCn.value = true
  importVisible.value = true
}

function triggerImportFile() {
  importFileRef.value?.click()
}

async function handleImportFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 清空让同一文件可重复选择
  if (!file) return
  if (file.size > IMPORT_MAX_BYTES) {
    message.warning('文件超过 2MB 上限，请拆分后分批导入')
    return
  }
  importCsvText.value = await file.text()
}

async function handleImportSubmit() {
  const text = importCsvText.value.trim()
  if (!text) {
    message.warning('请粘贴 CSV 文本或选择 CSV 文件')
    return
  }
  importSubmitting.value = true
  try {
    const result = await collectApi.importLeads(text, importIsCn.value)
    message.success(`导入完成：新建 ${result.created}，合并 ${result.merged}，跳过 ${result.skipped}`)
    if (result.errors.length) message.warning(result.errors.slice(0, 3).join('；'))
    importVisible.value = false
    fetchData()
    fetchStats()
    fetchIndustryOptions()
  } finally {
    importSubmitting.value = false
  }
}

// ---------- 跟进 ----------
/** 该回访了：约定了下次跟进时间且已到期（后端串无 Z 后缀，须按 UTC 解析） */
function isDue(row: Lead): boolean {
  return !!row.next_follow_at && parseUtc(row.next_follow_at).getTime() <= Date.now()
}

const followVisible = ref(false)
const followSubmitting = ref(false)
const followHistory = ref<FollowUpRecord[]>([])
const followTarget = ref<Lead | null>(null)
const followForm = reactive({
  status: 'contacted' as FollowStatus,
  owner_id: null as number | null,
  note: '',
  /** n-date-picker 的值是时间戳（毫秒），提交时转 ISO */
  next_follow: null as number | null,
})

async function openFollowUp(row: Lead) {
  followTarget.value = row
  followForm.status = row.follow_status ?? 'contacted'
  followForm.owner_id = row.owner_id ?? userStore.userInfo?.id ?? null
  followForm.note = ''
  followForm.next_follow = null
  followVisible.value = true
  // 历史时间线独立加载，不阻塞弹窗打开
  followHistory.value = []
  try {
    followHistory.value = await collectApi.getFollowUps(row.id)
  } catch {
    /* 历史加载失败不挡跟进 */
  }
}

/** 时间线节点配色与状态词表一致 */
function timelineType(s: string): 'default' | 'info' | 'success' | 'error' | 'warning' {
  return followStatusTagType(s)
}

async function handleFollowSubmit() {
  if (!followTarget.value) return
  followSubmitting.value = true
  try {
    await collectApi.followUpLead(followTarget.value.id, {
      status: followForm.status,
      owner_id: followForm.owner_id ?? undefined,
      note: followForm.note.trim() || undefined,
      next_follow_at: followForm.next_follow ? new Date(followForm.next_follow).toISOString() : undefined,
    })
    message.success('跟进已记录')
    followVisible.value = false
    fetchData()
  } finally {
    followSubmitting.value = false
  }
}

// ---------- 自动分配（PRD §24，主管操作） ----------
const autoAssignShow = ref(false)
const autoAssignSubmitting = ref(false)
const autoAssignForm = reactive({
  owner_ids: [] as number[],
  max_per_owner: 50,
  grade: null as Grade | null,
  min_score: null as number | null,
  limit: 100,
})

function openAutoAssign() {
  autoAssignForm.owner_ids = []
  autoAssignForm.grade = null
  autoAssignForm.min_score = null
  autoAssignShow.value = true
}

async function handleAutoAssign() {
  if (!autoAssignForm.owner_ids.length) {
    message.warning('请选择候选销售')
    return
  }
  autoAssignSubmitting.value = true
  try {
    const result = await autoAssignLeads({
      owner_ids: autoAssignForm.owner_ids,
      max_per_owner: autoAssignForm.max_per_owner,
      grade: autoAssignForm.grade || undefined,
      min_score: autoAssignForm.min_score ?? undefined,
      limit: autoAssignForm.limit,
    })
    const detail = result.per_owner.filter((x) => x.count > 0).map((x) => `${x.owner_name ?? x.owner_id}:${x.count}`).join('、')
    message.success(`已分配 ${result.assigned_count} 条线索（${detail || '共享池已空'}）`)
    autoAssignShow.value = false
    fetchData()
    fetchStats()
  } finally {
    autoAssignSubmitting.value = false
  }
}

// ---------- 表格 ----------

const columns: DataTableColumns<Lead> = [
  { type: 'selection' },
  {
    title: '企业',
    key: 'name',
    minWidth: 200,
    ellipsis: { tooltip: true },
    render: (row) =>
      h('span', null, [
        h('span', { style: 'font-weight:500' }, row.name),
        row.fb_whatsapp
          ? h(NTag, { size: 'small', type: 'error', style: 'margin-left:6px' }, { default: () => 'FB私域' })
          : null,
        row.whatsapp_hit
          ? h(NTag, { size: 'small', type: 'success', style: 'margin-left:6px' }, { default: () => 'WA' })
          : null,
        row.whatsapp_job
          ? h(NTag, { size: 'small', type: 'warning', style: 'margin-left:4px' }, { default: () => '在招' })
          : null,
        // ICP 二重门资格标：qualified=出海（默认口径）/ cn_domestic=国内待培育 /
        // foreign=非中国企业（默认列表已排除，显式筛才见）
        row.icp_status === 'qualified'
          ? h(NTag, { size: 'small', bordered: false, style: 'margin-left:4px' }, { default: () => '出海' })
          : null,
        row.icp_status === 'cn_domestic'
          ? h(NTag, { size: 'small', bordered: false, type: 'default', style: 'margin-left:4px' }, { default: () => '国内' })
          : null,
        row.icp_status === 'foreign'
          ? h(NTag, { size: 'small', bordered: false, type: 'error', style: 'margin-left:4px' }, { default: () => '非中国企业' })
          : null,
      ]),
  },
  {
    title: '国家/城市',
    key: 'country',
    width: 150,
    render: (row) =>
      h('div', { style: 'line-height:1.45' }, [
        countryLabel(row.country)
          ? h('div', null, countryLabel(row.country))
          : h('div', { style: `color:${PLACEHOLDER_GRAY}` }, '—'),
        row.city ? h('div', { style: `font-size:12px;color:${MUTED}` }, row.city) : null,
      ]),
  },
  {
    title: '行业',
    key: 'industry',
    width: 110,
    render: (row) =>
      row.industry
        ? h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => industryLabel(row.industry) })
        : h('span', { style: `color:${PLACEHOLDER_GRAY}` }, '—'),
  },
  {
    title: '等级',
    key: 'grade',
    width: 96,
    sorter: 'default',
    render: (row) =>
      h(
        NTag,
        { type: gradeTagType(row.grade), size: 'small' },
        { default: () => `${row.grade} · ${row.score}` }
      ),
  },
  {
    title: '联系人',
    key: 'contacts_count',
    width: 110,
    render: (row) => {
      if (!row.contacts_count)
        return h('span', { style: `color:${PLACEHOLDER_GRAY}` }, '待补')
      // 有决策层联系人（CEO/总经理）= 「找谁」的第一答案，直接标出来
      return h('span', { style: 'display:flex;align-items:center;gap:4px' }, [
        h('span', { style: 'font-weight:500' }, `${row.contacts_count} 人`),
        row.has_tier1 ? h(NTag, { size: 'tiny', type: 'success', bordered: false }, () => '决策层') : null,
      ])
    },
  },
  {
    title: '推荐产品',
    key: 'recommended_products',
    width: 150,
    render: (row) => {
      if (!row.recommended_products.length)
        return h('span', { style: `color:${PLACEHOLDER_GRAY}` }, '—')
      return h(
        'div',
        { style: 'display:flex;flex-direction:column;gap:2px;align-items:flex-start' },
        row.recommended_products.slice(0, 2).map((p) =>
          h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => p })
        )
      )
    },
  },
  {
    title: '联系方式',
    key: 'contact',
    width: 180,
    render: (row) => {
      const phone = row.phone_e164 || row.phone_raw
      if (!phone && !row.email) return h('span', { style: `color:${PLACEHOLDER_GRAY}` }, '—')
      return h('div', { style: 'line-height:1.45;min-width:0' }, [
        phone
          ? h(
              'div',
              { style: 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis', title: phone },
              phone
            )
          : null,
        row.email
          ? h(
              'div',
              {
                style: `font-size:12px;color:${MUTED};white-space:nowrap;overflow:hidden;text-overflow:ellipsis`,
                title: row.email,
              },
              row.email
            )
          : null,
      ])
    },
  },
  {
    title: '官网',
    key: 'website',
    width: 90,
    render: (row) =>
      row.website
        ? h('a', { href: row.website, target: '_blank', rel: 'noopener' }, row.domain || '链接')
        : h('span', { style: `color:${PLACEHOLDER_GRAY}` }, '—'),
  },
  {
    title: '来源',
    key: 'sources',
    width: 160,
    render: (row) => {
      const srcs = row.sources || []
      if (!srcs.length) return h('span', { style: `color:${PLACEHOLDER_GRAY}` }, '—')
      return h(
        'div',
        { style: 'display:flex;flex-wrap:wrap;gap:4px' },
        srcs.map((s) =>
          h(
            NTag,
            { size: 'small', bordered: false, type: SOURCE_TAG_TYPES[s.source] || 'default' },
            { default: () => sourceLabel(s.source) }
          )
        )
      )
    },
  },
  {
    title: '跟进人',
    key: 'owner_name',
    width: 100,
    render: (row) =>
      row.owner_name
        ? h('span', { style: 'font-weight:500' }, row.owner_name)
        : h('span', { style: `color:${PLACEHOLDER_GRAY}` }, '—'),
  },
  {
    title: '跟进状态',
    key: 'follow_status',
    width: 120,
    render: (row) =>
      h('div', { style: 'display:flex;align-items:center;gap:4px' }, [
        h(
          NTag,
          {
            size: 'small',
            bordered: false,
            type: followStatusTagType(row.follow_status),
          },
          { default: () => followStatusLabel(row.follow_status) }
        ),
        isDue(row)
          ? h('span', { style: 'font-size:12px;color:#d03050;font-weight:600' }, '该回访')
          : null,
      ]),
  },
  {
    title: '采集时间',
    key: 'created_at',
    width: 160,
    render: (row) =>
      h('span', { style: `font-size:12px;color:${MUTED}` }, formatTime(row.created_at)),
  },
  {
    title: '操作',
    key: 'actions',
    width: 180,
    fixed: 'right', // 列多时固定右侧，横向滚动也不丢操作按钮
    render(row) {
      const buttons = [
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/lead/${row.id}`) },
          { default: () => '详情' }
        ),
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'primary', onClick: () => openFollowUp(row) },
          { default: () => '跟进' }
        ),
      ]
      // 共享池商机一键领取（§七「领取线索」）；已有人跟进则不显示（撞单保护在后端）
      if (!row.owner_id) {
        buttons.push(
          h(
            NButton,
            { size: 'small', quaternary: true, type: 'success', onClick: () => handleClaim(row) },
            { default: () => '领取' }
          )
        )
      }
      // 删线索是管理员操作，销售不可见
      if (userStore.isSuperuser) {
        buttons.push(
          h(
            NButton,
            { size: 'small', quaternary: true, type: 'error', onClick: () => handleDelete(row) },
            { default: () => '删除' }
          )
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

function resetQuery() {
  Object.assign(query, {
    page: 1,
    keyword: '',
    country: null,
    industry: null,
    source: null,
    min_score: null,
    grade: null,
    whatsapp: null,
    follow_status: null,
    owner_id: null,
    due_follow: false,
    is_cn: false,
    icp: null,
  })
  fetchData()
}

onMounted(async () => {
  fetchData()
  fetchStats()
  statsTimer = setInterval(fetchStats, 15000)
  geo.value = await collectApi.getGeoOptions()
  fetchIndustryOptions()
  collectApi
    .getFollowOptions()
    .then((o) => (followOptions.value = o))
    .catch(() => {}) // 拉不到就先只按状态筛，不阻塞页面
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
          <span>中国出海 <b class="stat-cn">{{ stats.cn_leads }}</b></span>
          <n-divider vertical />
          <span>FB 私域按钮 <b class="stat-wa">{{ stats.fb_wa_leads }}</b></span>
          <n-divider vertical />
          <span>检测到 WhatsApp <b class="stat-wa">{{ stats.whatsapp_leads }}</b></span>
          <n-divider vertical />
          <span>S 级 <b class="stat-due">{{ stats.grade_counts?.S ?? 0 }}</b></span>
          <n-divider vertical />
          <span>A 级 <b class="stat-pending">{{ stats.grade_counts?.A ?? 0 }}</b></span>
          <n-divider vertical />
          <span>待跟进 <b class="stat-pending">{{ stats.pending_leads }}</b></span>
          <n-divider vertical />
          <span>该回访 <b class="stat-due">{{ stats.due_follow_leads }}</b></span>
          <n-divider vertical />
          <span>进行中任务 <b>{{ stats.active_tasks }}</b></span>
        </div>
      </n-card>
    </div>

    <!-- 筛选 -->
    <n-card
      size="small"
      class="mb-4"
    >
      <div class="flex flex-wrap items-center gap-3">
        <n-input
          v-model:value="query.keyword"
          placeholder="关键词：名称/邮箱/域名/电话/城市"
          clearable
          style="width: 230px"
          @keyup.enter="() => { query.page = 1; fetchData() }"
        />
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
          :options="(industrySelectOptions as any)"
          placeholder="行业"
          clearable
          filterable
          style="width: 160px"
        />
        <n-select
          v-model:value="query.source"
          :options="sourceOptions"
          placeholder="来源"
          clearable
          style="width: 125px"
        />
        <n-select
          v-model:value="query.grade"
          :options="collectApi.GRADE_OPTIONS"
          placeholder="等级"
          clearable
          style="width: 125px"
        />
        <n-input-number
          v-model:value="query.min_score"
          placeholder="最低分"
          clearable
          style="width: 105px"
          :min="0"
        />
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
        <n-select
          v-model:value="query.follow_status"
          :options="followOptions.statuses"
          placeholder="跟进状态"
          clearable
          style="width: 120px"
        />
        <n-select
          v-model:value="query.owner_id"
          :options="followOptions.users"
          placeholder="跟进人"
          clearable
          filterable
          style="width: 120px"
        />
        <n-checkbox
          v-model:checked="query.due_follow"
          size="small"
        >
          该回访了
        </n-checkbox>
        <n-checkbox
          v-model:checked="query.is_cn"
          size="small"
        >
          中国出海
        </n-checkbox>
        <n-select
          v-model:value="query.icp"
          :options="icpFilterOptions"
          clearable
          size="small"
          placeholder="ICP：默认排除非中国企业"
          style="width: 190px"
        />
        <n-button
          type="primary"
          secondary
          @click="() => { query.page = 1; fetchData() }"
        >
          查询
        </n-button>
        <n-button
          quaternary
          @click="resetQuery"
        >
          重置
        </n-button>
        <div class="flex-1" />
        <n-button
          :disabled="!checkedKeys.length"
          type="warning"
          secondary
          @click="handleCheckWhatsApp"
        >
          检测 WhatsApp（{{ checkedKeys.length }}）
        </n-button>
        <n-button
          secondary
          @click="openExport"
        >
          导出 CSV
        </n-button>
        <n-button
          secondary
          type="success"
          @click="openImport"
        >
          导入种子
        </n-button>
        <n-button
          v-if="userStore.isSuperuser"
          secondary
          type="info"
          @click="openAutoAssign"
        >
          自动分配
        </n-button>
        <n-button
          type="primary"
          @click="openCreate"
        >
          手工录入
        </n-button>
      </div>
    </n-card>

    <n-data-table
      v-model:checked-row-keys="checkedKeys"
      remote
      :scroll-x="1846"
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
      <n-form
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-placement="left"
        label-width="80"
      >
        <n-form-item
          label="企业名称"
          path="name"
        >
          <n-input
            v-model:value="form.name"
            placeholder="必填"
          />
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
          <n-input
            v-model:value="form.phone"
            placeholder="带国家码更准，如 +60123456789"
          />
        </n-form-item>
        <n-form-item label="官网">
          <n-input
            v-model:value="form.website"
            placeholder="https://..."
          />
        </n-form-item>
        <n-form-item label="邮箱">
          <n-input v-model:value="form.email" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="dialogVisible = false">
            取消
          </n-button>
          <n-button
            type="primary"
            @click="handleSubmit"
          >
            提交
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 跟进弹窗：上半历史时间线，下半本次跟进表单 -->
    <n-modal
      v-model:show="followVisible"
      preset="card"
      :title="followTarget ? `跟进 · ${followTarget.name}` : '跟进线索'"
      style="width: 560px"
    >
      <div class="follow-history">
        <n-timeline v-if="followHistory.length">
          <n-timeline-item
            v-for="rec in followHistory"
            :key="rec.id"
            :type="timelineType(rec.status)"
            :title="`${rec.user_name || '—'} · ${followStatusLabel(rec.status)}`"
            :content="rec.note || ''"
            :time="formatTime(rec.created_at)"
          >
            <template
              v-if="rec.next_follow_at"
              #footer
            >
              下次跟进：{{ formatTime(rec.next_follow_at) }}
            </template>
          </n-timeline-item>
        </n-timeline>
        <n-empty
          v-else
          description="暂无跟进记录"
          size="small"
        />
      </div>
      <n-form
        label-placement="left"
        label-width="90"
        style="margin-top: 12px"
      >
        <n-form-item
          label="跟进状态"
          required
        >
          <n-select
            v-model:value="followForm.status"
            :options="followOptions.statuses"
          />
        </n-form-item>
        <n-form-item label="跟进人">
          <n-select
            v-model:value="followForm.owner_id"
            :options="followOptions.users"
            placeholder="留空默认本人"
            clearable
            filterable
          />
        </n-form-item>
        <n-form-item label="跟进备注">
          <n-input
            v-model:value="followForm.note"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-count
            placeholder="本次沟通情况，如：已发 WhatsApp，对方明天回复"
          />
        </n-form-item>
        <n-form-item label="下次跟进">
          <n-date-picker
            v-model:value="followForm.next_follow"
            type="datetime"
            clearable
            style="width: 100%"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="followVisible = false">
            取消
          </n-button>
          <n-button
            type="primary"
            :loading="followSubmitting"
            @click="handleFollowSubmit"
          >
            保存跟进
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 导出弹窗：字段勾选（默认全选）+ 当前筛选口径 -->
    <n-modal
      v-model:show="exportVisible"
      preset="card"
      title="导出线索 CSV"
      style="width: 560px"
    >
      <p class="export-hint">
        按当前筛选条件导出（分页不生效，最多 5000 条）；UTF-8 BOM 编码，Excel 可直接打开。
      </p>
      <n-checkbox-group v-model:value="exportFields">
        <div class="export-fields">
          <n-checkbox
            v-for="f in EXPORT_FIELDS"
            :key="f.key"
            :value="f.key"
            :label="f.label"
          />
        </div>
      </n-checkbox-group>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="exportVisible = false">
            取消
          </n-button>
          <n-button
            type="primary"
            :loading="exportSubmitting"
            @click="handleExport"
          >
            导出（{{ exportFields.length }} 字段）
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 种子导入弹窗：粘贴/选择 CSV → POST /collect/leads/import（去重合并） -->
    <n-modal
      v-model:show="importVisible"
      preset="card"
      title="导入企业种子（Seed Pool）"
      style="width: 640px"
    >
      <p class="export-hint">
        首行表头 name,website,phone,country,city,industry（可省略）；每行一家企业，单次上限 2MB。命中去重键（域名/电话/名称）的行会合并到已有线索。
      </p>
      <n-input
        v-model:value="importCsvText"
        type="textarea"
        :rows="12"
        placeholder="首行表头 name,website,phone,country,city,industry；每行一家企业，如：&#10;某某科技有限公司,https://example.com,+8675512345678,CN,深圳,电子商务"
      />
      <div class="flex items-center gap-3 import-actions">
        <n-button
          size="small"
          secondary
          @click="triggerImportFile"
        >
          选择 CSV 文件
        </n-button>
        <div class="flex items-center gap-2">
          <n-switch
            v-model:value="importIsCn"
            size="small"
          />
          <span class="nl-hint">标记为中国出海企业</span>
        </div>
      </div>
      <input
        ref="importFileRef"
        type="file"
        accept=".csv,text/csv"
        style="display: none"
        @change="handleImportFile"
      >
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="importVisible = false">
            取消
          </n-button>
          <n-button
            type="primary"
            :loading="importSubmitting"
            @click="handleImportSubmit"
          >
            开始导入
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 自动分配（§24，主管操作） -->
    <n-modal
      v-model:show="autoAssignShow"
      preset="card"
      title="自动分配共享池线索"
      style="width: 520px"
    >
      <n-form
        label-placement="left"
        label-width="90"
      >
        <n-form-item
          label="候选销售"
          required
        >
          <n-select
            v-model:value="autoAssignForm.owner_ids"
            :options="followOptions.users"
            multiple
            filterable
            placeholder="可被分配的销售（多选）"
          />
        </n-form-item>
        <n-form-item label="每人上限">
          <n-input-number
            v-model:value="autoAssignForm.max_per_owner"
            :min="1"
            :max="500"
            style="width: 100%"
          />
        </n-form-item>
        <n-form-item label="等级限定">
          <n-select
            v-model:value="autoAssignForm.grade"
            :options="collectApi.GRADE_OPTIONS"
            clearable
            placeholder="不限定"
          />
        </n-form-item>
        <n-form-item label="最低分">
          <n-input-number
            v-model:value="autoAssignForm.min_score"
            :min="0"
            clearable
            style="width: 100%"
          />
        </n-form-item>
        <n-form-item label="本次上限">
          <n-input-number
            v-model:value="autoAssignForm.limit"
            :min="1"
            :max="1000"
            style="width: 100%"
          />
        </n-form-item>
      </n-form>
      <p class="nl-hint">
        规则：只分配「未分配」状态的共享池线索，按 Lead Score 降序轮转，兼顾各销售当前负载。
      </p>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="autoAssignShow = false">
            取消
          </n-button>
          <n-button
            type="primary"
            :loading="autoAssignSubmitting"
            @click="handleAutoAssign"
          >
            开始分配
          </n-button>
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
.stat-cn {
  color: #2080f0;
}
.stat-pending {
  color: #f0a020;
}
.stat-due {
  color: #d03050;
}
/* 跟进历史时间线：限制高度内部滚动，弹窗不至于被长历史撑爆 */
.follow-history {
  max-height: 220px;
  overflow-y: auto;
  padding: 4px 4px 0;
  border-bottom: 1px dashed var(--yz-border-color, #e0e0e6);
}
.nl-hint {
  font-size: 12px;
  color: var(--yz-text-secondary, #999);
  margin: 0;
}
.export-hint {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--yz-text-secondary, #888);
}
.export-fields {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px 10px;
  max-height: 320px;
  overflow-y: auto;
}
.import-actions {
  margin-top: 12px;
}
</style>
