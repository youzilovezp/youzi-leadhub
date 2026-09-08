/**
 * 代理式销售运营（agent）API 客户端。
 *
 * 类型与后端 app/schemas/agent.py 严格对齐——运行时验证靠 request.ts 拦截器。
 *
 * request.ts 拦截器会 unwrap 返回 { code, data } → data（去掉外层包装），
 * 所以签名是 `request.get<TResp, TResolved>(...)`，TResolved 才是真实返回类型。
 */

import request from './request'

// ---------- 序列 ----------

export interface OutreachStep {
  day_offset: number
  channel: string
  template_hint: string
}

export interface OutreachSequence {
  id: number
  name: string
  description: string | null
  scenario: string
  channel: string
  steps: OutreachStep[]
  active: boolean
  owner_id: number | null
  created_at: string
  updated_at: string
}

export interface SequenceCreate {
  name: string
  description?: string | null
  scenario?: string
  channel?: string
  steps: OutreachStep[]
}

export interface SequenceUpdate {
  name?: string
  description?: string | null
  steps?: OutreachStep[]
  active?: boolean
}

export const listSequences = (params: {
  active?: boolean
  scenario?: string
  page?: number
  page_size?: number
} = {}) =>
  request.get<unknown, { items: OutreachSequence[]; total: number }>('/agent/sequences', {
    params,
  })

export const getSequence = (id: number) =>
  request.get<unknown, OutreachSequence>(`/agent/sequences/${id}`)

export const createSequence = (payload: SequenceCreate) =>
  request.post<unknown, OutreachSequence>('/agent/sequences', payload)

export const updateSequence = (id: number, payload: SequenceUpdate) =>
  request.patch<unknown, OutreachSequence>(`/agent/sequences/${id}`, payload)

export const deleteSequence = (id: number) =>
  request.delete<unknown, { deleted: number }>(`/agent/sequences/${id}`)

// ---------- 草稿与排程 ----------

export interface OutreachDraftRequest {
  lead_id: number
  sequence_id?: number | null
  step_index?: number | null
  channel?: string | null
  contact_id?: number | null
  context_hint?: string | null
}

export const draftOne = (payload: OutreachDraftRequest) =>
  request.post<unknown, OutreachMessage>('/agent/drafts', payload)

export interface ScheduleRequest {
  sequence_id: number
  lead_ids: number[]
  start_at?: string | null
  owner_id?: number | null
}

export const scheduleOutreach = (payload: ScheduleRequest) =>
  request.post<unknown, { scheduled: number; skipped: number; errors: string[] }>(
    '/agent/schedule',
    payload,
  )

// ---------- 外联消息 ----------

export interface OutreachMessage {
  id: number
  lead_id: number
  contact_id: number | null
  sequence_id: number | null
  step_index: number | null
  channel: string
  subject: string | null
  body: string
  status: string
  llm_generated: boolean
  generated_by: string
  scheduled_at: string | null
  sent_at: string | null
  sent_error: string | null
  reply_id: number | null
  owner_id: number | null
  locked_at: string | null
  approved_by: number | null
  created_at: string
  updated_at: string
  lead_name?: string
  contact_name?: string
}

export const listMessages = (params: {
  lead_id?: number
  status?: string
  channel?: string
  sequence_id?: number
  page?: number
  page_size?: number
} = {}) =>
  request.get<
    unknown,
    { items: (OutreachMessage & { lead_name: string; contact_name: string })[]; total: number }
  >('/agent/messages', { params })

export const getMessage = (id: number) =>
  request.get<unknown, OutreachMessage>(`/agent/messages/${id}`)

export const updateMessage = (
  id: number,
  payload: { subject?: string; body?: string; scheduled_at?: string | null },
) => request.patch<unknown, OutreachMessage>(`/agent/messages/${id}`, payload)

export const approveMessage = (id: number) =>
  request.post<unknown, OutreachMessage>(`/agent/messages/${id}/approve`)

