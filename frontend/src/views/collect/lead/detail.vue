<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NTag } from 'naive-ui'
import type { DataTableColumns, FormInst, FormRules } from 'naive-ui'
import * as collectApi from '@/api/collect'
import * as salesApi from '@/api/sales'
import type { Contact, ContactPayload, LeadDetail } from '@/api/collect'
import type { AiAnalysis } from '@/api/sales'
import {
  DIM_LABELS,
  EVENT_TYPE_LABELS,
  ICP_STATUS_LABELS,
  SAAS_LABELS,
  OVERSEAS_LABELS,
  SCENE_LABELS,
  SENIORITY_LABELS,
  followStatusTagType,
  gradeTagType,
} from '@/api/collect'
import { useUserStore } from '@/stores/user'
import { formatTime, parseUtc } from '@/utils/format'
import { confirm, message } from '@/utils/feedback'
import { downloadFile } from '@/utils/download'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const leadId = Number(route.params.id)

const detail = ref<LeadDetail | null>(null)
const loading = ref(false)

async function fetchDetail() {
  loading.value = true
  try {
    detail.value = await collectApi.getLeadDetail(leadId)
  } finally {
    loading.value = false
  }
}

// ---------- 六维条 ----------

const dims = computed(() => {
  const d = detail.value?.dimensions ?? {}
  return DIM_LABELS.map((x) => ({
    ...x,
    weight: detail.value?.dimension_weights?.[x.key] ?? x.weight,
    score: d[x.key] ?? 0,
  }))
})

// ---------- 撞单提示（§45）+ 分配（§24） ----------

const collisionHint = computed(() => {
  if (!detail.value?.owner_id) return null
  if (detail.value.owner_id === userStore.userInfo?.id) return null
  return `该企业已被 ${detail.value.owner_name || `#${detail.value.owner_id}`} 跟进，请勿重复建联（主管可转移/释放）`
})

const followOptions = ref<{ users: Array<{ value: number; label: string }> }>({ users: [] })
const assignTarget = ref<number | null>(null)
const assignShow = ref(false)

async function openAssign() {
  const opts = await collectApi.getFollowOptions()
  followOptions.value = opts
  assignTarget.value = detail.value?.owner_id ?? null
  assignShow.value = true
}

async function submitAssign() {
  if (!assignTarget.value) {
    message.warning('请选择跟进人')
    return
  }
  await salesApi.assignLead(leadId, assignTarget.value)
  message.success('已分配')
  assignShow.value = false
  fetchDetail()
}

async function handleRelease() {
  if (!(await confirm({ title: '提示', content: '释放回共享池？其他销售可认领。', positiveText: '释放' }))) return
  await salesApi.releaseLead(leadId)
  message.success('已释放')
  fetchDetail()
}

// ---------- 联系人 ----------

const contactModalShow = ref(false)
const contactEditing = ref<Contact | null>(null)
const contactForm = reactive({
  name: '',
  job_title: '',
  department: '',
  email: '',
  phone: '',
  linkedin: '',
})
const contactFormRef = ref<FormInst | null>(null)
const contactRules: FormRules = {
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
}

function openContactModal(contact?: Contact) {
  contactEditing.value = contact ?? null
  Object.assign(contactForm, {
    name: contact?.name ?? '',
    job_title: contact?.job_title ?? '',
    department: contact?.department ?? '',
    email: contact?.email ?? '',
    phone: contact?.phone ?? '',
    linkedin: contact?.linkedin ?? '',
  })
  contactModalShow.value = true
}

async function submitContact() {
  await contactFormRef.value?.validate()
  const payload: ContactPayload = {
    name: contactForm.name || undefined,
    job_title: contactForm.job_title || undefined,
    department: contactForm.department || undefined,
    email: contactForm.email || undefined,
    phone: contactForm.phone || undefined,
    linkedin: contactForm.linkedin || undefined,
  }
  if (contactEditing.value) {
    await collectApi.updateContact(leadId, contactEditing.value.id, payload)
    message.success('联系人已更新')
  } else {
    await collectApi.createContact(leadId, payload)
    message.success('联系人已新增')
  }
  contactModalShow.value = false
  fetchDetail()
}

