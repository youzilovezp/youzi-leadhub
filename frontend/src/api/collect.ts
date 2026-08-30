// 线索采集 API
import request from './request'

// ---------- 类型 ----------

export interface Lead {
  id: number
  name: string
  country: string | null
  city: string | null
  industry: string | null
  address: string | null
  phone_raw: string | null
  phone_e164: string | null
  website: string | null
  domain: string | null
  email: string | null
  social: Record<string, string>
  whatsapp_hit: boolean
  whatsapp_url: string | null
  whatsapp_job: boolean
  job_urls: string[]
  enriched_at: string | null
  score: number
  /** 六维分 {维度键: 维度分}（overseas/whatsapp/saas/scale/marketing/contact） */
  score_signals: Record<string, number>
  grade: Grade
  /** WhatsApp 场景（website_enrich 关键词检测） */
  scenes: SceneKey[]
  /** SaaS 需求信号 {键: 命中关键词数} */
  saas_signals: Record<string, number>
  /** 页面出现的全部 WhatsApp 号码（多分线 = 规模化证据，§4.1） */
  whatsapp_numbers: string[]
  sources: Array<{ source: string; first_seen: string; last_seen: string }>
  owner_id: number | null
  owner_name: string | null
  follow_status: FollowStatus | null
  last_followed_at: string | null
  next_follow_at: string | null
  is_cn: boolean
  fb_whatsapp: boolean
  /** 投放/目标国家（meta_ads 累计，§8） */
  target_countries: string[]
  export_type: string | null
  /** 字段级数据质量 {字段: {source, updated_at, confidence}}（§32） */
  field_meta: Record<string, { source: string; updated_at: string; confidence: number }>
  contacts_count: number
  recommended_products: string[]
  created_at: string
  updated_at: string
}

/** 等级（六维总分：80+=S 60-79=A 40-59=B <40=C） */
export type Grade = 'S' | 'A' | 'B' | 'C'

export const GRADE_OPTIONS: Array<{ value: Grade; label: string }> = [
  { value: 'S', label: 'S 级（立即跟进）' },
  { value: 'A', label: 'A 级（高潜力）' },
  { value: 'B', label: 'B 级（培育池）' },
  { value: 'C', label: 'C 级（暂不优先）' },
]

/** 等级 → NTag type（S 红=A 级热度最高，C 灰） */
export function gradeTagType(g: string): 'error' | 'warning' | 'info' | 'default' {
  if (g === 'S') return 'error'
  if (g === 'A') return 'warning'
  if (g === 'B') return 'info'
  return 'default'
}

/** WhatsApp 场景键（后端 collectors/scenes.py 词表一致） */
export type SceneKey = 'customer_service' | 'marketing' | 'transactional' | 'saas'

/** 出海信号键 → 中文（§4.2，与后端 collectors/overseas.py 对齐） */
export const OVERSEAS_LABELS: Record<string, string> = {
  currencies: '海外货币',
  languages: '多语言版本',
  ecommerce: '电商平台',
  shipping: '海外配送',
  markets: '海外市场提及',
  export_words: '出海自述',
}

export const SCENE_LABELS: Record<string, string> = {
  customer_service: '客服',
  marketing: '营销',
  transactional: '交易通知',
  saas: 'SaaS',
}

/** SaaS 需求信号键 → 中文（后端 SAAS_LABELS_ZH 一致） */
export const SAAS_LABELS: Record<string, string> = {
  crm: 'CRM',
  helpdesk: '工单/客服系统',
  chatbot: '聊天机器人',
  ai_service: 'AI 客服',
  marketing_automation: '营销自动化',
  omnichannel: '全渠道',
}

/** 六维键 → 中文（后端 DIM_LABELS_ZH 一致） */
export const DIM_LABELS: Array<{ key: string; label: string; weight: number }> = [
  { key: 'overseas', label: '出海指数', weight: 25 },
  { key: 'whatsapp', label: 'WhatsApp 指数', weight: 30 },
  { key: 'saas', label: 'SaaS 需求', weight: 20 },
  { key: 'scale', label: '企业规模', weight: 10 },
  { key: 'marketing', label: '营销活跃', weight: 10 },
  { key: 'contact', label: '联系人质量', weight: 5 },
]

