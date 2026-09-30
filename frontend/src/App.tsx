import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Check,
  Copy,
  Database,
  Download,
  ExternalLink,
  Globe,
  Loader2,
  MapPin,
  Phone,
  Search,
  ShoppingBag,
  Smartphone,
  TestTube2,
  X,
  Zap,
} from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Logo } from '@/components/Logo'
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from '@/components/ui/pagination'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { getTier, TIER_STYLE } from '@/lib/leads'

/* ============================================================
 * 类型
 * ============================================================ */
type Stat = {
  channel: string
  total: number
  hits: number
  p0: number
  hit_rate: number
}

type Lead = {
  entity: string
  channel: string
  market: string | null
  market_group: string | null
  lang: string | null
  p0: number
  score: number
  phones: string
  widget: string | null
  developer_name: string | null
}

type CrawlJob = {
  job_id: string
  channel: string
  pid: number
  started_at: number
  status: 'created' | 'running' | 'exited' | 'failed' | 'killed'
  exit_code: number | null
  log: string | null
}

const PAGE_SIZE = 24

/* WhatsApp 品牌标（FA5 brands 轮廓，官方绿 #25D366） */
function WhatsAppIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 448 512" fill="currentColor" aria-hidden className={className}>
      <path d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z" />
    </svg>
  )
}

/* 渠道视觉身份：zinc 单色，与 Logo 配色同源 */
const CHANNEL_IDENTITY: Record<string, { icon: typeof Globe; chip: string; grad: string }> = {
  play:      { icon: Smartphone,  chip: 'bg-foreground/10 text-foreground',
                                grad: 'from-foreground/8 to-foreground/4' },
  myshopify: { icon: ShoppingBag, chip: 'bg-muted text-muted-foreground',
                                grad: 'from-muted/30 to-muted/10' },
  osm:       { icon: Globe,       chip: 'bg-muted text-muted-foreground',
                                grad: 'from-muted/30 to-muted/10' },
  sample:    { icon: TestTube2,   chip: 'bg-muted text-muted-foreground',
                                grad: 'from-muted/30 to-muted/10' },
}
const channelStyle = (c: string) =>
  CHANNEL_IDENTITY[c] ?? { icon: Globe, chip: 'bg-muted text-muted-foreground',
                            grad: 'from-muted/30 to-muted/10' }

/* 渠道码 → 中文名（销售可读；导出 CSV 仍用机器码） */
const CHANNEL_CN: Record<string, string> = {
  play: 'Google Play 商店',
  myshopify: 'Shopify 独立站',
  osm: '海外地图商户',
  sample: '示例数据',
}
const channelName = (c: string) => CHANNEL_CN[c] ?? c

const MARKET_GROUPS = ['SEA', 'LATAM', 'MENA', 'EU', 'OTHER'] as const
type MarketGroup = (typeof MARKET_GROUPS)[number]

/* entity → 官网 URL（自动补 https，兼容裸域）；用于表格行点击直达 */
function entityUrl(entity: string): string {
  return /^https?:\/\//i.test(entity) ? entity : `https://${entity}`
}

/* phone → wa.me 链接（去掉 +、空格、连字符；用于表格点击直接唤起 WhatsApp） */
function waUrl(phone: string): string {
  const digits = phone.replace(/[^0-9]/g, '')
  return `https://wa.me/${digits}`
}

/* ============================================================
 * 国家码 → 中文名（按线索面板常出现的覆盖；缺省回退原码）
 * ============================================================ */
const MARKET_CN: Record<string, string> = {
  CN: '中国', HK: '香港', MO: '澳门', TW: '台湾',
  ID: '印尼', MY: '马来西亚', TH: '泰国', PH: '菲律宾',
  VN: '越南', SG: '新加坡', BN: '文莱', KH: '柬埔寨',
  LA: '老挝', MM: '缅甸',
  BR: '巴西', MX: '墨西哥', CO: '哥伦比亚', AR: '阿根廷',
  CL: '智利', PE: '秘鲁', VE: '委内瑞拉', UY: '乌拉圭',
  AE: '阿联酋', SA: '沙特', EG: '埃及', TR: '土耳其',
  IL: '以色列', QA: '卡塔尔', KW: '科威特',
  DE: '德国', FR: '法国', IT: '意大利', ES: '西班牙',
  NL: '荷兰', PL: '波兰', RU: '俄罗斯', UA: '乌克兰',
  GB: '英国',
  US: '美国', CA: '加拿大',
  AU: '澳大利亚', NZ: '新西兰',
  IN: '印度', PK: '巴基斯坦', BD: '孟加拉', LK: '斯里兰卡',
  JP: '日本', KR: '韩国',
  ZA: '南非', NG: '尼日利亚', KE: '肯尼亚',
}
const marketName = (code: string | null) => {
  if (!code) return '—'
  return MARKET_CN[code.toUpperCase()] ?? code
}