async function removeContact(contact: Contact) {
  if (
    !(await confirm({
      title: '提示',
      content: `删除联系人「${contact.name || contact.email || `#${contact.id}`}」？`,
      positiveText: '删除',
    }))
  )
    return
  await collectApi.deleteContact(leadId, contact.id)
  message.success('已删除')
  fetchDetail()
}

function seniorityTagType(s: string | null): 'success' | 'info' | 'warning' | 'default' {
  if (s === 'tier1') return 'success'
  if (s === 'tier2') return 'info'
  if (s === 'tier3') return 'warning'
  return 'default'
}

/** 推荐联系人排序（PRD §七）：tier1 > tier2 > tier3 > unknown——仅前端展示排序，不改后端存储顺序 */
const SENIORITY_ORDER: Record<string, number> = { tier1: 0, tier2: 1, tier3: 2, unknown: 3 }

const sortedContacts = computed<Contact[]>(() =>
  [...(detail.value?.contacts ?? [])].sort(
    (a, b) =>
      (SENIORITY_ORDER[a.seniority ?? 'unknown'] ?? 3) - (SENIORITY_ORDER[b.seniority ?? 'unknown'] ?? 3),
  ),
)

const contactColumns: DataTableColumns<Contact> = [
  { title: '姓名', key: 'name', render: (r) => r.name || '—（待补全）' },
  { title: '职位', key: 'job_title', render: (r) => r.job_title || '待补全' },
  {
    title: '层级',
    key: 'seniority',
    width: 170,
    render: (r) =>
      r.seniority
        ? h('span', { class: 'flex items-center gap-1' }, [
            h(NTag, { size: 'small', type: seniorityTagType(r.seniority) }, () => SENIORITY_LABELS[r.seniority!] ?? r.seniority),
            // 决策层优先建联（推荐联系人），行内标注便于销售直读
            r.seniority === 'tier1' ? h(NTag, { size: 'small', type: 'error' }, () => '推荐') : null,
          ])
        : h('span', { style: 'color: var(--yz-text-secondary,#999)' }, '未分层'),
  },
  { title: '邮箱', key: 'email', render: (r) => r.email || '—' },
  { title: '电话', key: 'phone', render: (r) => r.phone || '—' },
  {
    title: '来源',
    key: 'source',
    width: 100,
    render: (r) => h(NTag, { size: 'small', bordered: false }, () => (r.source === 'manual' ? '手工' : '富化')),
  },
  {
    title: '操作',
    key: 'actions',
    width: 130,
    render: (r) =>
      h('div', { class: 'flex gap-2' }, [
        h(NButton, { size: 'tiny', quaternary: true, type: 'primary', onClick: () => openContactModal(r) }, () => '编辑'),
        h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => removeContact(r) }, () => '删除'),
      ]),
  },
]

// ---------- AI 能力（§25/§26） ----------

const aiLoading = ref(false)
const aiResult = ref<AiAnalysis | null>(null)
const scriptLoading = ref(false)
/** 卡内就地展示的最新话术（生成成功后不再强制跳页） */
const generatedScript = ref('')

async function runAiAnalysis() {
  aiLoading.value = true
  try {
    aiResult.value = await salesApi.getAiAnalysis(leadId)
  } finally {
    aiLoading.value = false
  }
}

async function generateScript() {
  scriptLoading.value = true
  try {
    const { script } = await salesApi.generateSalesScript(leadId)
    // 话术就地在卡片内展示（PRD §七：不强制跳页）
    generatedScript.value = script
    message.success('话术已生成')
  } finally {
    scriptLoading.value = false
  }
}

async function copyScript() {
  try {
    await navigator.clipboard.writeText(generatedScript.value)
    message.success('话术已复制')
  } catch {
    message.warning('剪贴板不可用（需 https 环境），请手动选中文本复制')
  }
}

// ---------- 加分明细（加分制 MVP 口径，§五） ----------

/** 加分条目配色：分值越大越暖（≥30 红 / 15-29 橙 / <15 灰） */
function bonusTagType(points: number): 'error' | 'warning' | 'default' {
  if (points >= 30) return 'error'
  if (points >= 15) return 'warning'
  return 'default'
}

// ---------- 字段级数据质量（§32） ----------

const FIELD_LABELS: Record<string, string> = {
  whatsapp_url: 'WhatsApp',
  email: '邮箱',
  social: '社媒',
  scenes: '场景',
  saas_signals: 'SaaS 信号',
  target_countries: '投放国',
  website: '官网',
  phone_e164: '电话',
}