/** 跟进状态（后端 FOLLOW_STATUS_OPTIONS 同词表；PRD §23 十态） */
export type FollowStatus =
  | 'unassigned'
  | 'pending'
  | 'contacted'
  | 'replied'
  | 'opportunity'
  | 'quote'
  | 'negotiation'
  | 'won'
  | 'invalid'
  | 'paused'

export const FOLLOW_STATUS_OPTIONS: Array<{ value: FollowStatus; label: string }> = [
  { value: 'unassigned', label: '未分配' },
  { value: 'pending', label: '待跟进' },
  { value: 'contacted', label: '已联系' },
  { value: 'replied', label: '已回复' },
  { value: 'opportunity', label: '有效商机' },
  { value: 'quote', label: '报价' },
  { value: 'negotiation', label: '谈判' },
  { value: 'won', label: '成交' },
  { value: 'invalid', label: '无效' },
  { value: 'paused', label: '暂不考虑' },
]

/** 漏斗阶段顺序（统计/漏斗图口径） */
export const FUNNEL_STAGES: FollowStatus[] = [
  'unassigned',
  'pending',
  'contacted',
  'replied',
  'opportunity',
  'quote',
  'negotiation',
  'won',
]

/** 状态 → 中文（null=未分配） */
export function followStatusLabel(v: string | null): string {
  return FOLLOW_STATUS_OPTIONS.find((s) => s.value === v)?.label ?? '未分配'
}

/** 状态 → NTag 配色（沿漏斗推进加深） */
export function followStatusTagType(v: string | null): 'default' | 'info' | 'warning' | 'success' | 'error' {
  if (v === 'won') return 'success'
  if (v === 'opportunity' || v === 'quote' || v === 'negotiation') return 'warning'
  if (v === 'pending' || v === 'contacted' || v === 'replied') return 'info'
  if (v === 'invalid') return 'error'
  return 'default' // unassigned / paused / null
}

/** 跟进历史记录（弹窗时间线） */
export interface FollowUpRecord {
  id: number
  lead_id: number
  user_id: number | null
  user_name: string | null
  status: FollowStatus
  note: string | null
  next_follow_at: string | null
  created_at: string
}

export interface FollowUpPayload {
  status: FollowStatus
  owner_id?: number
  note?: string
  /** ISO 字符串（UTC） */
  next_follow_at?: string
}

/** 跟进弹窗下拉选项（/collect/follow-options 一次拉全） */
export interface FollowOptions {
  statuses: Array<{ value: FollowStatus; label: string }>
  users: Array<{ value: number; label: string }>
}

export interface LeadPage {
  items: Lead[]
  total: number
  page: number
  page_size: number
}

export interface LeadQuery {
  page?: number
  page_size?: number
  country?: string
  industry?: string
  source?: string
  min_score?: number
  grade?: Grade
  whatsapp_hit?: boolean
  has_website?: boolean
  keyword?: string
  follow_status?: FollowStatus
  owner_id?: number
  /** 只看「该回访了」（下次跟进时间已到期） */
  due_follow?: boolean
  /** 只看中国出海企业 */
  is_cn?: boolean
}

export interface LeadCreatePayload {
  name: string
  country?: string
  city?: string
  industry?: string
  address?: string
  phone?: string
  website?: string
  email?: string
}

// ---------- 联系人 / 事件 / 详情 ----------

export type Seniority = 'tier1' | 'tier2' | 'tier3' | 'unknown'

export const SENIORITY_LABELS: Record<string, string> = {
  tier1: '决策层',
  tier2: '市场/客服负责人',
  tier3: '技术/产品',
  unknown: '未识别',
}

export interface Contact {
  id: number
  lead_id: number
  name: string | null
  job_title: string | null
  department: string | null
  email: string | null
  phone: string | null
  linkedin: string | null
  seniority: Seniority | null
  confidence: number
  source: 'manual' | 'website_enrich' | string
  created_at: string
  updated_at: string
}

export interface ContactPayload {
  name?: string
  job_title?: string
  department?: string
  email?: string
  phone?: string
  linkedin?: string
  confidence?: number
}

