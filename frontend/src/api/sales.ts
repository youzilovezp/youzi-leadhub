// 销售域 API：商机 / 话术队列 / 预警 / AI 能力 / 漏斗排行数据源
import request from './request'
import type { LeadQuery } from './collect'

// ---------- 商机（§37） ----------

export interface Opportunity {
  id: number
  lead_id: number
  name: string
  amount: number
  stage: string // opportunity/quote/negotiation/won/lost
  expected_close_at: string | null
  won_at: string | null
  owner_id: number | null
  owner_name: string | null
  note: string | null
  created_at: string
  updated_at: string
}

export interface OpportunityPayload {
  name?: string
  amount?: number
  stage?: string
  expected_close_at?: string
  owner_id?: number
  note?: string
}

export const OPPORTUNITY_STAGE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'opportunity', label: '有效商机' },
  { value: 'quote', label: '报价' },
  { value: 'negotiation', label: '谈判' },
  { value: 'won', label: '成交' },
  { value: 'lost', label: '失去' },
]

export function opportunityStageLabel(s: string): string {
  return OPPORTUNITY_STAGE_OPTIONS.find((x) => x.value === s)?.label ?? s
}

/** 商机阶段 → NTag 配色（沿漏斗推进加深，won 绿 lost 灰） */
export function opportunityStageTagType(s: string): 'info' | 'warning' | 'success' | 'error' | 'default' {
  if (s === 'won') return 'success'
  if (s === 'lost') return 'default'
  if (s === 'opportunity') return 'info'
  return 'warning' // quote / negotiation
}

export function listOpportunities(leadId: number) {
  return request.get<Opportunity[], Opportunity[]>(`/sales/leads/${leadId}/opportunities`)
}

export function createOpportunity(leadId: number, payload: OpportunityPayload) {
  return request.post<Opportunity, Opportunity>(`/sales/leads/${leadId}/opportunities`, payload)
}

export function updateOpportunity(leadId: number, oppId: number, payload: OpportunityPayload) {
  return request.put<Opportunity, Opportunity>(`/sales/leads/${leadId}/opportunities/${oppId}`, payload)
}

export function deleteOpportunity(leadId: number, oppId: number) {
  return request.delete<unknown, unknown>(`/sales/leads/${leadId}/opportunities/${oppId}`)
}

// ---------- 话术审核队列（§56） ----------

export interface SalesMessage {
  id: number
  lead_id: number
  lead_name: string | null
  channel: string
  content: string
  status: 'draft' | 'approved' | 'sent' | 'rejected'
  generated_by: 'llm' | 'template'
  created_by: number | null
  reviewed_by: number | null
  sent_at: string | null
  created_at: string
  updated_at: string
}

export const MESSAGE_STATUS_LABELS: Record<string, string> = {
  draft: '待审核',
  approved: '已通过',
  sent: '已发送',
  rejected: '已驳回',
}

export function messageStatusTagType(s: string): 'warning' | 'info' | 'success' | 'error' {
  if (s === 'sent') return 'success'
  if (s === 'approved') return 'info'
  if (s === 'rejected') return 'error'
  return 'warning'
}

export interface MessagePage {
  items: SalesMessage[]
  total: number
  page: number
  page_size: number
}

export function listMessages(params: { page?: number; page_size?: number; status?: string; lead_id?: number }) {
  return request.get<MessagePage, MessagePage>('/sales/messages', { params })
}

export function generateMessage(leadId: number) {
  return request.post<SalesMessage, SalesMessage>(`/sales/leads/${leadId}/messages/generate`)
}

export function reviewMessage(messageId: number, action: 'approve' | 'reject' | 'mark_sent') {
  return request.post<SalesMessage, SalesMessage>(`/sales/messages/${messageId}/review`, { action })
}

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

export function generateSalesScript(leadId: number) {
  return request.post<{ script: string; generated_by: string }, { script: string; generated_by: string }>(
    `/sales/leads/${leadId}/sales-script`,
  )
}

/** 自然语言 → 结构化筛选参数；未配置 LLM 返回业务错误（code!==0 走统一 toast） */
export function searchNl(text: string) {
  return request.post<{ params: Partial<LeadQuery> }, { params: Partial<LeadQuery> }>('/sales/leads/search-nl', {
    text,
  })
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

// ---------- 漏斗 / 排行榜 / 数据源（§38-§40/§33） ----------

export interface FunnelStats {
  stages: Record<string, number>
  opportunities: Record<string, { count: number; amount: number }>
  won_amount: number
  arpu: number
  total_leads: number
}

export function getFunnel() {
  return request.get<FunnelStats, FunnelStats>('/sales/funnel')
}

export interface LeaderboardRow {
  owner_id: number
  owner_name: string | null
  leads: number
  opportunities: number
  won: number
  won_amount: number
}

export function getLeaderboard() {
  return request.get<LeaderboardRow[], LeaderboardRow[]>('/sales/leaderboard')
}

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
}

export function getDataSources() {
  return request.get<DataSourceStat[], DataSourceStat[]>('/sales/data-sources')
}