const fieldQuality = computed(() =>
  Object.entries(detail.value?.field_meta ?? {}).map(([field, meta]) => ({
    field,
    label: FIELD_LABELS[field] ?? field,
    ...meta,
  })),
)

// ---------- 时间线（事件 + 跟进合并） ----------

interface TimelineItem {
  key: string
  time: string
  title: string
  content: string
  type: 'success' | 'info' | 'warning' | 'error' | 'default'
}

/** 排序键：原始 ISO → 毫秒（解析失败按 0，避免 NaN 破坏 sort）；展示文本排序前再映射 */
function timelineSortTs(iso: string): number {
  const ms = parseUtc(iso).getTime()
  return Number.isNaN(ms) ? 0 : ms
}

const timeline = computed<TimelineItem[]>(() => {
  if (!detail.value) return []
  const events: TimelineItem[] = detail.value.events.map((e) => ({
    key: `e-${e.id}`,
    time: e.created_at,
    title: EVENT_TYPE_LABELS[e.event_type] ?? e.event_type,
    content: e.note ?? '',
    type:
      e.event_type === 'whatsapp_found' || e.event_type === 'contact_added' || e.event_type === 'opportunity_created'
        ? 'success'
        : e.event_type === 'grade_change' || e.event_type === 'saas_signal_change'
          ? 'warning'
          : e.event_type === 'score_change' || e.event_type === 'scene_change'
            ? 'info'
            : 'default',
  }))
  const follows: TimelineItem[] = detail.value.follow_ups.map((f) => ({
    key: `f-${f.id}`,
    time: f.created_at,
    title: `跟进 · ${collectApi.followStatusLabel(f.status)}`,
    content: [f.note, f.next_follow_at ? `下次回访：${formatTime(f.next_follow_at)}` : ''].filter(Boolean).join('｜'),
    type: f.status === 'won' ? 'success' : f.status === 'invalid' ? 'error' : 'info',
  }))
  // 按原始时间倒序（最新在前）。不能对 formatTime 后的本地化文本排序：非补零格式跨日/跨月必错
  return [...events, ...follows]
    .sort((a, b) => timelineSortTs(b.time) - timelineSortTs(a.time))
    .map((x) => ({ ...x, time: formatTime(x.time) }))
})

// ---------- 导出单条 ----------

async function exportOne() {
  const d = new Date()
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  await downloadFile('/collect/leads/export', { keyword: detail.value?.domain ?? detail.value?.name }, `leads_${stamp}.csv`)
  message.success('导出已开始')
}

onMounted(fetchDetail)
</script>