/** 动态事件（详情页时间线，与跟进历史合并展示） */
export interface LeadEvent {
  id: number
  lead_id: number
  event_type: string
  payload: Record<string, unknown>
  note: string | null
  created_by: number | null
  created_at: string
}

export const EVENT_TYPE_LABELS: Record<string, string> = {
  source_added: '新来源',
  manual_entry: '手工录入',
  whatsapp_found: '发现 WhatsApp',
  whatsapp_job_found: '在招 WhatsApp 岗位',
  email_found: '发现邮箱',
  social_found: '新增社媒',
  scene_change: '场景变化',
  saas_signal_change: 'SaaS 需求信号',
  score_change: '评分变化',
  grade_change: '等级变化',
  contact_added: '新增联系人',
  assigned: '分配变动',
  opportunity_created: '新增商机',
  opportunity_stage: '商机推进',
}

export interface Recommendation {
  key: string
  name: string
  reason: string
  priority: number
}

/** 企业画像详情（GET /collect/leads/{id}） */
/** 信号级证据（PRD §4.1：系统为什么判定此客户有需求） */
export interface SignalEvidence {
  id: number
  signal_type: string
  signal_type_label: string
  value: string
  evidence_url: string | null
  evidence_raw: string | null
  confidence: number
  source: string
  first_seen: string | null
  last_seen: string | null
}

export interface LeadDetail extends Lead {
  dimensions: Record<string, number>
  dimension_weights: Record<string, number>
  contacts: Contact[]
  events: LeadEvent[]
  follow_ups: FollowUpRecord[]
  recommendations: Recommendation[]
  sales_suggestion: string
  /** 需求类型 A-E（§4.4）：[{type, label, selling}] */
  need_types: Array<{ type: string; label: string; selling: string }>
  /** 出海信号（§4.2）：{currencies/languages/ecommerce/shipping/markets/export_words: [证据]} */
  overseas_signals: Record<string, string[]>
  /** 招聘信号细分（§4.3）：{wa_ops/overseas_cs/...: {label, points}} */
  job_signals: Record<string, { label: string; points: number }>
  /** 广告信号（§4.1）：累计在投广告数 */
  ad_count: number
  last_ad_at: string | null
  /** 信号级证据链（§4.1） */
  signals: SignalEvidence[]
  /** 加分制明细（§五 MVP 口径）：{total, items}——items 只含命中项 [{key,label,points}] */
  score_breakdown: { total: number; items: Array<{ key: string; label: string; points: number }> }
  /** WhatsApp Business 账号（号码级验证命中） */
  wa_business: boolean
}

export interface CollectTask {
  id: number
  name: string
  collector: string
  params: Record<string, unknown>
  cron_expr: string | null
  enabled: boolean
  is_implicit: boolean
  created_by: number | null
  created_by_name: string | null
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress_total: number
  progress_done: number
  leads_added: number
  leads_merged: number
  error: string | null
  started_at: string | null
  finished_at: string | null
  last_run_at: string | null
  created_at: string
  updated_at: string
}

export interface TaskPage {
  items: CollectTask[]
  total: number
  page: number
  page_size: number
}

export interface TaskLog {
  id: number
  task_id: number
  level: 'info' | 'warn' | 'error'
  message: string
  created_at: string
}

export interface ParamOption {
  label: string
  value: string
}

/** 控件类型：select=可搜下拉(国家) cities=城市联动输入(依赖 depends_on 指向的国家字段)
 *  tags=标签输入(关键词) multiselect=多选(行业) switch=开关(布尔) number=数字输入
 *  text=文本(默认，缺 type 兼容旧采集器) */
export type ParamType = 'select' | 'cities' | 'tags' | 'multiselect' | 'switch' | 'number' | 'text'

export interface CollectorParam {
  key: string
  label: string
  required: boolean
  placeholder: string
  default: string
  type?: ParamType
  options?: ParamOption[]
  /** cities 联动：指向国家字段的 key */
  depends_on?: string
}

/** 国家/城市选项（/collect/geo-options，表单联动数据源） */
export interface GeoOptions {
  countries: ParamOption[]
  cities_by_country: Record<string, string[]>
}

