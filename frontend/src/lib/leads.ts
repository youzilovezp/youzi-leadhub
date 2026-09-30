export type LeadTier = 'hot' | 'warm' | 'normal'

// 2026-10-01 校准：后端分数刻度实测 0-13（理论上限 ~22）——旧阈值 80/70 按
// 错误假设的 0-100 刻度设定，hot 永不触发。P0（中国出海）无论分数都是最高
// 优先目标 → 直接 hot。
export const TIER_THRESHOLDS = { hotScore: 12, warmScore: 8 }

export function getTier(input: { p0: number; score: number }): LeadTier {
  const { hotScore, warmScore } = TIER_THRESHOLDS
  if (input.p0 || input.score >= hotScore) return 'hot'
  if (input.score >= warmScore) return 'warm'
  return 'normal'
}

export const TIER_STYLE: Record<LeadTier, { border: string; bg: string }> = {
  hot:    { border: 'border-red-500',   bg: 'bg-red-50/50 dark:bg-red-950/20' },
  warm:   { border: 'border-amber-500', bg: 'bg-amber-50/40 dark:bg-amber-950/15' },
  normal: { border: 'border-transparent', bg: '' },
}