/* ============================================================
 * 视觉原子
 * ============================================================ */
function ScorePill({ score }: { score: number }) {
  const tone =
    score >= 13 ? 'bg-primary/10 text-primary font-semibold'
    : score >= 8  ? 'bg-muted text-foreground/80 font-medium'
    : score >= 5  ? 'bg-muted text-muted-foreground'
    : 'bg-muted text-muted-foreground'
  return (
    <span className={`inline-flex min-w-9 justify-center rounded-md px-2 py-0.5 text-xs tabular-nums ${tone}`}>
      {score}
    </span>
  )
}

function P0Badge({ active }: { active: boolean }) {
  if (!active) return <span className="text-muted-foreground/60 text-sm">—</span>
  return (
    <span
      title="高优 = 推荐跟进：中国出海线索（开发者含中国公司名 / ICP 备案 / 中国 TLD 等强信号）。例：vstarcam.cn = 中国监控品牌出海"
      className="inline-flex items-center rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-primary"
    >
      高优
    </span>
  )
}

/* ============================================================
 * 数据 hooks
 * ============================================================ */
function usePanelData() {
  const [stats, setStats] = useState<Stat[] | null>(null)
  const [leads, setLeads] = useState<Lead[] | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [channel, setChannel] = useState('all')
  const [marketGroup, setMarketGroup] = useState('all')
  const [search, setSearch] = useState('')
  const [tick, setTick] = useState(0)

  const refresh = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(false)

    const qs = new URLSearchParams({ limit: '500' })
    if (channel !== 'all') qs.set('channel', channel)
    if (marketGroup !== 'all') qs.set('market_group', marketGroup)

    Promise.all([
      fetch('/api/stats').then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.json() as Promise<Stat[]>
      }),
      fetch(`/api/leads?${qs}`).then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.json() as Promise<Lead[]>
      }),
    ])
      .then(([s, l]) => {
        if (!alive) return
        setStats(s)
        setLeads(l)
        setUpdatedAt(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
      })
      .catch(() => alive && setError(true))
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [channel, marketGroup, tick])

  return {
    stats, leads, error, loading, updatedAt,
    channel, setChannel, marketGroup, setMarketGroup,
    search, setSearch, refresh,
  }
}

function useCrawlStatus() {
  const [jobs, setJobs] = useState<CrawlJob[]>([])
  /* health：{channel: 连续补种失败次数}——streak≥3 才出现（后端口径），冒横幅 */
  const [health, setHealth] = useState<Record<string, number>>({})
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    let lastRaw: string | null = null
    const poll = () => {
      fetch('/api/crawl/status')
        .then((r) => (r.ok ? r.text() : null))
        .then((raw) => {
          /* 性能：空闲时响应逐字节相同 → 跳过 setState，避免全应用 2s 一次的
           * 无效重渲染（jobs 在 App 根组件，一次更新带动整棵树 diff） */
          if (!alive || raw === null || raw === lastRaw) return
          lastRaw = raw
          const d = JSON.parse(raw) as { jobs: CrawlJob[]; health?: Record<string, number> }
          setJobs(d.jobs ?? [])
          setHealth(d.health ?? {})
        })
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 2000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [tick])

  return { jobs, health, bump: () => setTick((t) => t + 1) }
}

/* ============================================================
 * API 客户端
 * ============================================================ */
/* 智能爬取（2026-09-30 合并入口）：一键黑盒——后端自动挑最优渠道增量爬 +
   轮换业务场景后台挖新人群。销售不选渠道/模式/页数。*/