export const markSent = (id: number, sent_error?: string) =>
  request.post<unknown, OutreachMessage>(`/agent/messages/${id}/mark-sent`, null, {
    params: { sent_error },
  })

export const markNoReply = (id: number) =>
  request.post<unknown, OutreachMessage>(`/agent/messages/${id}/mark-no-reply`)

// ---------- 回复 ----------

export interface Reply {
  id: number
  lead_id: number
  message_id: number | null
  channel: string
  from_address: string | null
  subject: string | null
  body: string
  sentiment: string | null
  intent: string | null
  summary: string | null
  received_at: string
  processed_at: string | null
  crm_action: string | null
  llm_labeled: boolean
  overridden: boolean
  handled_by: number | null
  created_at: string
  updated_at: string
  lead_name?: string
}

export const listReplies = (params: {
  lead_id?: number
  intent?: string
  sentiment?: string
  unprocessed_only?: boolean
  page?: number
  page_size?: number
} = {}) =>
  request.get<unknown, { items: (Reply & { lead_name: string })[]; total: number }>(
    '/agent/replies',
    { params },
  )

export interface ReplyCreate {
  lead_id: number
  body: string
  channel?: string
  message_id?: number | null
  from_address?: string | null
  subject?: string | null
  sentiment?: string | null
  intent?: string | null
}

export const createReply = (payload: ReplyCreate) =>
  request.post<unknown, Reply>('/agent/replies', payload)

export const overrideReplyLabel = (
  id: number,
  payload: { sentiment?: string | null; intent?: string | null },
) => request.patch<unknown, Reply>(`/agent/replies/${id}/label`, payload)

export const syncReplyCRM = (payload: { reply_id: number; target_status?: string | null }) =>
  request.post<unknown, { crm_action: string }>('/agent/replies/sync-crm', payload)

// ---------- 商机预测 ----------

export interface ForecastDeal {
  id: number
  lead_id: number
  name: string
  stage: string
  amount: number
  probability: number
  close_date: string | null
  is_primary: boolean
  note: string | null
  owner_id: number | null
  weighted_amount: number
  created_at: string
  updated_at: string
  lead_name?: string
}

export const listDeals = (params: {
  stage?: string
  is_open?: boolean
  page?: number
  page_size?: number
} = {}) =>
  request.get<unknown, { items: (ForecastDeal & { lead_name: string })[]; total: number }>(
    '/agent/deals',
    { params },
  )

export interface ForecastDealCreate {
  lead_id: number
  name: string
  stage: string
  amount: number
  probability?: number | null
  close_date?: string | null
  is_primary?: boolean
  note?: string | null
}

export const createDeal = (payload: ForecastDealCreate) =>
  request.post<unknown, ForecastDeal>('/agent/deals', payload)

export const updateDeal = (id: number, payload: Partial<ForecastDealCreate>) =>
  request.patch<unknown, ForecastDeal>(`/agent/deals/${id}`, payload)

export const deleteDeal = (id: number) =>
  request.delete<unknown, { deleted: number }>(`/agent/deals/${id}`)

// ---------- 预测看板 ----------

export interface ForecastSummary {
  total_amount: number
  weighted_total: number
  open_weighted: number
  deal_count: number
  open_deal_count: number
  by_stage: Record<string, { count: number; amount: number; weighted: number }>
  by_owner: Record<string, { count: number; amount: number; weighted: number }>
}

export const getForecastSummary = () =>
  request.get<unknown, ForecastSummary>('/agent/forecast/summary')

export interface ForecastSnapshot {
  id: number
  period: string
  period_start: string
  period_end: string
  data: Record<string, unknown>
  computed_at: string
}

export const takeSnapshot = (period: 'weekly' | 'monthly') =>
  request.post<unknown, ForecastSnapshot>('/agent/forecast/snapshot', null, {
    params: { period },
  })

export const listSnapshots = (params: { period?: string; limit?: number } = {}) =>
  request.get<unknown, ForecastSnapshot[]>('/agent/forecast/snapshots', { params })