<template>
  <div class="page">
    <n-spin :show="loading">
      <template v-if="detail">
        <!-- 撞单提示（§45） -->
        <n-alert
          v-if="collisionHint"
          type="warning"
          class="mb-3"
          :show-icon="true"
        >
          {{ collisionHint }}
        </n-alert>

        <!-- 头部：名称 + 等级 + 总分 + 信号标签 + 分配 -->
        <n-card
          size="small"
          class="mb-4"
        >
          <div class="flex flex-wrap items-center gap-3">
            <n-button
              quaternary
              size="small"
              @click="router.push('/collect/lead')"
            >
              ← 返回
            </n-button>
            <h2 class="detail-title">
              {{ detail.name }}
            </h2>
            <n-tag
              :type="gradeTagType(detail.grade)"
              size="small"
            >
              {{ detail.grade }} 级
            </n-tag>
            <n-tag
              size="small"
              :bordered="false"
            >
              Lead Score {{ detail.score }}
            </n-tag>
            <n-tag
              size="small"
              :bordered="false"
              :type="followStatusTagType(detail.follow_status)"
            >
              {{ collectApi.followStatusLabel(detail.follow_status) }}
            </n-tag>
            <n-tag
              v-if="detail.fb_whatsapp"
              type="success"
              size="small"
            >
              FB私域
            </n-tag>
            <n-tag
              v-if="detail.whatsapp_hit || detail.whatsapp_url"
              type="success"
              size="small"
            >
              WA
            </n-tag>
            <n-tag
              v-if="detail.whatsapp_job"
              type="warning"
              size="small"
            >
              在招WA
            </n-tag>
            <n-tag
              v-if="detail.is_cn"
              type="info"
              size="small"
            >
              中国出海
            </n-tag>
            <div class="flex-1" />
            <span class="owner-label">跟进人：{{ detail.owner_name || '未分配（共享池）' }}</span>
            <n-button
              size="small"
              secondary
              @click="openAssign"
            >
              分配/转移
            </n-button>
            <n-button
              v-if="detail.owner_id"
              size="small"
              quaternary
              type="warning"
              @click="handleRelease"
            >
              释放
            </n-button>
            <n-button
              size="small"
              secondary
              @click="exportOne"
            >
              导出 CSV
            </n-button>
          </div>
        </n-card>

        <div class="detail-grid grid gap-4">
          <!-- 左列 -->
          <div class="flex flex-col gap-4">
            <n-card
              size="small"
              title="六维评分"
            >
              <div
                v-for="dim in dims"
                :key="dim.key"
                class="dim-row"
              >
                <span class="dim-label">{{ dim.label }}</span>
                <n-progress
                  type="line"
                  :percentage="dim.score"
                  :height="10"
                  :color="dim.score >= 60 ? '#18a058' : dim.score >= 30 ? '#f0a020' : '#d03050'"
                  class="dim-bar"
                />
                <span class="dim-score">{{ dim.score }}<span class="dim-weight">/100 · 权重{{ dim.weight }}%</span></span>
              </div>
            </n-card>

            <!-- 加分明细（§五 MVP 口径：信号级可解释，与六维加权总分并存） -->
            <n-card
              size="small"
              title="信号加分（MVP 口径）"
            >
              <template #header-extra>
                <n-tag size="small">
                  加分制参考分 {{ detail.score_breakdown?.total ?? 0 }}
                </n-tag>
              </template>
              <template v-if="detail.score_breakdown?.items?.length">
                <div
                  v-for="item in detail.score_breakdown.items"
                  :key="item.key"
                  class="bonus-row"
                >
                  <span>{{ item.label }}</span>
                  <n-tag
                    size="small"
                    :type="bonusTagType(item.points)"
                  >
                    +{{ item.points }}
                  </n-tag>
                </div>
              </template>
              <div
                v-else
                class="empty-hint"
              >
                暂无命中信号（跑一轮富化/采集后产生）
              </div>
              <div class="bonus-note">
                与六维加权总分并存：六维为主分与分级依据，加分制为信号级可解释明细
              </div>
            </n-card>

            <n-card
              size="small"
              title="基础信息"
            >
              <div class="kv">
                <span class="k">国家/城市</span><span>{{ detail.country || '—' }} / {{ detail.city || '—' }}</span>
                <span class="k">行业</span><span>{{ detail.industry || '—' }}</span>
                <span class="k">地址</span><span>{{ detail.address || '—' }}</span>
                <span class="k">电话</span><span>{{ detail.phone_e164 || detail.phone_raw || '—' }}</span>
                <span class="k">邮箱</span><span>{{ detail.email || '—' }}</span>
                <span class="k">官网</span>
                <span>
                  <a
                    v-if="detail.website"
                    :href="detail.website"
                    target="_blank"
                    rel="noopener"
                    class="link"
                  >{{ detail.website }}</a>
                  <template v-else>—</template>
                </span>
                <span class="k">域名</span><span>{{ detail.domain || '—' }}</span>
                <span class="k">社媒</span>
                <span>
                  <template v-if="Object.keys(detail.social).length">
                    <n-tag
                      v-for="(url, platform) in detail.social"
                      :key="platform"
                      size="small"
                      class="mr-1"
                      :bordered="false"
                    >
                      {{ platform }}
                    </n-tag>
                  </template>
                  <template v-else>—</template>
                </span>
                <span class="k">来源</span>
                <span>{{ detail.sources.map((s) => s.source).join('、') || '—' }}</span>
                <span class="k">跟进人</span><span>{{ detail.owner_name || '未分配' }}</span>
                <span class="k">采集时间</span><span>{{ formatTime(detail.created_at) }}</span>
              </div>
            </n-card>

            <!-- 出海画像（§8） -->
            <n-card
              size="small"
              title="出海画像"
            >
              <!-- CTWA 代理信号（FB 主页挂 WhatsApp 按钮 = 有私域转化预算，醒目置顶） -->
              <div
                v-if="detail.fb_whatsapp"
                class="mb-2"
              >
                <n-tag
                  type="error"
                  size="small"
                >
                  🔥 CTWA 代理信号：FB 主页挂 WhatsApp 按钮
                </n-tag>
              </div>
              <div class="kv">
                <span class="k">中国出海</span><span>{{ detail.is_cn ? '✓ 是' : '—' }}</span>
                <span class="k">ICP 资格</span><span>{{ ICP_STATUS_LABELS[detail.icp_status] ?? detail.icp_status }}</span>
                <span class="k">主要市场</span>
                <span>
                  <template v-if="detail.target_countries?.length">
                    <n-tag
                      v-for="c in detail.target_countries"
                      :key="c"
                      size="small"
                      type="info"
                      class="mr-1"
                    >{{ c }}</n-tag>
                  </template>
                  <template v-else>未识别（跑 meta_ads 采集后累计）</template>
                </span>
                <span class="k">业务类型</span><span>{{ detail.export_type || '—' }}</span>
                <span class="k">FB 私域</span><span>{{ detail.fb_whatsapp ? '✓ 主页带 wa.me' : '—' }}</span>
                <span class="k">在投广告</span>
                <span>
                  <template v-if="detail.ad_count">
                    ✓ {{ detail.ad_count }} 条（{{ detail.last_ad_at ? '最近 ' + formatTime(detail.last_ad_at) : '' }}）
                  </template>
                  <template v-else>—</template>
                </span>
                <template v-for="(vals, key) in detail.overseas_signals" :key="key">
                  <span class="k">{{ OVERSEAS_LABELS[key] ?? key }}</span>
                  <span>
                    <n-tag
                      v-for="v in vals.slice(0, 6)"
                      :key="v"
                      size="small"
                      type="warning"
                      class="mr-1"
                    >{{ v }}</n-tag>
                    <span v-if="vals.length > 6" class="dim-weight">等 {{ vals.length }} 项</span>
                  </span>
                </template>
              </div>
            </n-card>

            <n-card
              size="small"
              title="WhatsApp 画像"
            >
              <div class="kv">
                <span class="k">已发现</span><span>{{ detail.whatsapp_hit || detail.whatsapp_url ? '✓ 是' : '✗ 否' }}</span>
                <span class="k">WhatsApp Business</span><span>{{ detail.wa_business ? '✓ 业务号' : '—' }}</span>
                <span class="k">入口链接</span>
                <span>
                  <a
                    v-if="detail.whatsapp_url"
                    :href="detail.whatsapp_url"
                    target="_blank"
                    rel="noopener"
                    class="link"
                  >{{ detail.whatsapp_url }}</a>
                  <template v-else>—</template>
                </span>
                <span class="k">号码证据</span>
                <span>
                  <template v-if="detail.whatsapp_numbers?.length">
                    <n-tag
                      v-for="n in detail.whatsapp_numbers.slice(0, 5)"
                      :key="n"
                      size="small"
                      type="success"
                      class="mr-1"
                    >
                      +{{ n }}
                    </n-tag>
                    <span
                      v-if="detail.whatsapp_numbers.length > 5"
                      class="dim-weight"
                    >等 {{ detail.whatsapp_numbers.length }} 个</span>
                  </template>
                  <template v-else>—</template>
                </span>
                <span class="k">在招岗位</span><span>{{ detail.whatsapp_job ? `✓ ${detail.job_urls.length} 个` : '✗ 无' }}</span>
                <span class="k">招聘信号</span>
                <span>
                  <template v-if="Object.keys(detail.job_signals ?? {}).length">
                    <n-tag
                      v-for="(meta, key) in detail.job_signals"
                      :key="key"
                      size="small"
                      :type="key === 'wa_ops' ? 'error' : 'warning'"
                      class="mr-1"
                    >{{ meta.label }} +{{ meta.points }}</n-tag>
                  </template>
                  <template v-else>—</template>
                </span>
                <span class="k">场景</span>
                <span>
                  <template v-if="detail.scenes.length">
                    <n-tag
                      v-for="s in detail.scenes"
                      :key="s"
                      size="small"
                      type="info"
                      class="mr-1"
                    >✓ {{ SCENE_LABELS[s] ?? s }}</n-tag>
                  </template>
                  <template v-else>未检测到（需富化）</template>
                </span>
              </div>
            </n-card>

            <!-- 信号证据链（§4.1：系统为什么判定此客户有需求） -->
            <n-card
              v-if="detail.signals?.length"
              size="small"
              title="信号证据链"
            >
              <template #header-extra>
                <span class="dim-weight">{{ detail.signals.length }} 条证据</span>
              </template>
              <div
                v-for="sig in detail.signals.slice(0, 12)"
                :key="sig.id"
                class="signal-row"
              >
                <n-tag size="small" :bordered="false" class="signal-type">{{ sig.signal_type_label || sig.signal_type }}</n-tag>
                <span class="signal-value">
                  {{ sig.value }}
                  <a
                    v-if="sig.evidence_url"
                    :href="sig.evidence_url"
                    target="_blank"
                    rel="noopener"
                    class="link"
                  >来源页</a>
                </span>
                <span class="dim-weight">{{ sig.confidence }}% · {{ sig.source || '未知来源' }} · {{ formatTime(sig.first_seen) }}</span>
              </div>
              <div v-if="detail.signals.length > 12" class="dim-weight mt-2">
                等 {{ detail.signals.length }} 条（按置信度排序，前 12 条）
              </div>
            </n-card>

            <n-card
              size="small"
              title="SaaS 需求"
            >
              <template v-if="Object.keys(detail.saas_signals).length">
                <div
                  v-for="(count, key) in detail.saas_signals"
                  :key="key"
                  class="saas-row"
                >
                  <span class="dim-label">{{ SAAS_LABELS[key] ?? key }}</span>
                  <n-rate
                    :value="Math.min(5, count + 2)"
                    readonly
                    allow-half
                    class="saas-rate"
                  />
                  <span class="dim-weight">命中 {{ count }} 个关键词</span>
                </div>
              </template>
              <div
                v-else
                class="empty-hint"
              >
                未检测到 SaaS 需求信号（运行「检测 WhatsApp」富化后自动识别）
              </div>
              <div class="saas-dim">
                SaaS 需求维度分：{{ detail.dimensions.saas ?? 0 }}/100
              </div>
            </n-card>

            <!-- 字段级数据质量（§32） -->
            <n-card
              v-if="fieldQuality.length"
              size="small"
              title="数据质量（来源 / 最后验证 / 置信度）"
            >
              <div class="kv">
                <template
                  v-for="fq in fieldQuality"
                  :key="fq.field"
                >
                  <span class="k">{{ fq.label }}</span>
                  <span>{{ fq.source }} · {{ formatTime(fq.updated_at) }} · {{ fq.confidence }}%</span>
                </template>
              </div>
            </n-card>
          </div>

          <!-- 右列 -->
          <div class="flex flex-col gap-4">
            <!-- AI 销售助手（§25/§26） -->
            <n-card
              size="small"
              title="AI 销售助手"
            >
              <template #header-extra>
                <n-tag
                  v-if="aiResult"
                  size="small"
                  :bordered="false"
                  :type="aiResult.generated_by === 'llm' ? 'success' : 'default'"
                >
                  {{ aiResult.generated_by === 'llm' ? 'LLM 生成' : '规则模板（未配置 LLM）' }}
                </n-tag>
              </template>
              <div class="flex gap-3 mb-3">
                <n-button
                  size="small"
                  type="primary"
                  secondary
                  :loading="aiLoading"
                  @click="runAiAnalysis"
                >
                  AI 分析客户
                </n-button>
                <n-button
                  size="small"
                  secondary
                  :loading="scriptLoading"
                  @click="generateScript"
                >
                  生成话术
                </n-button>
              </div>
              <!-- 话术就地在卡内展示（PRD §七：不强制跳页） -->
              <div
                v-if="generatedScript"
                class="script-block"
              >
                <div class="script-text">{{ generatedScript }}</div>
                <div class="flex items-center gap-2 mt-2">
                  <n-button
                    size="tiny"
                    secondary
                    @click="copyScript"
                  >
                    复制话术
                  </n-button>
                </div>
              </div>
              <template v-if="aiResult">
                <div class="ai-block">
                  <span class="ai-k">企业概况</span>{{ aiResult.summary }}
                </div>
                <div class="ai-block">
                  <span class="ai-k">WhatsApp 机会</span>{{ aiResult.whatsapp_opportunity }}
                </div>
                <div
                  v-if="aiResult.pain_points.length"
                  class="ai-block"
                >
                  <span class="ai-k">潜在痛点</span>
                  <div class="ai-list">
                    <div
                      v-for="(p, i) in aiResult.pain_points"
                      :key="i"
                    >
                      {{ i + 1 }}. {{ p }}
                    </div>
                  </div>
                </div>
                <div
                  v-if="aiResult.products.length"
                  class="ai-block"
                >
                  <span class="ai-k">推荐产品</span>
                  <div class="ai-products">
                    <n-tag
                      v-for="p in aiResult.products"
                      :key="p.name"
                      size="small"
                      type="info"
                      class="mr-1"
                    >
                      {{ '★'.repeat(p.stars) }}{{ '☆'.repeat(Math.max(0, 5 - p.stars)) }} {{ p.name }}
                    </n-tag>
                  </div>
                </div>
                <div class="ai-block">
                  <span class="ai-k">切入点</span>{{ aiResult.entry_point }}
                </div>
              </template>
              <div
                v-else
                class="empty-hint"
              >
                点击「AI 分析客户」生成企业概况 / 机会 / 痛点 / 推荐产品 / 切入点
              </div>
            </n-card>

            <!-- 联系人 -->
            <n-card
              size="small"
              title="联系人"
            >
              <template #header-extra>
                <n-button
                  size="small"
                  type="primary"
                  secondary
                  @click="openContactModal()"
                >
                  新增联系人
                </n-button>
              </template>
              <n-data-table
                :columns="contactColumns"
                :data="sortedContacts"
                :row-key="(r: Contact) => r.id"
                size="small"
                :scroll-x="760"
              />
            </n-card>

            <n-card
              size="small"
              title="需求类型与推荐"
            >
              <template v-if="detail.need_types?.length">
                <div class="need-row">
                  <n-tag
                    v-for="n in detail.need_types"
                    :key="n.type"
                    size="small"
                    type="warning"
                    class="mr-1"
                  >
                    {{ n.label }}
                  </n-tag>
                </div>
                <div class="need-selling">
                  {{ detail.need_types.map((n) => n.selling).join('；') }}
                </div>
                <n-divider style="margin: 8px 0" />
              </template>
              <template v-if="detail.recommendations.length">
                <div
                  v-for="rec in detail.recommendations"
                  :key="rec.key"
                  class="rec-row"
                >
                  <span class="rec-name">{{ rec.name }}</span>
                  <span class="rec-reason">{{ rec.reason }}</span>
                </div>
              </template>
              <div
                v-else
                class="empty-hint"
              >
                暂无推荐（需要 WhatsApp 使用 + 场景证据）
              </div>
            </n-card>

            <n-card
              size="small"
              title="销售建议"
            >
              <div class="suggestion">
                {{ detail.sales_suggestion || '暂无建议' }}
              </div>
            </n-card>

            <n-card
              size="small"
              title="动态时间线（事件 + 跟进）"
            >
              <n-timeline v-if="timeline.length">
                <n-timeline-item
                  v-for="item in timeline"
                  :key="item.key"
                  :type="item.type"
                  :title="item.title"
                  :content="item.content"
                  :time="item.time"
                />
              </n-timeline>
              <n-empty
                v-else
                description="暂无动态"
                class="empty-hint"
              />
            </n-card>
          </div>
        </div>

        <!-- 联系人编辑弹窗 -->
        <n-modal
          v-model:show="contactModalShow"
          preset="card"
          title="联系人"
          style="width: 520px"
        >
          <n-form
            ref="contactFormRef"
            :model="contactForm"
            :rules="contactRules"
            label-placement="left"
            label-width="72"
          >
            <n-form-item
              label="姓名"
              path="name"
            >
              <n-input
                v-model:value="contactForm.name"
                placeholder="如 张三"
              />
            </n-form-item>
            <n-form-item
              label="职位"
              path="job_title"
            >
              <n-input
                v-model:value="contactForm.job_title"
                placeholder="如 CEO / Marketing Director（自动分层）"
              />
            </n-form-item>
            <n-form-item
              label="部门"
              path="department"
            >
              <n-input v-model:value="contactForm.department" />
            </n-form-item>
            <n-form-item
              label="邮箱"
              path="email"
            >
              <n-input
                v-model:value="contactForm.email"
                placeholder="同线索内唯一"
              />
            </n-form-item>
            <n-form-item
              label="电话"
              path="phone"
            >
              <n-input v-model:value="contactForm.phone" />
            </n-form-item>
            <n-form-item
              label="LinkedIn"
              path="linkedin"
            >
              <n-input v-model:value="contactForm.linkedin" />
            </n-form-item>
          </n-form>
          <template #footer>
            <div class="flex justify-end gap-3">
              <n-button @click="contactModalShow = false">
                取消
              </n-button>
              <n-button
                type="primary"
                @click="submitContact"
              >
                保存
              </n-button>
            </div>
          </template>
        </n-modal>

        <!-- 分配弹窗 -->
        <n-modal
          v-model:show="assignShow"
          preset="card"
          title="分配跟进人"
          style="width: 420px"
        >
          <n-select
            v-model:value="assignTarget"
            :options="followOptions.users"
            placeholder="选择跟进人"
            clearable
            filterable
          />
          <template #footer>
            <div class="flex justify-end gap-3">
              <n-button @click="assignShow = false">
                取消
              </n-button>
              <n-button
                type="primary"
                @click="submitAssign"
              >
                分配
              </n-button>
            </div>
          </template>
        </n-modal>
      </template>
    </n-spin>
  </div>
