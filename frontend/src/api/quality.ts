/** 质量抽检（§十二 验证闭环）：队列 / 标注 / 指标。 */
import request from './request'

export type ReviewField = 'whatsapp' | 'overseas' | 'contact'
export type ReviewVerdict = 'correct' | 'incorrect' | 'unsure'

export const REVIEW_FIELD_LABELS: Record<ReviewField, string> = {
  whatsapp: 'WhatsApp 识别',
  overseas: '出海识别',
  contact: '联系人有效',
}

export interface QueueItem {
  lead_id: number
  name: string
  grade: string
  score: number
  icp_status: string
  evidence: {
    whatsapp_url?: string | null
    whatsapp_numbers?: string[]
    website?: string | null
    overseas_signals?: Record<string, string[]>
    target_countries?: string[]
    contacts?: Array<{ email: string | null; phone: string | null; name: string | null; job_title: string | null }>
  }
}

export interface QualityStats {
  fields: Record<
    ReviewField,
    {
      label: string
      target: number
      reviewed: number
      correct: number
      incorrect: number
      unsure: number
      accuracy: number | null
      meets_target: boolean | null
    }
  >
  sa_ratio: { value: number; target: number; grade_counts: Record<string, number> }
  coverage: Record<ReviewField, { reviewed: number; pool: number }>
  note: string
}

export function getReviewQueue(field: ReviewField, size = 10) {
  return request.get<{ field: string; label: string; items: QueueItem[] }, { field: string; label: string; items: QueueItem[] }>(
    '/quality/queue',
    { params: { field, size } },
  )
}

export function submitReview(payload: { lead_id: number; field: ReviewField; verdict: ReviewVerdict; note?: string }) {
  return request.post<unknown, unknown>('/quality/review', payload)
}

export function getQualityStats() {
  return request.get<QualityStats, QualityStats>('/quality/stats')
}
