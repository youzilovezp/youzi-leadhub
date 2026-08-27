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
  created_at: string
  updated_at: string
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

export interface CollectorParam {
  key: string
  label: string
  required: boolean
  placeholder: string
  default: string
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

// ---------- 任务 ----------

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
    { total_leads: number; whatsapp_leads: number; high_intent_leads: number; active_tasks: number },
    { total_leads: number; whatsapp_leads: number; high_intent_leads: number; active_tasks: number }
  >('/collect/stats')
}
