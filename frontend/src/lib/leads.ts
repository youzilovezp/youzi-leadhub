export type LeadTier = 'hot' | 'warm' | 'normal'

export const TIER_THRESHOLDS = { hotScore: 80, warmScore: 70 }

export function getTier(input: { p0: number; score: number }): LeadTier {
  const { hotScore, warmScore } = TIER_THRESHOLDS
  if (input.p0 && input.score >= hotScore) return 'hot'
  if (input.p0 || input.score >= warmScore) return 'warm'
  return 'normal'
}

export const TIER_STYLE: Record<LeadTier, { border: string; bg: string }> = {
  hot:    { border: 'border-red-500',   bg: 'bg-red-50/50 dark:bg-red-950/20' },
  warm:   { border: 'border-amber-500', bg: 'bg-amber-50/40 dark:bg-amber-950/15' },
  normal: { border: 'border-transparent', bg: '' },
}
