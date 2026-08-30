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
  score_signals: Record<string, number>
  sources: Array<{ source: string; first_seen: string; last_seen: string }>
  owner_id: number | null
  owner_name: string | null
  follow_status: FollowStatus | null
  last_followed_at: string | null
  next_follow_at: string | null
  created_at: string
  updated_at: string
}

/** 跟进状态（后端 FOLLOW_STATUS_OPTIONS 同词表） */
export type FollowStatus =
  | 'pending'
  | 'following'
  | 'interested'
  | 'not_interested'
  | 'unreachable'
  | 'converted'

export const FOLLOW_STATUS_OPTIONS: Array<{ value: FollowStatus; label: string }> = [
  { value: 'pending', label: '待跟进' },
  { value: 'following', label: '跟进中' },
  { value: 'interested', label: '有意向' },
  { value: 'not_interested', label: '无意向' },
  { value: 'unreachable', label: '联系不上' },
  { value: 'converted', label: '已成交' },
]

/** 状态 → 中文（null/未知值兜底「待跟进」） */
export function followStatusLabel(v: string | null): string {
  return FOLLOW_STATUS_OPTIONS.find((s) => s.value === v)?.label ?? '待跟进'
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
  whatsapp_hit?: boolean
  has_website?: boolean
  keyword?: string
  follow_status?: FollowStatus
  owner_id?: number
  /** 只看「该回访了」（下次跟进时间已到期） */
  due_follow?: boolean
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

export function deleteLead(id: number) {
  return request.delete<unknown, unknown>(`/collect/leads/${id}`)
}

/** 勾选线索 → 创建隐式 website_enrich 任务，返回任务（跳转详情轮询进度） */
export function checkWhatsApp(leadIds: number[]) {
  return request.post<CollectTask, CollectTask>('/collect/leads/check-whatsapp', { lead_ids: leadIds })
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

export function getStats() {
  return request.get<
    {
      total_leads: number
      whatsapp_leads: number
      high_intent_leads: number
      active_tasks: number
      pending_leads: number
      due_follow_leads: number
    },
    {
      total_leads: number
      whatsapp_leads: number
      high_intent_leads: number
      active_tasks: number
      pending_leads: number
      due_follow_leads: number
    }
  >('/collect/stats')
}