</template>

<style scoped>
.detail-title {
  font-size: 16px;
  margin: 0;
}
.owner-label {
  font-size: 13px;
  color: var(--yz-text-secondary, #888);
}
.detail-grid {
  grid-template-columns: 1fr 1fr;
  align-items: start;
}
@media (max-width: 1100px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
.kv {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 8px 12px;
  font-size: 13px;
}
.kv .k {
  color: var(--yz-text-secondary, #888);
}
.link {
  color: var(--yz-primary, #2080f0);
  word-break: break-all;
}
.dim-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 0;
}
.dim-label {
  width: 108px;
  flex-shrink: 0;
  font-size: 13px;
}
.dim-bar {
  flex: 1;
}
.dim-score {
  width: 118px;
  text-align: right;
  font-size: 13px;
  flex-shrink: 0;
}
.dim-weight {
  color: var(--yz-text-secondary, #999);
  font-size: 12px;
  margin-left: 4px;
}
.signal-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px dashed var(--n-border-color);
  flex-wrap: wrap;
}
.signal-row:last-child {
  border-bottom: none;
}
.signal-type {
  min-width: 96px;
  justify-content: center;
}
.signal-value {
  flex: 1;
  min-width: 160px;
  word-break: break-all;
}

/* 加分明细行：label 左、+分右（颜色按分值在模板里配） */
.bonus-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 5px 0;
  border-bottom: 1px dashed var(--yz-border, #eee);
  font-size: 13px;
}
.bonus-row:last-of-type {
  border-bottom: none;
}
.bonus-note {
  margin-top: 8px;
  color: var(--yz-text-secondary, #999);
  font-size: 12px;
}
/* 话术引用块：白底 + 主色左边线，保留换行便于逐句复制 */
.script-block {
  margin-top: 4px;
  padding: 10px 12px;
  background: var(--yz-bg-card, #fff);
  border: 1px solid var(--yz-border, #eee);
  border-left: 3px solid var(--yz-primary, #2080f0);
  border-radius: 4px;
}
.script-text {
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-all;
}

.saas-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}
.saas-rate {
  flex: 1;
}
.saas-dim {
  margin-top: 8px;
  color: var(--yz-text-secondary, #888);
  font-size: 12px;
}
.ai-block {
  font-size: 13px;
  line-height: 1.7;
  margin-bottom: 8px;
}
.ai-k {
  display: inline-block;
  color: var(--yz-text-secondary, #888);
  margin-right: 8px;
  flex-shrink: 0;
}
.ai-list {
  display: inline-block;
}
.ai-products {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}
.need-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
}
.need-selling {
  font-size: 12px;
  color: var(--yz-text-secondary, #888);
  line-height: 1.6;
}
.rec-row {
  display: flex;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--yz-border, #eee);
  font-size: 13px;
}
.rec-row:last-child {
  border-bottom: none;
}
.rec-name {
  flex-shrink: 0;
  font-weight: 500;
}
.rec-reason {
  color: var(--yz-text-secondary, #888);
}
.suggestion {
  font-size: 13px;
  line-height: 1.8;
}
.empty-hint {
  color: var(--yz-text-secondary, #999);
  font-size: 13px;
  padding: 8px 0;
}
.mr-1 {
  margin-right: 4px;
}
</style>