export interface CollectorInfo {
  name: string
  title: string
  params: CollectorParam[]
}

export interface TaskCreatePayload {
  collector: string
  name?: string
  params: Record<string, string>
  cron_expr?: string
}

// ---------- 线索 ----------

export function listLeads(query: LeadQuery) {
  return request.get<LeadPage, LeadPage>('/collect/leads', { params: query })
}

export function createLead(payload: LeadCreatePayload) {
  return request.post<Lead, Lead>('/collect/leads', payload)
}

/** Seed Pool 批量导入结果（与后端 LeadImportResult 一致） */
export interface LeadImportResult {
  total: number
  created: number
  merged: number
  skipped: number
  errors: string[]
}

/** Seed Pool 批量导入企业种子（POST /collect/leads/import）：CSV 文本逐行走去重合并 */
export function importLeads(csvText: string, isCn = true) {
  return request.post<LeadImportResult, LeadImportResult>('/collect/leads/import', {
    csv_text: csvText,
    is_cn: isCn,
  })
}

export function deleteLead(id: number) {
  return request.delete<unknown, unknown>(`/collect/leads/${id}`)
}

/** 勾选线索 → 创建隐式 website_enrich 任务，返回任务（跳转详情轮询进度） */
export function checkWhatsApp(leadIds: number[]) {
  return request.post<CollectTask, CollectTask>('/collect/leads/check-whatsapp', { lead_ids: leadIds })
}

/** 企业画像详情（六维分/联系人/事件/跟进/推荐/销售建议） */
export function getLeadDetail(id: number) {
  return request.get<LeadDetail, LeadDetail>(`/collect/leads/${id}`)
}

/** 动态事件分页（时间线「加载更多」） */
export function getLeadEvents(id: number, page = 1, pageSize = 20) {
  return request.get<
    { items: LeadEvent[]; total: number; page: number; page_size: number },
    { items: LeadEvent[]; total: number; page: number; page_size: number }
  >(`/collect/leads/${id}/events`, { params: { page, page_size: pageSize } })
}

// ---------- 联系人 ----------

export function listContacts(leadId: number) {
  return request.get<Contact[], Contact[]>(`/collect/leads/${leadId}/contacts`)
}

export function createContact(leadId: number, payload: ContactPayload) {
  return request.post<Contact, Contact>(`/collect/leads/${leadId}/contacts`, payload)
}

export function updateContact(leadId: number, contactId: number, payload: ContactPayload) {
  return request.put<Contact, Contact>(`/collect/leads/${leadId}/contacts/${contactId}`, payload)
}

export function deleteContact(leadId: number, contactId: number) {
  return request.delete<unknown, unknown>(`/collect/leads/${leadId}/contacts/${contactId}`)
}

// ---------- 导出 ----------

/** 导出字段目录（key, 表头）——与后端 EXPORT_FIELDS 口径一致 */
export const EXPORT_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'id', label: 'ID' },
  { key: 'name', label: '企业名称' },
  { key: 'country', label: '国家' },
  { key: 'city', label: '城市' },
  { key: 'industry', label: '行业' },
  { key: 'website', label: '官网' },
  { key: 'domain', label: '域名' },
  { key: 'phone_e164', label: '电话(E.164)' },
  { key: 'phone_raw', label: '电话(原始)' },
  { key: 'email', label: '邮箱' },
  { key: 'grade', label: '等级' },
  { key: 'score', label: 'Lead Score' },
  { key: 'dim_overseas', label: '出海指数' },
  { key: 'dim_whatsapp', label: 'WhatsApp指数' },
  { key: 'dim_saas', label: 'SaaS需求' },
  { key: 'dim_scale', label: '企业规模' },
  { key: 'dim_marketing', label: '营销活跃' },
  { key: 'dim_contact', label: '联系人质量' },
  { key: 'whatsapp_hit', label: 'WhatsApp' },
  { key: 'whatsapp_url', label: 'WhatsApp链接' },
  { key: 'whatsapp_numbers', label: 'WhatsApp号码' },
  { key: 'whatsapp_job', label: '在招WA岗位' },
  { key: 'scenes', label: 'WhatsApp场景' },
  { key: 'saas_signals', label: 'SaaS需求信号' },
  { key: 'is_cn', label: '中国出海' },
  { key: 'fb_whatsapp', label: 'FB私域' },
  { key: 'job_urls', label: '在招岗位链接' },
  { key: 'sources', label: '来源' },
  { key: 'contacts_count', label: '联系人数' },
  { key: 'contacts_summary', label: '联系人明细' },
  { key: 'social', label: '社媒' },
  { key: 'recommended_products', label: '推荐产品' },
  { key: 'need_types', label: '需求类型' },
  { key: 'follow_status', label: '跟进状态' },
  { key: 'owner_name', label: '跟进人' },
  { key: 'created_at', label: '创建时间' },
]