async function triggerCrawl(opts: {
  channels: string[]
  mode: 'incremental' | 'full' | 'smart'
  limit: number
  max_pages: number
}) {
  const r = await fetch('/api/crawl', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json() as Promise<{ mode: string; spawned: CrawlJob[]; skipped: { channel: string; reason: string }[]; total: number; seeded?: number }>
}

async function exportCsv(opts: { channel?: string; marketGroup?: string }) {
  const qs = new URLSearchParams()
  if (opts.channel && opts.channel !== 'all') qs.set('channel', opts.channel)
  if (opts.marketGroup && opts.marketGroup !== 'all') qs.set('market_group', opts.marketGroup)
  const r = await fetch(`/api/leads?${qs}&limit=2000`)
  if (!r.ok) throw new Error(`${r.status}`)
  const leads = await r.json()
  const headerRow = ['entity', 'channel', 'market', 'market_group', 'lang', 'p0', 'score',
                     'developer_name', 'widget', 'phones']
  const rows = leads.map((l: Lead) => [
    l.entity, l.channel, l.market ?? '', l.market_group ?? '', l.lang ?? '',
    l.p0 ? '1' : '0', l.score, l.developer_name ?? '', l.widget ?? '', l.phones,
  ])
  const csv = '\uFEFF' + [headerRow, ...rows].map((r) => r.map((c: string) =>
    /[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : c).join(',')
  ).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const stamp = new Date().toISOString().slice(0, 10)
  a.download = `youzi-bsp-leads-${stamp}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/* ============================================================
 * 通用组件
 * ============================================================ */
function pageWindow(current: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const out: (number | '…')[] = [1]
  const lo2 = Math.max(2, current - 1)
  const hi = Math.min(total - 1, current + 1)
  if (lo2 > 2) out.push('…')
  for (let i = lo2; i <= hi; i++) out.push(i)
  if (hi < total - 1) out.push('…')
  out.push(total)
  return out
}

function PanelError({ onRetry, label }: { onRetry: () => void; label: string }) {
  return (
    <Alert variant="destructive">
      <AlertCircle className="size-4" />
      <AlertTitle>{label}</AlertTitle>
      <AlertDescription className="flex items-center gap-3">
        后端未响应，请确认 API 服务已启动。
        <Button variant="outline" size="sm" onClick={onRetry}>重试</Button>
      </AlertDescription>
    </Alert>
  )
}

/* 智能爬取运行指示：仅在有 RUNNING 任务时显示的非交互短条（黑盒——不暴露跳转/日志入口）。 */
function CrawlStatusBar({ jobs }: { jobs: CrawlJob[] }) {
  const running = jobs.filter((j) => j.status === 'running')
  if (running.length === 0) return null
  const head = running[0]
  const more = running.length - 1
  return (
    <div
      role="status"
      className="inline-flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs"
    >
      <Loader2 className="size-3 animate-spin text-primary" />
      <span className="font-medium">智能爬取进行中</span>
      <span className="font-semibold text-foreground/80">{channelName(head.channel)}</span>
      {more > 0 && (
        <span className="rounded-full bg-rose-500 px-1.5 text-[10px] font-semibold text-white">
          +{more}
        </span>
      )}
    </div>
  )
}

/* ============================================================
 * Tab 1：渠道命中率卡
 * ============================================================ */
function StatsCards({
  stats, loading, error, onRetry, activeChannel, onChannelClick, avgRate,
}: {
  stats: Stat[] | null
  loading: boolean
  error: boolean
  onRetry: () => void
  activeChannel: string
  onChannelClick: (ch: string) => void
  avgRate: number
}) {
  if (error) return <PanelError onRetry={onRetry} label="无法加载统计数据" />
  if (stats === null) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-28 w-full rounded-xl" />
        ))}
      </div>
    )
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {stats.map((s) => {
        const Style = channelStyle(s.channel)
        const Icon = Style.icon
        const isActive = activeChannel === s.channel
        const isPrimary = s.channel === 'play'
        return (
          <button
            key={s.channel}
            type="button"
            onClick={() => onChannelClick(s.channel)}
            className={cn(
              'group relative flex flex-col gap-2 overflow-hidden rounded-xl border bg-card p-4 text-left',
              'transition-colors hover:bg-muted/40',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
              isActive && 'border-primary/50 bg-primary/5',
              !isActive && 'border-border/70'
            )}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={cn(
                  'inline-flex size-6 items-center justify-center rounded-md',
                  isPrimary ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
                )}>
                  <Icon className="size-3.5" aria-hidden />
                </span>
                <p className="text-xs font-medium text-muted-foreground">{channelName(s.channel)}</p>
              </div>
              <span
                title={`${channelName(s.channel)} 渠道高优（推荐跟进）线索数 ${s.p0}`}
                className={cn(
                  'inline-flex h-5 items-center rounded-md px-1.5 text-[10px] font-semibold tabular-nums tracking-wide',
                  isPrimary ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
                )}
              >
                高优 ×{s.p0}
              </span>
            </div>
            <p className="text-2xl font-semibold tabular-nums tracking-tight">
              {(s.hit_rate * 100).toFixed(1)}
              <span className="ml-0.5 text-sm font-normal text-muted-foreground">%</span>
            </p>
            <div className="relative h-1 overflow-hidden rounded-full bg-muted">
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-primary/80"
                style={{ width: `${Math.min(100, s.hit_rate * 100)}%` }}
              />
              <div
                aria-hidden
                className="absolute inset-y-0 w-px bg-foreground/50"
                style={{ left: `${Math.min(100, avgRate * 100)}%` }}
                title={`全渠道平均 ${(avgRate * 100).toFixed(1)}%`}
              />
            </div>
            <div className="flex items-baseline justify-between text-[11px] tabular-nums text-muted-foreground">
              <span>{s.hits.toLocaleString()} / {s.total.toLocaleString()}</span>
            </div>
          </button>
        )
      })}
    </div>
  )
}

/* ============================================================
 * Tab 1：线索表
 * ============================================================ */
function LeadsTable({
  leads, loading, error, onRetry, channel, marketGroup,
  channelFilter, setChannelFilter, marketGroupFilter, setMarketGroupFilter,
  search, setSearch, stats,
}: {
  leads: Lead[] | null
  loading: boolean
  error: boolean
  onRetry: () => void
  channel: string
  marketGroup: string
  channelFilter: string
  setChannelFilter: (v: string) => void
  marketGroupFilter: string
  setMarketGroupFilter: (v: string) => void
  search: string
  setSearch: (v: string) => void
  stats: Stat[] | null
}) {
  const [page, setPage] = useState(1)
  const [copied, setCopied] = useState<string | null>(null)
  const filtered = useMemo(() => {
    if (!leads) return []
    if (!search.trim()) return leads
    const q = search.trim().toLowerCase()
    return leads.filter((l) =>
      l.entity.toLowerCase().includes(q) ||
      (l.developer_name && l.developer_name.toLowerCase().includes(q)) ||
      l.phones.toLowerCase().includes(q)
    )
  }, [leads, search])

  useEffect(() => setPage(1), [channel, marketGroup, search])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const pageLeads = useMemo(
    () => filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [filtered, page]
  )

  useEffect(() => {
    if (!copied) return
    const t = setTimeout(() => setCopied(null), 1200)
    return () => clearTimeout(t)
  }, [copied])

  const copyPhones = (entity: string, phones: string) => {
    navigator.clipboard?.writeText(phones).then(() => setCopied(entity))
  }

  const filterCount = (channel !== 'all' ? 1 : 0) + (marketGroup !== 'all' ? 1 : 0) +
                      (search ? 1 : 0)

  return (
    <section aria-labelledby="leads-heading" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="leads-heading" className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-muted-foreground">线索列表</span>
          {leads !== null && !error && (
            <span className="text-xs font-normal tabular-nums text-muted-foreground">
              {filtered.length}{filtered.length !== leads?.length ? ` / ${leads.length}` : ''}
            </span>
          )}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/70" />
            <Input
              type="search"
              placeholder="搜索实体 / 电话"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-64 rounded-lg bg-muted/40 pl-9 shadow-none transition-colors focus-visible:bg-background"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                aria-label="清空搜索"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground/70 transition-colors hover:text-foreground"
              >
                <X className="size-3.5" />
              </button>
            )}
          </div>
          <Select value={channelFilter} onValueChange={setChannelFilter}>
            <SelectTrigger
              className="w-36 rounded-lg bg-background shadow-none transition-colors hover:border-ring/50 data-[size=default]:h-9 dark:bg-input/30 dark:hover:border-ring/50"
              aria-label="按渠道筛选"
            >
              <Globe className="size-3.5 text-muted-foreground" />
              <SelectValue placeholder="渠道" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部渠道</SelectItem>
              {(stats ?? []).map((s) => (
                <SelectItem key={s.channel} value={s.channel}>{channelName(s.channel)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={marketGroupFilter} onValueChange={setMarketGroupFilter}>
            <SelectTrigger
              className="w-32 rounded-lg bg-background shadow-none transition-colors hover:border-ring/50 data-[size=default]:h-9 dark:bg-input/30 dark:hover:border-ring/50"
              aria-label="按市场筛选"
            >
              <MapPin className="size-3.5 text-muted-foreground" />
              <SelectValue placeholder="市场" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部市场</SelectItem>
              {MARKET_GROUPS.map((g) => (
                <SelectItem key={g} value={g}>{g}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {(filterCount > 0) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setChannelFilter('all')
                setMarketGroupFilter('all')
                setSearch('')
              }}
            >
              <X className="size-3.5" /> 清筛选 ({filterCount})
            </Button>
          )}
          {/* HIGH-5 修复：导出按钮从数据 Tab 移除——用户对"导出"是目的性行为，
              独立 Tab 才是它的正确归属（避免两处入口行为重复）。*/}
        </div>
      </div>

      {error ? (
        <PanelError onRetry={onRetry} label="无法加载线索列表" />
      ) : leads === null ? (
        <Skeleton className="h-64 w-full" />
      ) : leads.length === 0 ? (
        <EmptyLeads hasFilter={filterCount > 0} onClear={() => {
          setChannelFilter('all')
          setMarketGroupFilter('all')
          setSearch('')
        }} />
      ) : (
        <>
          <div className="rounded-xl border border-border/70 bg-card overflow-hidden">
            <Table>
              <caption className="sr-only">线索列表，按分数降序排列</caption>
              <TableHeader>
                <TableRow className="hover:bg-transparent border-b border-border/60 bg-muted/30">
                  <TableHead className="text-left font-medium">企业</TableHead>
                  <TableHead className="hidden md:table-cell text-left font-medium">渠道 / 市场</TableHead>
                  <TableHead className="text-right font-medium">分数</TableHead>
                  <TableHead className="text-left font-medium">电话</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody key={page} className="animate-in fade-in duration-150">
                {pageLeads.map((l) => {
                  const phones = l.phones.split(',')
                  const Style = channelStyle(l.channel)
                  const tier = getTier(l)
                  const tierCls = TIER_STYLE[tier]
                  return (
                    <TableRow key={l.entity} className={cn('group', tierCls.bg)}>
                      <TableCell className={cn('border-l-[2px] border-solid py-3 align-top', tierCls.border)}>
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1.5">
                            {/* entity 可点击直达官网：开新标签，rel=noopener 安全 */}
                            <a
                              href={entityUrl(l.entity)}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={`访问 ${l.entity}`}
                              className="group inline-flex items-center gap-1 font-medium text-foreground transition-colors hover:text-primary hover:underline"
                            >
                              {l.entity}
                              <ExternalLink className="size-3 shrink-0 text-muted-foreground/0 transition-colors group-hover:text-primary" />
                            </a>
                          </div>
                          {l.developer_name && (
                            <span className="text-[11px] text-muted-foreground">
                              {l.developer_name}
                            </span>
                          )}
                          {l.widget && (
                            <span className="mt-0.5 inline-flex w-fit items-center rounded bg-muted px-1 py-0 font-mono text-[10px] text-muted-foreground">
                              {l.widget}
                            </span>
                          )}
                        </div>
                      </TableCell>
                      {/* 渠道/市场合并列：均为次级元数据，合并后给企业和电话让出宽度 */}
                      <TableCell className="hidden py-3 md:table-cell align-top">
                        <div className="flex flex-col items-start gap-1">
                          <span className={cn('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs', Style.chip)}>
                            {channelName(l.channel)}
                          </span>
                          {l.market ? (
                            <span className="text-xs text-foreground/80">
                              {marketName(l.market)}
                              {l.market_group && (
                                <span className="ml-1 text-[10px] text-muted-foreground/60">{l.market_group}</span>
                              )}
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground/40">—</span>
                          )}
                        </div>
                      </TableCell>
                      {/* 优先级列：分数 + 高优同列聚合，扫一眼右边即知该不该先跟 */}
                      <TableCell className="py-3 text-right align-top">
                        <div className="flex flex-col items-end gap-1">
                          <ScorePill score={l.score} />
                          {l.p0 ? <P0Badge active /> : null}
                        </div>
                      </TableCell>
                      <TableCell className="py-3 align-top">
                        <div className="flex flex-col gap-1">
                          <button
                            type="button"
                            onClick={() => copyPhones(l.entity, l.phones)}
                            title={copied === l.entity ? '已复制' : `点击复制全部电话：${l.phones}`}
                            aria-label={`复制 ${l.entity} 的电话`}
                            className="group inline-flex max-w-44 items-center gap-1.5 rounded-md px-1.5 py-0.5 font-mono text-xs tabular-nums transition-colors hover:bg-muted md:max-w-none"
                          >
                            {copied === l.entity ? (
                              <Check className="size-3.5 shrink-0 text-primary" aria-hidden />
                            ) : (
                              <Copy className="size-3.5 shrink-0 text-muted-foreground/60" aria-hidden />
                            )}
                            <span className="truncate">{phones[0]}</span>
                            {phones.length > 1 && (
                              <Badge variant="secondary" className="h-4 px-1 text-[10px] tabular-nums">
                                ×{phones.length - 1}
                              </Badge>
                            )}
                          </button>
                          {/* 外联双入口横向并列：WhatsApp + 电话直达，统一绿色系配色 */}
                          <div className="flex items-center gap-0.5">
                            <a
                              href={waUrl(phones[0])}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={`唤起 WhatsApp 与 ${l.entity} 对话`}
                              aria-label={`唤起 WhatsApp 与 ${l.entity}`}
                              className="inline-flex items-center rounded-md p-1 text-[#25D366] transition-colors hover:bg-[#25D366]/10 hover:text-[#1EBE5B]"
                            >
                              <WhatsAppIcon className="size-3.5" />
                            </a>
                            {/* 号码与 WA 同一 E.164，tel: 直接拨打 */}
                            <a
                              href={`tel:${phones[0]}`}
                              title={`拨打 ${l.entity} 的电话`}
                              aria-label={`拨打 ${l.entity} 的电话`}
                              className="inline-flex items-center rounded-md p-1 text-[#25D366] transition-colors hover:bg-[#25D366]/10 hover:text-[#1EBE5B]"
                            >
                              <Phone className="size-3.5" />
                            </a>
                          </div>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span className="tabular-nums">
                第 {page}–{Math.min(page * PAGE_SIZE, filtered.length)} 条 / 共 {filtered.length} 条
              </span>
              <nav aria-label="线索分页">
              <Pagination>
                <PaginationContent>
                  <PaginationItem>
                    <PaginationPrevious
                      href="#"
                      aria-disabled={page === 1}
                      className={page === 1 ? 'pointer-events-none opacity-50' : ''}
                      onClick={(e) => { e.preventDefault(); setPage((p) => Math.max(1, p - 1)) }}
                    />
                  </PaginationItem>
                  {pageWindow(page, totalPages).map((p, i) =>
                    p === '…' ? (
                      <PaginationItem key={`e${i}`}><PaginationEllipsis /></PaginationItem>
                    ) : (
                      <PaginationItem key={p}>
                        <PaginationLink
                          href="#"
                          isActive={p === page}
                          aria-label={`第 ${p} 页`}
                          aria-current={p === page ? 'page' : undefined}
                          onClick={(e) => { e.preventDefault(); setPage(p) }}
                        >
                          {p}
                        </PaginationLink>
                      </PaginationItem>
                    )
                  )}
                  <PaginationItem>
                    <PaginationNext
                      href="#"
                      aria-disabled={page === totalPages}
                      className={page === totalPages ? 'pointer-events-none opacity-50' : ''}
                      onClick={(e) => { e.preventDefault(); setPage((p) => Math.min(totalPages, p + 1)) }}
                    />
                  </PaginationItem>
                </PaginationContent>
              </Pagination>
            </nav>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function EmptyLeads({ hasFilter, onClear }: { hasFilter: boolean; onClear: () => void }) {
  return (
    <div className="flex h-48 flex-col items-center justify-center gap-3 rounded-xl border border-dashed text-sm text-muted-foreground">
      <Database className="size-7 text-muted-foreground/40" />
      {hasFilter ? (
        <>
          <p>当前筛选无匹配线索</p>
          <Button variant="outline" size="sm" onClick={onClear}>
            <X className="size-3.5" /> 清除筛选
          </Button>
        </>
      ) : (
        <>
          <p>暂无线索</p>
          <p className="text-xs">点右上角「智能爬取」，完成后这里就有数据了</p>
        </>
      )}
    </div>
  )
}

/* ============================================================
 * Tab 3：导出
 * ============================================================ */
function ExportPanel({
  stats, leads, channelFilter, marketGroupFilter,
  setChannelFilter, setMarketGroupFilter,
}: {
  stats: Stat[] | null
  leads: Lead[] | null
  channelFilter: string
  marketGroupFilter: string
  setChannelFilter: (v: string) => void
  setMarketGroupFilter: (v: string) => void
}) {
  const [busy, setBusy] = useState(false)

  const handleExport = async () => {
    setBusy(true)
    try {
      await exportCsv({
        channel: channelFilter,
        marketGroup: marketGroupFilter,
      })
    } catch (e) {
      alert(`导出失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const summary = stats ?? []
  /* 只统计真实会进 CSV 的（leads 即有号码线索），不展示与导出无关的"域"总数 */
  const p0Count = useMemo(() => (leads ?? []).filter((l) => l.p0).length, [leads])
  const hasFilter = channelFilter !== 'all' || marketGroupFilter !== 'all'

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-card">
      {/* 工具栏：筛选 + 计数 + 导出操作一行完成（data-table toolbar 模式） */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 bg-muted/30 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={channelFilter} onValueChange={setChannelFilter}>
            <SelectTrigger
              aria-label="按渠道筛选"
              className="w-36 rounded-lg bg-background shadow-none transition-colors hover:border-ring/50 data-[size=default]:h-9 dark:bg-input/30 dark:hover:border-ring/50"
            >
              <Globe className="size-3.5 text-muted-foreground" />
              <SelectValue placeholder="渠道" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部渠道</SelectItem>
              {summary.map((s) => (
                <SelectItem key={s.channel} value={s.channel}>{channelName(s.channel)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={marketGroupFilter} onValueChange={setMarketGroupFilter}>
            <SelectTrigger
              aria-label="按市场分组筛选"
              className="w-36 rounded-lg bg-background shadow-none transition-colors hover:border-ring/50 data-[size=default]:h-9 dark:bg-input/30 dark:hover:border-ring/50"
            >
              <MapPin className="size-3.5 text-muted-foreground" />
              <SelectValue placeholder="市场分组" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部市场</SelectItem>
              {MARKET_GROUPS.map((g) => (
                <SelectItem key={g} value={g}>{g}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {hasFilter && (
            <Button variant="ghost" onClick={() => {
              setChannelFilter('all'); setMarketGroupFilter('all')
            }}>
              <X className="size-4" /> 清筛选
            </Button>
          )}
        </div>
        <div className="flex items-center gap-3">
          {leads !== null && (
            <p className="text-xs tabular-nums text-muted-foreground">
              将导出 <span className="font-semibold text-foreground">{leads.length}{leads.length >= 500 ? '+' : ''}</span> 条 · 高优 {p0Count}
            </p>
          )}
          <Button onClick={handleExport} disabled={busy}>
            {busy ? (<><Loader2 className="size-4 animate-spin" /> 打包中…</>)
                  : (<><Download className="size-4" /> 导出 CSV</>)}
          </Button>
        </div>
      </div>
        {leads === null ? (
          <Skeleton className="h-40 w-full rounded-none" />
        ) : leads.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground">
            当前筛选条件下暂无线索，调整上方筛选或先「智能爬取」获取数据
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="border-b border-border/60 bg-muted/30 hover:bg-transparent">
                <TableHead className="text-left font-medium">企业</TableHead>
                <TableHead className="text-left font-medium">渠道</TableHead>
                <TableHead className="text-left font-medium">市场</TableHead>
                <TableHead className="text-right font-medium">分数</TableHead>
                <TableHead className="text-left font-medium">电话</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {leads.slice(0, 20).map((l) => {
                const Style = channelStyle(l.channel)
                const phones = l.phones.split(',')
                return (
                  <TableRow key={l.entity} className="group">
                    <TableCell className="py-2.5">
                      <div className="flex items-center gap-1.5">
                        <a
                          href={entityUrl(l.entity)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium transition-colors hover:text-primary hover:underline"
                        >
                          {l.entity}
                        </a>
                        {l.p0 ? <P0Badge active /> : null}
                      </div>
                      {l.developer_name && (
                        <p className="text-[11px] text-muted-foreground">{l.developer_name}</p>
                      )}
                    </TableCell>
                    <TableCell className="py-2.5">
                      <span className={cn('inline-flex items-center rounded-md px-1.5 py-0.5 text-xs', Style.chip)}>
                        {channelName(l.channel)}
                      </span>
                    </TableCell>
                    <TableCell className="py-2.5 text-xs text-muted-foreground">
                      {l.market ? marketName(l.market) : '—'}
                    </TableCell>
                    <TableCell className="py-2.5 text-right">
                      <ScorePill score={l.score} />
                    </TableCell>
                    <TableCell className="py-2.5 font-mono text-xs tabular-nums">
                      {phones[0]}
                      {phones.length > 1 && (
                        <span className="ml-1 text-muted-foreground">+{phones.length - 1}</span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
        {leads !== null && leads.length > 20 && (
          <p className="border-t border-border/60 bg-muted/20 px-4 py-2 text-center text-xs text-muted-foreground">
            仅预览前 20 条，导出包含全部 {leads.length}{leads.length >= 500 ? '+' : ''} 条
          </p>
        )}
      </div>
  )
}

/* ============================================================
 * App
 * ============================================================ */
export default function App() {
  const {
    stats, leads, error, loading,
    channel, setChannel, marketGroup, setMarketGroup,
    search, setSearch, refresh,
  } = usePanelData()
  const { jobs, health } = useCrawlStatus()
  const [tab, setTab] = useState<'data' | 'export'>(() => {
    const h = window.location.hash.replace('#', '')
    return h === 'export' ? h : 'data'
  })

  /* URL hash 双向：Tab 切换 → 更新 hash（便于分享 + 后退） */
  useEffect(() => {
    window.history.replaceState(null, '', `#${tab}`)
  }, [tab])

  useEffect(() => {
    const onHash = () => {
      const h = window.location.hash.replace('#', '')
      if (h === 'export' || h === 'data') setTab(h)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const runningCount = jobs.filter((j) => j.status === 'running').length

  const avgRate = useMemo(() => {
    if (!stats?.length) return 0
    const hits = stats.reduce((a, s) => a + s.hits, 0)
    const total = stats.reduce((a, s) => a + s.total, 0)
    return total ? hits / total : 0
  }, [stats])

  /* 智能爬取（一键黑盒）：渠道/模式/种子全部后端决策，这里只发起 + 反馈 */
  const [smartBusy, setSmartBusy] = useState(false)
  const [smartMsg, setSmartMsg] = useState<{ kind: 'ok' | 'warn' | 'error'; text: string } | null>(null)
  useEffect(() => {
    if (!smartMsg || smartMsg.kind === 'error') return
    const t = setTimeout(() => setSmartMsg(null), 6000)
    return () => clearTimeout(t)
  }, [smartMsg])

  const runSmartCrawl = async () => {
    setSmartBusy(true); setSmartMsg(null)
    try {
      const r = await triggerCrawl({ channels: [], mode: 'smart', limit: 1, max_pages: 3 })
      setSmartMsg(r.total > 0
        ? { kind: 'ok', text: '智能爬取已启动：自动挑最优渠道采集，同时后台挖新人群。' }
        : (r.seeded ?? 0) > 0
          ? { kind: 'ok', text: '正在挖新人群（约 1–3 分钟）——种子落地后自动爬取，无需再点。' }
          : { kind: 'ok', text: '本批进行中——完成后自动补种接续，无需重复点击。' })
    } catch (e) {
      setSmartMsg({ kind: 'error', text: (e as Error).message.replace(/^\d+:\s*/, '') })
    } finally {
      setSmartBusy(false)
    }
  }
  /* 跑批状态：started → running → exited/failed。必须从"有 running"切到"全部完成"才算跑完一次，
   * 此时主动 refresh() 拉新数据。 */
  const wasRunning = useRef(false)
  useEffect(() => {
    const hasRunning = jobs.some((j) => j.status === 'running')
    if (hasRunning) {
      wasRunning.current = true
    } else if (wasRunning.current) {
      wasRunning.current = false
      /* 任务完成 — 主动触发数据刷新 */
      refresh()
    }
  }, [jobs, refresh])

  // stats 卡点击 → 切换 Tab + 设筛选
  const onChannelClick = useCallback(
    (ch: string) => {
      setChannel(ch === channel ? 'all' : ch)
    },
    [channel, setChannel]
  )

  return (
    <div className="min-h-screen bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      >
        跳到主内容
      </a>

      {/* 顶栏 */}
      <header className="sticky top-0 z-40 border-b border-border/60 bg-background/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-5 md:px-8 md:h-16">
          <div className="flex min-w-0 items-center gap-3">
            <Logo size={40} className="shrink-0" />
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="default" size="sm"
              onClick={runSmartCrawl}
              disabled={smartBusy}
              aria-label="智能爬取"
              className="relative h-9 gap-1.5 px-3.5"
            >
              {smartBusy || runningCount > 0
                ? <Loader2 className="size-3.5 animate-spin" />
                : <Zap className="size-3.5" />}
              {/* 进行中仍可点：后端幂等合并（200），点了只刷新提示 */}
              <span className="hidden sm:inline">
                {runningCount > 0 ? '爬取中…' : '智能爬取'}
              </span>
              {runningCount > 0 && (
                <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold tabular-nums text-white">
                  {runningCount}
                </span>
              )}
            </Button>
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-7xl space-y-8 px-5 pt-4 pb-6 md:px-8 md:pt-5 md:pb-10">

        {/* 智能爬取反馈条：一键结果 + 运行指示（黑盒——无配置、无日志入口）；无内容时不渲染，避免顶栏下留白 */}
        {(smartMsg || runningCount > 0) && (
          <div className="flex flex-col items-end gap-2">
            <CrawlStatusBar jobs={jobs} />
            {smartMsg && (
              <Alert variant={smartMsg.kind === 'error' ? 'destructive' : 'default'}
                     className="w-full max-w-md py-2">
                <AlertCircle className="size-4" />
                <AlertDescription>{smartMsg.text}</AlertDescription>
              </Alert>
            )}
          </div>
        )}

        {/* Q7/Q13 分级冒头：某渠道连续 3 次补种失败才亮（一次成功即消）——
            黑盒≠静默空转，销售需要知道「为什么点了不涨」 */}
        {Object.keys(health).length > 0 && (
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertDescription>
              {Object.entries(health).map(([ch, n]) => `${ch} 补种连续失败 ${n} 次`).join('；')}
              ——网络或数据源异常，已自动重试；仍失败请联系管理员。
            </AlertDescription>
          </Alert>
        )}

        {/* Tab 导航 */}
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="data" className="gap-1.5">
              <Globe className="size-3.5" /> 所有线索
            </TabsTrigger>
            <TabsTrigger value="export" className="gap-1.5">
              <Download className="size-3.5" /> 导出
            </TabsTrigger>
          </TabsList>

          {/* ===== Tab 1：数据 ===== */}
          <TabsContent value="data" className="space-y-8">
            <section aria-labelledby="stats-heading" className="space-y-3">
              <div className="flex items-baseline justify-between">
                <h2 id="stats-heading" className="text-sm font-medium text-muted-foreground">
                  渠道命中率
                </h2>
                <p className="text-[11px] text-muted-foreground">
                  点击渠道卡 → 下方线索筛选到该渠道
                </p>
              </div>
              <StatsCards
                stats={stats} loading={loading} error={error}
                onRetry={refresh}
                activeChannel={channel}
                onChannelClick={onChannelClick}
                avgRate={avgRate}
              />
            </section>

            <LeadsTable
              leads={leads} loading={loading} error={error}
              onRetry={refresh}
              channel={channel}
              marketGroup={marketGroup}
              channelFilter={channel} setChannelFilter={setChannel}
              marketGroupFilter={marketGroup} setMarketGroupFilter={setMarketGroup}
               search={search} setSearch={setSearch}
               stats={stats}
             />
           </TabsContent>

          {/* ===== Tab 3：导出 ===== */}
          <TabsContent value="export">
            <ExportPanel
              stats={stats}
              leads={leads}
              channelFilter={channel}
              marketGroupFilter={marketGroup}
              setChannelFilter={setChannel}
              setMarketGroupFilter={setMarketGroup}
            />
          </TabsContent>
        </Tabs>
      </main>
    </div>
  )
}