// 销售域 API：预警 / AI 能力 / 分配 / 数据源
import request from './request'

// ---------- 高价值预警（§55） ----------

export interface LeadAlert {
  id: number
  lead_id: number
  lead_name: string
  lead_grade: string
  event_type: string
  payload: Record<string, unknown>
  note: string | null
  created_at: string
}

export interface AlertPage {
  items: LeadAlert[]
  total: number
  page: number
  page_size: number
}

export function listAlerts(page = 1, pageSize = 20) {
  return request.get<AlertPage, AlertPage>('/sales/alerts', { params: { page, page_size: pageSize } })
}

// ---------- AI 能力（§25/§26/§27） ----------

export interface AiAnalysis {
  summary: string
  whatsapp_opportunity: string
  pain_points: string[]
  products: Array<{ name: string; stars: number }>
  entry_point: string
  generated_by: 'llm' | 'template'
}

export function getAiAnalysis(leadId: number) {
  return request.get<AiAnalysis, AiAnalysis>(`/sales/leads/${leadId}/ai-analysis`)
}

/** 生成销售话术（同步返回，不入审核队列） */
export async function generateSalesScript(leadId: number): Promise<{ script: string; generated_by: string }> {
  return request.post<{ script: string; generated_by: string }, { script: string; generated_by: string }>(
    `/sales/leads/${leadId}/sales-script`,
  )
}

// ---------- 分配（§24） ----------

export function assignLead(leadId: number, ownerId: number) {
  return request.post<unknown, unknown>(`/collect/leads/${leadId}/assign`, { owner_id: ownerId })
}

export function releaseLead(leadId: number) {
  return request.post<unknown, unknown>(`/collect/leads/${leadId}/release`)
}

export interface AutoAssignPayload {
  owner_ids: number[]
  max_per_owner?: number
  grade?: string
  min_score?: number
  industry?: string
  country?: string
  limit?: number
}

export function autoAssignLeads(payload: AutoAssignPayload) {
  return request.post<
    { assigned_count: number; per_owner: Array<{ owner_id: number; owner_name: string | null; count: number }> },
    { assigned_count: number; per_owner: Array<{ owner_id: number; owner_name: string | null; count: number }> }
  >('/collect/leads/auto-assign', payload)
}

// ---------- 数据源（§33） ----------

export interface DataSourceStat {
  collector: string
  title: string
  tasks: number
  success_rate: number | null
  error_rate: number | null
  leads_added: number
  leads_merged: number
  last_run_at: string | null
  status: 'active' | 'idle'
  /** 爬取逻辑与循环复核说明（抓什么/怎么滤/准确机制/复核节奏/边界） */
  logic_note: string
}

export function getDataSources() {
  return request.get<DataSourceStat[], DataSourceStat[]>('/sales/data-sources')
}