/** 导出线索 CSV（blob 下载；fields 逗号分隔，缺省全部） */
export function exportLeads(query: LeadQuery, fields?: string[]) {
  return request.get<Blob, Blob>('/collect/leads/export', {
    params: { ...query, page: undefined, page_size: undefined, fields: fields?.join(',') },
    responseType: 'blob',
    timeout: 60000,
  })
}

// ---------- 跟进 ----------

/** 跟进弹窗选项（状态词表 + 可选跟进人） */
export function getFollowOptions() {
  return request.get<FollowOptions, FollowOptions>('/collect/follow-options')
}

/** 记录跟进：更新线索跟进人/状态并写一条历史，返回更新后的线索 */
export function followUpLead(id: number, payload: FollowUpPayload) {
  return request.post<Lead, Lead>(`/collect/leads/${id}/follow-up`, payload)
}

/** 跟进历史（最近 50 条，最新在前） */
export function getFollowUps(id: number) {
  return request.get<FollowUpRecord[], FollowUpRecord[]>(`/collect/leads/${id}/follow-ups`)
}

// ---------- 任务 ----------

export function getGeoOptions() {
  return request.get<GeoOptions, GeoOptions>('/collect/geo-options')
}

/** 线索行业筛选选项（库存 distinct + 中文名 + 数量；value 保持原 token 保证筛选精确） */
export interface IndustryOption {
  label: string
  value: string
  count: number
}

export function getIndustryOptions() {
  return request.get<IndustryOption[], IndustryOption[]>('/collect/industries')
}

export function listCollectors() {
  return request.get<CollectorInfo[], CollectorInfo[]>('/collect/collectors')
}

export function listTasks(query: { page?: number; page_size?: number; collector?: string; status?: string }) {
  return request.get<TaskPage, TaskPage>('/collect/tasks', { params: query })
}

export function createTask(payload: TaskCreatePayload) {
  return request.post<CollectTask, CollectTask>('/collect/tasks', payload)
}

export function getTask(id: number) {
  return request.get<CollectTask, CollectTask>(`/collect/tasks/${id}`)
}

export function updateTask(id: number, payload: Partial<TaskCreatePayload> & { enabled?: boolean }) {
  return request.put<CollectTask, CollectTask>(`/collect/tasks/${id}`, payload)
}

export function deleteTask(id: number) {
  return request.delete<unknown, unknown>(`/collect/tasks/${id}`)
}

export function runTask(id: number) {
  return request.post<unknown, unknown>(`/collect/tasks/${id}/run`)
}

export function cancelTask(id: number) {
  return request.post<unknown, unknown>(`/collect/tasks/${id}/cancel`)
}

export function getTaskLogs(id: number, afterId = 0, pageSize = 200) {
  return request.get<{ items: TaskLog[] }, { items: TaskLog[] }>(`/collect/tasks/${id}/logs`, {
    params: { after_id: afterId, page_size: pageSize },
  })
}

export interface CollectStats {
  total_leads: number
  whatsapp_leads: number
  high_intent_leads: number
  /** S/A/B/C 等级分布（销售优先级口径） */
  grade_counts: Record<Grade, number>
  active_tasks: number
  pending_leads: number
  due_follow_leads: number
  cn_leads: number
  fb_wa_leads: number
  /** 月度口径（§39） */
  month_new_leads: number
  month_won_count: number
}

export function getStats() {
  return request.get<CollectStats, CollectStats>('/collect/stats')
}
