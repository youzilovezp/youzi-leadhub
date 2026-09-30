import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Check,
  Copy,
  Database,
  Download,
  ExternalLink,
  Globe,
  Loader2,
  MessageCircle,
  Moon,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShoppingBag,
  Smartphone,
  Sun,
  TestTube2,
  X,
} from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Logo } from '@/components/Logo'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
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
import { Switch } from '@/components/ui/switch'
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
  status: 'created' | 'running' | 'exited' | 'failed'
  exit_code: number | null
  log: string | null
}

type Theme = 'light' | 'dark' | 'system'

const PAGE_SIZE = 24

/* 渠道视觉身份：克制型 — 单一 emerald 强调 + 中性灰，告别"五色卡片" */
const CHANNEL_IDENTITY: Record<string, { icon: typeof Globe; chip: string; grad: string }> = {
  play:      { icon: Smartphone,  chip: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
                                grad: 'from-emerald-500/10 to-teal-500/10' },
  tranco:    { icon: Globe,       chip: 'bg-muted text-muted-foreground',
                                grad: 'from-muted/30 to-muted/10' },
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

/* 渠道历史命中率 hint（v7 实测口径——给用户预算参考） */
const CRAWL_CHANNELS = ['tranco', 'myshopify', 'play', 'osm'] as const
type CrawlChannel = (typeof CRAWL_CHANNELS)[number]

/* 渠道中文名 + 一句话定位 — 给非开发者用户看的，不是给后端看的 */
const CHANNEL_CN: Record<CrawlChannel, { name: string; desc: string }> = {
  tranco:    { name: '域名榜单',     desc: 'Tranco 全球访问 Top 1M 域名（覆盖长尾）' },
  myshopify: { name: 'Shopify 店铺', desc: 'CDX 索引 *.myshopify.com 子域的店铺' },
  play:      { name: 'Play 应用',    desc: '按国家×类目爬应用及开发者官网（高优级第 1 强信号）' },
  osm:       { name: '地图商户',     desc: 'OpenStreetMap 提商户电话（需海外 VPS）' },
}

/* 拖到模块顶层：避免 CrawlDialog 收到新引用就 useEffect 重置用户已选渠道。
 * （HIGH-2 fix：父组件轮询时 setJobs → App 重渲染 → defaultChannels 新数组 → 子 dialog 重置）*/
const DEFAULT_CRAWL_CHANNELS: CrawlChannel[] = [...CRAWL_CHANNELS]
const CHANNEL_HIT_RATE_HINT: Record<CrawlChannel, string> = {
  play:      '~12–25%',
  tranco:    '~1%',
  myshopify: '~0.7%',
  osm:       '~15–55%',
}

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

function StatusPill({ status, exitCode }: { status: CrawlJob['status']; exitCode: number | null }) {
  if (status === 'running')
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/15 px-2 py-0.5 text-xs text-sky-700 dark:text-sky-300 ring-1 ring-sky-500/30">
        <Loader2 className="size-3 animate-spin" /> 跑批中
      </span>
    )
  if (status === 'exited')
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-500/30">
        <Check className="size-3" /> 完成
      </span>
    )
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/15 px-2 py-0.5 text-xs text-rose-700 dark:text-rose-300 ring-1 ring-rose-500/30">
      <X className="size-3" /> 失败 {exitCode !== null ? `(${exitCode})` : ''}
    </span>
  )
}

/* ============================================================
 * 主题
 * ============================================================ */
function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem('theme') as Theme) || 'system'
  )

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      const dark = theme === 'dark' || (theme === 'system' && mq.matches)
      document.documentElement.classList.toggle('dark', dark)
    }
    apply()
    localStorage.setItem('theme', theme)
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [theme])

  return [theme, setTheme] as const
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
  const [p0Only, setP0Only] = useState(false)
  const [search, setSearch] = useState('')
  const [tick, setTick] = useState(0)

  const refresh = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(false)

    const qs = new URLSearchParams({ limit: '500' })
    if (channel !== 'all') qs.set('channel', channel)
    if (p0Only) qs.set('p0', '1')
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
  }, [channel, p0Only, marketGroup, tick])

  return {
    stats, leads, error, loading, updatedAt,
    channel, setChannel, marketGroup, setMarketGroup, p0Only, setP0Only,
    search, setSearch, refresh,
  }
}

function useCrawlStatus() {
  const [jobs, setJobs] = useState<CrawlJob[]>([])
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
          setJobs(JSON.parse(raw))
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

  return { jobs, bump: () => setTick((t) => t + 1) }
}

/* 种子池：各渠道待爬种子数（挖新人群追加后刷新） */
function useSeedPools() {
  const [pools, setPools] = useState<Record<string, number>>({})
  const refresh = useCallback(() => {
    fetch('/api/seeds')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setPools(d))
      .catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])
  return { pools, refreshPools: refresh }
}

/* ============================================================
 * API 客户端
 * ============================================================ */
async function triggerCrawl(opts: {
  channels: CrawlChannel[]
  mode: 'incremental' | 'full'
  limit: number
  max_pages: number
}) {
  const r = await fetch('/api/crawl', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json() as Promise<{ mode: string; spawned: CrawlJob[]; skipped: unknown[]; total: number }>
}

/* 挖新人群：换国家×类目生成新种子，后端去重追加进渠道种子池（后台任务） */
async function triggerSeeds(opts: {
  channel: 'play' | 'osm'
  countries: string
  categories: string
  limit: number
}) {
  const r = await fetch('/api/seeds', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json() as Promise<{ job: CrawlJob; target: string; hint: string }>
}

async function exportCsv(opts: { channel?: string; p0?: boolean; marketGroup?: string }) {
  const qs = new URLSearchParams()
  if (opts.channel && opts.channel !== 'all') qs.set('channel', opts.channel)
  if (opts.p0) qs.set('p0', '1')
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

/* 跑批状态行 */
function CrawlJobsList({ jobs, onRefresh }: { jobs: CrawlJob[]; onRefresh: () => void }) {
  if (jobs.length === 0) return null
  return (
    <div className="rounded-xl border border-border/70 bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-medium text-muted-foreground">
          跑批状态
        </h3>
        <Button variant="ghost" size="sm" onClick={onRefresh}>
          <RefreshCw className="size-3.5" /> 刷新
        </Button>
      </div>
      <ul className="space-y-1.5">
        {jobs.map((j) => (
          <li key={j.job_id} className="rounded-md bg-muted/40 px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-semibold">{j.channel}</span>
              <StatusPill status={j.status} exitCode={j.exit_code} />
              <span className="text-muted-foreground tabular-nums">PID {j.pid}</span>
              <span className="ml-auto text-muted-foreground tabular-nums">
                {new Date(j.started_at * 1000).toLocaleTimeString('zh-CN')}
              </span>
            </div>
            {j.log && (
              <details className="mt-1">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">日志末尾</summary>
                <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded bg-background/60 p-2 font-mono text-[11px] text-muted-foreground">
                  {j.log}
                </pre>
              </details>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/* HIGH-1 修复：跑批状态精简为 sticky 短条（仅显示有任务时），不再占据大块屏幕。
 * 完整列表搬到跑批 Tab 内（#crawl）。点击短条跳转过去。
 * v2：只在有 RUNNING 任务时才显示——已完成/失败的任务落库后用户不再需要关注，避免噪声。*/
function CrawlStatusBar({ jobs, onJump }: { jobs: CrawlJob[]; onJump: () => void }) {
  const running = jobs.filter((j) => j.status === 'running')
  if (running.length === 0) return null
  const head = running[0]
  const more = running.length - 1
  return (
    <button
      onClick={onJump}
      title="跳到跑批 Tab 查看实时日志"
      className="group inline-flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs transition-colors hover:bg-primary/10"
    >
      <Loader2 className="size-3 animate-spin text-primary" />
      <span className="font-medium">爬批运行中</span>
      <span className="font-mono font-semibold text-foreground/80">{head.channel}</span>
      <span className="text-[10px] tabular-nums text-muted-foreground">PID {head.pid}</span>
      {more > 0 && (
        <span className="rounded-full bg-rose-500 px-1.5 text-[10px] font-semibold text-white">
          +{more}
        </span>
      )}
      <span className="ml-1 text-muted-foreground group-hover:text-foreground">查看 →</span>
    </button>
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
                <p className="text-xs font-medium text-muted-foreground">{s.channel}</p>
              </div>
              <span
                title={`${s.channel} 渠道高优（推荐跟进）线索数 ${s.p0}`}
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
  leads, loading, error, onRetry, channel, marketGroup, p0Only,
  channelFilter, setChannelFilter, marketGroupFilter, setMarketGroupFilter,
  setP0Only, search, setSearch, stats,
}: {
  leads: Lead[] | null
  loading: boolean
  error: boolean
  onRetry: () => void
  channel: string
  marketGroup: string
  p0Only: boolean
  channelFilter: string
  setChannelFilter: (v: string) => void
  marketGroupFilter: string
  setMarketGroupFilter: (v: string) => void
  setP0Only: (v: boolean) => void
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

  useEffect(() => setPage(1), [channel, marketGroup, p0Only, search])

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
                      (p0Only ? 1 : 0) + (search ? 1 : 0)

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
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              placeholder="搜索实体 / 电话 / 开发者"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-56 pl-8"
            />
          </div>
          <Select value={channelFilter} onValueChange={setChannelFilter}>
            <SelectTrigger className="w-32" aria-label="按渠道筛选">
              <SelectValue placeholder="渠道" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部渠道</SelectItem>
              {(stats ?? []).map((s) => (
                <SelectItem key={s.channel} value={s.channel}>{s.channel}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={marketGroupFilter} onValueChange={setMarketGroupFilter}>
            <SelectTrigger className="w-28" aria-label="按市场筛选">
              <SelectValue placeholder="市场" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部市场</SelectItem>
              {MARKET_GROUPS.map((g) => (
                <SelectItem key={g} value={g}>{g}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted">
            <Switch checked={p0Only} onCheckedChange={setP0Only} aria-label="仅看高优" />
            <span className="whitespace-nowrap" title="仅显示推荐跟进的高优线索">仅高优</span>
          </label>
          {(filterCount > 0) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setChannelFilter('all')
                setMarketGroupFilter('all')
                setP0Only(false)
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
          setP0Only(false)
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
                  <TableHead className="hidden md:table-cell text-left font-medium">渠道</TableHead>
                  <TableHead className="hidden md:table-cell text-left font-medium">市场</TableHead>
                  <TableHead className="text-right font-medium whitespace-nowrap">分数 · 高优</TableHead>
                  <TableHead className="text-left font-medium">电话</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody key={page} className="animate-in fade-in duration-150">
                {pageLeads.map((l) => {
                  const phones = l.phones.split(',')
                  const Style = channelStyle(l.channel)
                  return (
                    <TableRow key={l.entity} className="group">
                      <TableCell className="py-3 align-top">
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
                            {l.p0 ? <P0Badge active /> : null}
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
                      <TableCell className="hidden py-3 md:table-cell align-top">
                        <span className={cn('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs', Style.chip)}>
                          {l.channel}
                        </span>
                      </TableCell>
                      <TableCell className="hidden py-3 align-top text-xs text-muted-foreground md:table-cell">
                        {l.market ? (
                          <span className="flex flex-col gap-0.5">
                            <span className="text-foreground/80">{marketName(l.market)}</span>
                            {l.market_group && (
                              <span className="text-[10px] text-muted-foreground/60">{l.market_group}</span>
                            )}
                          </span>
                        ) : (
                          <span className="text-muted-foreground/40">—</span>
                        )}
                      </TableCell>
                      <TableCell className="py-3 text-right align-top">
                        <ScorePill score={l.score} />
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
                          {/* 点击直达 WhatsApp：销售外联主入口，电话一键唤起 */}
                          <a
                            href={waUrl(phones[0])}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={`唤起 WhatsApp 与 ${l.entity} 对话`}
                            aria-label={`唤起 WhatsApp 与 ${l.entity}`}
                            className="inline-flex w-fit items-center gap-1 rounded px-1 py-0 text-[11px] text-emerald-700 transition-colors hover:bg-emerald-500/10 dark:text-emerald-400"
                          >
                            <MessageCircle className="size-3" />
                            <span>WA</span>
                          </a>
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
          <p className="text-xs">先跑 seed + crawl（README 步骤 4），完成后点首页"刷新"</p>
        </>
      )}
    </div>
  )
}

/* ============================================================
 * Tab 2：跑一次采集 —— 任务控制台（重构 2026-09-29）
 *
 * 设计判断：
 * 1. 「挖新人群」是前置的异步任务，与「跑这次采集」生命周期不同 → 拆成独立弹窗
 * 2. ①种子 → ②模式 → ③渠道 是真实的决策顺序 → 编号即结构
 * 3. 发射后弹窗切进度态，不玩"提交即失联"
 * 4. 术语去工程化：JOBDIR/dupefilter 不出现在操作者面前
 * ============================================================ */

/* 管道状态条（签名元素）：种子池 → 本次采集 → 线索库，实时数字连通成一条管道 */
function PipelineStrip({ pools, planned, leads, running, runNote }: {
  pools: Record<string, number>
  planned: number
  leads: number
  running: boolean
  runNote: string
}) {
  const poolTotal = Object.values(pools).reduce((a, b) => a + b, 0)
  const nodes = [
    { key: 'seeds', label: '种子池', value: poolTotal, sub: '待爬站点的来源池',
      icon: Database },
    { key: 'run', label: '本次采集', value: planned, sub: running ? '采集中…' : runNote,
      icon: Play, active: true },
    { key: 'leads', label: '线索库', value: leads, sub: '已入库带号线索', icon: Check },
  ]
  return (
    <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr] items-stretch gap-1" role="img"
         aria-label={`种子池 ${poolTotal}，本次 ${planned} 站，线索库 ${leads}`}>
      {nodes.map((n, i) => {
        const Icon = n.icon
        return (
          <Fragment key={n.key}>
            {i > 0 && (
              <div className="flex w-5 items-center justify-center"
                   aria-hidden="true">
                <span className={cn('h-px w-full bg-muted-foreground/40',
                                    running && n.key === 'leads'
                                      && 'animate-pulse motion-reduce:animate-none')} />
              </div>
            )}
            <div className={cn(
                   'rounded-lg border px-2.5 py-2 text-center',
                   n.active ? 'border-primary/50 bg-primary/5' : 'border-border/60 bg-muted/30'
                 )}>
              <div className="flex items-center justify-center gap-1 text-[10px] text-muted-foreground">
                <Icon className="size-3" aria-hidden="true" />
                {n.label}
              </div>
              <div className={cn('mt-0.5 text-lg font-semibold tabular-nums leading-none',
                                 n.active && 'text-primary',
                                 running && n.key === 'run' && 'animate-pulse motion-reduce:animate-none')}>
                {n.value.toLocaleString()}
              </div>
              <div className="mt-0.5 text-[9px] leading-none text-muted-foreground/70">{n.sub}</div>
            </div>
          </Fragment>
        )
      })}
    </div>
  )
}

/* 挖新人群：换国家×类目扩种子池（独立弹窗——异步分钟级任务不该塞进采集表单） */
function SeedDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const [seedCh, setSeedCh] = useState<'play' | 'osm'>('play')
  const [countries, setCountries] = useState('br,mx,th')
  const [categories, setCategories] = useState('BUSINESS')
  const [seedLimit, setSeedLimit] = useState('800')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (open) { setMsg(null); setDone(false) }
  }, [open])

  const submit = async () => {
    const n = Math.round(Number(seedLimit) || 0)
    if (n < 1) { setMsg('目标数量必须 ≥ 1'); return }
    setBusy(true); setMsg(null)
    try {
      const res = await triggerSeeds({ channel: seedCh, countries: countries.trim(),
                                       categories: categories.trim(), limit: n })
      setDone(true)
      setMsg(`已提交：正在挖 ${countries.trim() || '默认国家'} 的${seedCh === 'play' ? '应用榜官网' : '地图商户'}，追加进种子池（后台约 1–3 分钟）。完成后再回来「开始采集·只爬新增」。`)
      void res
    } catch (e) {
      setMsg(`失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>挖新人群</DialogTitle>
          <DialogDescription>
            换一批国家（或类目）生成新种子，追加进种子池。已有的不会重复。
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-2">
          {([
            ['play', 'Play 应用榜', '国家×类目分榜 → 开发者官网（命中率 ~12–25%）'],
            ['osm', 'OSM 地图商户', '按国家挖带官网的本地商家（命中率 ~55%）'],
          ] as const).map(([value, label, desc]) => (
            <label key={value}
                   className={cn('flex cursor-pointer gap-2 rounded-lg border p-3 transition-colors',
                                 seedCh === value ? 'border-primary/50 bg-primary/5'
                                                   : 'border-border/60 hover:border-muted-foreground/40')}>
              <input type="radio" name="seed-channel" checked={seedCh === value}
                     onChange={() => setSeedCh(value)}
                     className="mt-0.5 size-4 accent-primary" aria-label={label} />
              <div>
                <div className="text-sm font-medium">{label}</div>
                <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{desc}</p>
              </div>
            </label>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="seed-countries">国家码</Label>
            <Input id="seed-countries" value={countries}
                   onChange={(e) => setCountries(e.target.value)}
                   placeholder={seedCh === 'osm' ? 'SG,VN,PH' : 'br,mx,th'} />
            <p className="text-[10px] text-muted-foreground">ISO 两位码，逗号分隔</p>
          </div>
          {seedCh === 'play' ? (
            <div className="space-y-1.5">
              <Label htmlFor="seed-category">类目</Label>
              <Input id="seed-category" value={categories}
                     onChange={(e) => setCategories(e.target.value)} />
              <p className="text-[10px] text-muted-foreground">
                BUSINESS / SHOPPING / COMMUNICATION / FINANCE / FOOD_AND_DRINK
              </p>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="seed-limit">目标数量</Label>
              <Input id="seed-limit" type="number" min={1} max={5000} value={seedLimit}
                     onChange={(e) => setSeedLimit(e.target.value)} />
            </div>
          )}
        </div>
        {seedCh === 'play' && (
          <div className="space-y-1.5">
            <Label htmlFor="seed-limit-play">目标数量</Label>
            <Input id="seed-limit-play" type="number" min={1} max={5000} value={seedLimit}
                   onChange={(e) => setSeedLimit(e.target.value)} />
            <p className="text-[10px] text-muted-foreground">按国家均摊；生成走本地代理（7890）</p>
          </div>
        )}

        {msg && (
          <p className={cn('rounded-md px-2.5 py-2 text-xs leading-relaxed',
                           done ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground')}>
            {msg}
          </p>
        )}

        <DialogFooter>
          <Button onClick={done ? () => onOpenChange(false) : submit}
                  disabled={busy || done}
                  aria-label={done ? '关闭挖新人群' : '开始生成种子'}>
            {busy ? (<><Loader2 className="size-4 animate-spin" /> 生成中…</>)
                  : done ? '好，知道了'
                         : (<><Plus className="size-4" /> 开始生成种子</>)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* 跑一次采集：任务控制台 */
function CrawlDialog({
  open, onOpenChange, defaultChannels, jobs, pools, leadsTotal,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  defaultChannels: CrawlChannel[]
  jobs: CrawlJob[]
  pools: Record<string, number>
  leadsTotal: number
}) {
  const [mode, setMode] = useState<'incremental' | 'full'>('incremental')
  const [selected, setSelected] = useState<Set<CrawlChannel>>(new Set(defaultChannels))
  /* 数字输入用字符串状态：清空即 0 的受控 number 框会让每次输入都以"0"开头 */
  const [maxPages, setMaxPages] = useState('3')
  const [fullLimit, setFullLimit] = useState('500')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [phase, setPhase] = useState<'configure' | 'launched'>('configure')
  const [launchedChannels, setLaunchedChannels] = useState<Set<CrawlChannel>>(new Set())

  useEffect(() => {
    if (open) {
      setSelected(new Set(defaultChannels))
      setMode('incremental')
      setMaxPages('3')
      setError(null)
      setPhase('configure')
    }
  }, [open, defaultChannels])

  const toggle = (ch: CrawlChannel) => {
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(ch)) n.delete(ch)
      else n.add(ch)
      return n
    })
  }

  /* 运行中感知：同渠道任务在跑时按钮让位，不再让用户撞 409 */
  const busyChannels = new Set(
    jobs.filter((j) => j.status === 'running' && !j.job_id.startsWith('seed-'))
        .map((j) => j.channel as CrawlChannel))
  const launchable = [...selected].filter((ch) => !busyChannels.has(ch))

  const plannedSeeds = launchable.reduce((a, ch) => a + (pools[ch] ?? 0), 0)
  const nPages = Math.round(Number(maxPages) || 0)

  const submit = async () => {
    if (launchable.length === 0) { setError('所选渠道都在跑批中——等它结束，或换一个渠道'); return }
    if (nPages < 1) { setError('每站页数必须 ≥ 1'); return }
    setBusy(true); setError(null)
    try {
      await triggerCrawl({
        channels: launchable, mode, max_pages: nPages,
        limit: mode === 'full' ? Math.max(1, Math.round(Number(fullLimit) || 0)) : plannedSeeds,
      })
      setLaunchedChannels(new Set(launchable))
      setPhase('launched')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  /* 进度态：盯本次发射的渠道；任务从 jobs（App 每 2s 轮询）实时来 */
  const myJobs = jobs.filter((j) => launchedChannels.has(j.channel as CrawlChannel)
                              && !j.job_id.startsWith('seed-'))
  const allDone = myJobs.length > 0 && myJobs.every((j) => j.status === 'exited' || j.status === 'failed')
  const anyRunning = myJobs.some((j) => j.status === 'running')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>

        {phase === 'launched' ? (
          /* ---------- 进度态：发射后弹窗不玩失踪 ---------- */
          <div className="space-y-4">
            <DialogHeader>
              <DialogTitle>采集中</DialogTitle>
              <DialogDescription>
                {allDone
                  ? (myJobs.every((j) => j.status === 'exited')
                      ? '全部完成，数据已自动刷新。'
                      : '部分渠道失败——详情看跑批页日志。')
                  : '礼貌爬取需要时间，可以留在原地等，也可以放后台。'}
              </DialogDescription>
            </DialogHeader>
            <ul className="space-y-1.5">
              {myJobs.map((j) => {
                const cnInfo = CHANNEL_CN[j.channel as CrawlChannel]
                return (
                  <li key={j.job_id}
                      className="flex items-center gap-2.5 rounded-lg border border-border/60 px-3 py-2.5">
                    {j.status === 'running'
                      ? <Loader2 className="size-4 shrink-0 animate-spin text-primary" />
                      : j.status === 'exited'
                        ? <Check className="size-4 shrink-0 text-primary" />
                        : <AlertCircle className="size-4 shrink-0 text-rose-500" />}
                    <span className="text-sm font-medium">{cnInfo?.name ?? j.channel}</span>
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                      {j.status === 'running' ? '爬取中…'
                        : j.status === 'exited' ? '完成'
                        : `失败（exit ${j.exit_code}）`}
                    </span>
                  </li>
                )
              })}
            </ul>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant={allDone ? 'default' : 'outline'}>
                  {allDone ? '完成' : '放后台运行'}
                </Button>
              </DialogClose>
            </DialogFooter>
          </div>
        ) : (
          /* ---------- 配置态：① 种子 → ② 模式 → ③ 渠道（真实决策顺序） ---------- */
          <>
        <DialogHeader>
          <DialogTitle>跑一次采集</DialogTitle>
          <DialogDescription>
            种子池里有站没爬过就进线索库；都在池子里 → 先挖新人群。
          </DialogDescription>
        </DialogHeader>

        <PipelineStrip pools={pools} planned={plannedSeeds} leads={leadsTotal}
                       running={busyChannels.size > 0}
                       runNote={mode === 'incremental' ? '池中新站（旧站自动跳过）' : '全部重爬'} />

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-muted-foreground">
              <span className="mr-1.5 text-primary">①</span>种子池
            </h3>
            <span className="text-[10px] tabular-nums text-muted-foreground">
              {Object.entries(pools).filter(([, n]) => n > 0)
                .map(([c, n]) => `${CHANNEL_CN[c as CrawlChannel]?.name ?? c} ${n}`).join(' · ') || '空'}
            </span>
          </div>
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('open-seed-dialog'))}
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border/70 px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
            aria-label="打开挖新人群"
          >
            <Plus className="size-4" aria-hidden="true" />
            挖新人群——换国家×类目，给种子池补货
            <span className="ml-auto text-[10px]">1–3 分钟 · 后台</span>
          </button>
        </section>

        <section className="space-y-2">
          <h3 className="text-xs font-semibold text-muted-foreground">
            <span className="mr-1.5 text-primary">②</span>模式
          </h3>
          <div className="grid grid-cols-2 gap-2 rounded-lg bg-muted/50 p-1" role="radiogroup" aria-label="采集模式">
            {([
              ['incremental', '只爬新增', '跳过爬过的站，只吃新种子。快，日常用这个。'],
              ['full', '全部重爬', '从头爬一遍，用于校准和对比变化。慢。'],
            ] as const).map(([value, label, desc]) => (
              <button
                key={value}
                role="radio" aria-checked={mode === value}
                onClick={() => setMode(value)}
                className={cn(
                  'flex items-start gap-2 rounded-md px-3 py-2 text-left transition-colors',
                  mode === value ? 'bg-background shadow-sm' : 'hover:bg-background/50'
                )}
              >
                <span className={cn('mt-1 size-2 shrink-0 rounded-full border',
                                    mode === value
                                      ? 'border-primary bg-primary'
                                      : 'border-muted-foreground/50')}
                      aria-hidden="true" />
                <span>
                  <span className={cn('block text-sm font-medium',
                                      mode === value && 'text-primary')}>{label}</span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{desc}</span>
                </span>
              </button>
            ))}
          </div>
          {mode === 'full' && (
            <div className="flex items-center gap-2 pl-1">
              <Label htmlFor="full-limit" className="text-[11px] text-muted-foreground">
                每渠道重爬上限
              </Label>
              <Input id="full-limit" type="number" min={1} max={2000} value={fullLimit}
                     onChange={(e) => setFullLimit(e.target.value)}
                     className="h-7 w-24 text-xs" />
              <span className="text-[10px] text-muted-foreground">留空按整池</span>
            </div>
          )}
        </section>

        <section className="space-y-2">
          <h3 className="text-xs font-semibold text-muted-foreground">
            <span className="mr-1.5 text-primary">③</span>渠道
          </h3>
          <div className="grid grid-cols-2 gap-1.5" role="group" aria-label="采集渠道多选">
            {CRAWL_CHANNELS.map((ch) => {
              const checked = selected.has(ch)
              const running = busyChannels.has(ch)
              const Icon = channelStyle(ch).icon
              const cnInfo = CHANNEL_CN[ch]
              return (
                <button
                  key={ch}
                  role="checkbox" aria-checked={checked}
                  onClick={() => toggle(ch)}
                  className={cn(
                    'flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors',
                    checked ? 'border-primary/50 bg-primary/5' : 'border-border/60 hover:border-muted-foreground/40',
                    running && 'opacity-60'
                  )}
                >
                  <span className={cn('flex size-4 shrink-0 items-center justify-center rounded border',
                                      checked ? 'border-primary bg-primary text-primary-foreground'
                                              : 'border-muted-foreground/40')}
                        aria-hidden="true">
                    {checked && <Check className="size-3" />}
                  </span>
                  <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium leading-tight">
                      {cnInfo.name}
                    </span>
                    <span className="block text-[11px] tabular-nums leading-tight text-muted-foreground">
                      {running ? '跑批中…' : `池 ${pools[ch] ?? 0} · 命中 ${CHANNEL_HIT_RATE_HINT[ch]}`}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        </section>

        <section className="space-y-1.5">
          <div className="flex items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor="crawl-pages">每站页数</Label>
              <Input id="crawl-pages" type="number" min={1} max={10} value={maxPages}
                     onChange={(e) => setMaxPages(e.target.value)}
                     className="h-8 w-20 text-sm" />
            </div>
            <p className="flex-1 pb-1.5 text-[11px] leading-snug text-muted-foreground">
              一个站最多下钻几页（首页 → 联系页）；建议 3–5，礼貌上限 5。
            </p>
          </div>
        </section>

        {error && (
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertDescription className="font-mono text-xs">{error}</AlertDescription>
          </Alert>
        )}

        <DialogFooter className="items-center gap-3">
          <p className="flex-1 text-[11px] leading-snug text-muted-foreground" aria-live="polite">
            {launchable.length === 0
              ? '所选渠道都在跑批中——等它结束或换渠道'
              : `${launchable.length} 个渠道 · ${plannedSeeds.toLocaleString()} 站 · 每站 ≤${nPages} 页`}
          </p>
          <DialogClose asChild>
            <Button variant="ghost" disabled={busy}>取消</Button>
          </DialogClose>
          <Button onClick={submit} disabled={busy || launchable.length === 0}
                  aria-label="开始采集">
            {busy ? (<><Loader2 className="size-4 animate-spin" /> 启动中…</>)
                   : (<><Play className="size-4" /> 开始采集</>)}
          </Button>
        </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

/* ============================================================
 * Tab 3：导出
 * ============================================================ */
function ExportPanel({
  stats, channelFilter, marketGroupFilter, p0Only,
  setChannelFilter, setMarketGroupFilter, setP0Only,
}: {
  stats: Stat[] | null
  channelFilter: string
  marketGroupFilter: string
  p0Only: boolean
  setChannelFilter: (v: string) => void
  setMarketGroupFilter: (v: string) => void
  setP0Only: (v: boolean) => void
}) {
  const [busy, setBusy] = useState(false)

  const handleExport = async () => {
    setBusy(true)
    try {
      await exportCsv({
        channel: channelFilter,
        p0: p0Only,
        marketGroup: marketGroupFilter,
      })
    } catch (e) {
      alert(`导出失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const summary = stats ?? []
  const totalDomains = summary.reduce((a, s) => a + s.total, 0)
  const totalHits = summary.reduce((a, s) => a + s.hits, 0)
  const totalP0 = summary.reduce((a, s) => a + s.p0, 0)

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border/70 bg-card p-5">
        <div className="flex items-start gap-4">
          <div className="rounded-lg bg-primary/10 p-2.5">
            <Download className="size-5 text-primary" />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-semibold">线索导出（CSV）</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              按当前筛选条件导出 ≤ 2000 条线索，UTF-8 with BOM（Excel 中文兼容）。
              CSV 列：entity, channel, market, market_group, lang, p0, score,
              developer_name, widget, phones
            </p>
            <div className="mt-4 grid grid-cols-3 gap-2 text-sm">
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">域</p>
                <p className="mt-1 text-lg font-semibold tabular-nums">{totalDomains.toLocaleString()}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">带号码线索</p>
                <p className="mt-1 text-lg font-semibold tabular-nums">{totalHits.toLocaleString()}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground" title="高优 = 中国出海推荐跟进">高优</p>
                <p className="mt-1 text-lg font-semibold tabular-nums text-primary">{totalP0.toLocaleString()}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border/70 bg-card p-5">
        <h4 className="text-sm font-medium">筛选条件</h4>
        <p className="mt-1 text-xs text-muted-foreground">选择范围后导出，仅导出该范围的线索</p>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="space-y-1.5">
            <Label>渠道</Label>
            <Select value={channelFilter} onValueChange={setChannelFilter}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部渠道</SelectItem>
                {summary.map((s) => (
                  <SelectItem key={s.channel} value={s.channel}>{s.channel}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>市场分组</Label>
            <Select value={marketGroupFilter} onValueChange={setMarketGroupFilter}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部市场</SelectItem>
                {MARKET_GROUPS.map((g) => (
                  <SelectItem key={g} value={g}>{g}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end">
            <label className="flex w-full cursor-pointer items-center justify-between rounded-md border border-border/60 p-3 text-sm hover:bg-muted/40">
              <span>仅高优（推荐跟进）</span>
              <Switch checked={p0Only} onCheckedChange={setP0Only} />
            </label>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-3">
        <Button variant="outline" onClick={() => {
          setChannelFilter('all'); setMarketGroupFilter('all'); setP0Only(false)
        }}>
          <X className="size-4" /> 重置
        </Button>
        <Button onClick={handleExport} disabled={busy} size="lg">
          {busy ? (<><Loader2 className="size-4 animate-spin" /> 打包中…</>)
                 : (<><Download className="size-4" /> 导出 CSV</>)}
        </Button>
      </div>
    </div>
  )
}

/* ============================================================
 * App
 * ============================================================ */
export default function App() {
  const [theme, setTheme] = useTheme()
  const {
    stats, leads, error, loading, updatedAt,
    channel, setChannel, marketGroup, setMarketGroup, p0Only, setP0Only,
    search, setSearch, refresh,
  } = usePanelData()
  const { jobs, bump: bumpCrawl } = useCrawlStatus()
  const { pools, refreshPools } = useSeedPools()
  const [crawlDialogOpen, setCrawlDialogOpen] = useState(false)
  const [seedDialogOpen, setSeedDialogOpen] = useState(false)

  /* 控制台「① 挖新人群」入口 → 独立弹窗（事件桥接，保持组件无 prop 钻透） */
  useEffect(() => {
    const open = () => setSeedDialogOpen(true)
    window.addEventListener('open-seed-dialog', open)
    return () => window.removeEventListener('open-seed-dialog', open)
  }, [])
  /* 种子任务结束 → 池子立刻反映追加结果 */
  useEffect(() => {
    const seedRunning = jobs.some((j) => j.job_id.startsWith('seed-') && j.status === 'running')
    if (!seedRunning) refreshPools()
  }, [jobs, refreshPools])
  const [tab, setTab] = useState<'data' | 'crawl' | 'export'>(() => {
    const h = window.location.hash.replace('#', '')
    return h === 'crawl' || h === 'export' ? h : 'data'
  })

  /* 本次新增：用户点"触发爬取"时快照当前线索总数；任务结束且总数增长时算 delta */
  const hitsAtCrawlStart = useRef<number | null>(null)
  const [lastCrawlDelta, setLastCrawlDelta] = useState<number | null>(null)

  /* URL hash 双向：Tab 切换 → 更新 hash（便于分享 + 后退） */
  useEffect(() => {
    window.history.replaceState(null, '', `#${tab}`)
  }, [tab])

  useEffect(() => {
    const onHash = () => {
      const h = window.location.hash.replace('#', '')
      if (h === 'crawl' || h === 'export' || h === 'data') setTab(h)
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

  const totalDomains = stats?.reduce((a, s) => a + s.total, 0) ?? 0
  const totalHits = stats?.reduce((a, s) => a + s.hits, 0) ?? 0
  const totalP0 = stats?.reduce((a, s) => a + s.p0, 0) ?? 0

  const openCrawlDialog = () => {
    hitsAtCrawlStart.current = totalHits
    setCrawlDialogOpen(true)
  }
  /* 跑批状态：started → running → exited/failed。必须从"有 running"切到"全部完成"才算跑完一次。
   * 此时主动 refresh() 拉新数据，否则 totalHits 一直停在旧值，本此新增算不出来。 */
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
  useEffect(() => {
    if (hitsAtCrawlStart.current === null) return
    const stillRunning = jobs.some((j) => j.status === 'running')
    if (stillRunning) return
    /* 任务结束且数据已刷新 — 算 delta */
    if (totalHits > hitsAtCrawlStart.current) {
      setLastCrawlDelta(totalHits - hitsAtCrawlStart.current)
    } else {
      /* 增量跑完未发现新数据 — 0 让用户知道任务确实完成了 */
      setLastCrawlDelta(0)
    }
    hitsAtCrawlStart.current = null
  }, [jobs, totalHits])

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
            <div className="min-w-0">
              <h1 className="flex items-baseline gap-2 truncate">
                <span className="text-[15px] font-semibold tracking-tight md:text-base">youzi-LeadHub</span>
              </h1>
              <p className="hidden items-center gap-2 truncate text-[11px] text-muted-foreground md:flex md:text-xs">
                {updatedAt && !error ? (
                  <>
                    <span>更新于 {updatedAt}</span>
                    <span className="text-border">·</span>
                    <span title="数据库累计域数（不是本次跑批新增）">总域 {totalDomains.toLocaleString()}</span>
                    <span className="text-border">·</span>
                    <span title="数据库累计线索数（不是本次跑批新增）">总线索 {totalHits.toLocaleString()}</span>
                    <span className="text-border">·</span>
                    <span title="高优 = 推荐跟进：中国出海线索">高优 {totalP0}</span>
                    {lastCrawlDelta !== null && (
                      <span
                        className={cn(
                          'ml-1 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
                          lastCrawlDelta > 0
                            ? 'bg-primary/10 text-primary'
                            : 'bg-muted text-muted-foreground'
                        )}
                        title="上一次爬批相对开始前的增量（0 = 增量模式已完成且未发现新线索）"
                      >
                        {lastCrawlDelta > 0 ? `本次新增 +${lastCrawlDelta}` : '本次 +0'}
                      </span>
                    )}
                  </>
                ) : (
                  <span>WhatsApp BSP 线索获取 · 只发现不外联（合规红线）</span>
                )}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline" size="icon"
              onClick={refresh} disabled={loading}
              aria-label="刷新数据"
              className="size-9"
            >
              <RefreshCw className={cn('size-3.5', loading && 'animate-spin')} />
            </Button>
            <Button
              variant="default" size="sm"
              onClick={openCrawlDialog}
              aria-label="触发爬取"
              className="relative h-9 gap-1.5 px-3.5"
            >
              <Play className="size-3.5" />
              <span className="hidden sm:inline">触发爬取</span>
              {runningCount > 0 && (
                <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold tabular-nums text-white">
                  {runningCount}
                </span>
              )}
            </Button>
             <CrawlDialog
              open={crawlDialogOpen}
              onOpenChange={(v) => { setCrawlDialogOpen(v); if (!v) bumpCrawl() }}
              defaultChannels={DEFAULT_CRAWL_CHANNELS}
              jobs={jobs}
              pools={pools}
              leadsTotal={totalHits}
            />
            <SeedDialog
              open={seedDialogOpen}
              onOpenChange={(v) => { setSeedDialogOpen(v); if (!v) refreshPools() }}
            />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon" aria-label="切换主题" className="size-9">
                  {theme === 'dark' ? <Moon className="size-3.5" /> : <Sun className="size-3.5" />}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {([
                  ['light', '浅色'],
                  ['dark', '深色'],
                  ['system', '跟随系统'],
                ] as [Theme, string][]).map(([value, label]) => (
                  <DropdownMenuItem key={value} onClick={() => setTheme(value)}>
                    <Check className={cn('size-4', theme !== value && 'opacity-0')} />
                    {label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-7xl space-y-8 px-5 py-6 md:px-8 md:py-10">

        {/* HIGH-1 修复：跑批状态从"全量列表"瘦身成"短条"，详细列表仅在跑批 Tab 展示。
           防止进入数据 Tab 第一屏就被 9 条历史记录挡住大半视野。*/}
        <div className="flex justify-end">
          <CrawlStatusBar jobs={jobs} onJump={() => setTab('crawl')} />
        </div>

        {/* Tab 导航 */}
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="data" className="gap-1.5">
              <Globe className="size-3.5" /> 数据
            </TabsTrigger>
            <TabsTrigger value="crawl" className="gap-1.5">
              <Play className="size-3.5" /> 跑批
              {runningCount > 0 && (
                <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold tabular-nums text-white">
                  {runningCount}
                </span>
              )}
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
              p0Only={p0Only}
              channelFilter={channel} setChannelFilter={setChannel}
              marketGroupFilter={marketGroup} setMarketGroupFilter={setMarketGroup}
              setP0Only={setP0Only}
              search={search} setSearch={setSearch}
              stats={stats}
            />
          </TabsContent>

          {/* ===== Tab 2：跑批 ===== */}
          <TabsContent value="crawl" className="space-y-8">
            <section className="rounded-xl border border-border/70 bg-card p-5">
              <div className="flex items-start gap-4">
                <div className="rounded-lg bg-primary/10 p-2.5">
                  <Play className="size-5 text-primary" />
                </div>
                <div className="flex-1">
                  <h3 className="text-sm font-semibold">手动触发爬取</h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    从顶栏"触发爬取"按钮或下方入口打开模态框。三步选择：模式 → 渠道 → 数量。
                  </p>
                  <Button onClick={() => setCrawlDialogOpen(true)} className="mt-3 h-9 gap-1.5">
                    <Play className="size-4" /> 打开爬取对话框
                  </Button>
                </div>
              </div>
            </section>

            <section className="space-y-3">
              <h3 className="text-sm font-medium text-muted-foreground">
                跑批历史
              </h3>
              {jobs.length === 0 ? (
                <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                  <Settings2 className="mx-auto size-7 text-muted-foreground/30" />
                  <p className="mt-2">暂无跑批记录</p>
                  <p className="mt-1 text-xs">点击上方"打开爬取对话框"启动第一次跑批</p>
                </div>
              ) : (
                <CrawlJobsList jobs={jobs} onRefresh={bumpCrawl} />
              )}
            </section>
          </TabsContent>

          {/* ===== Tab 3：导出 ===== */}
          <TabsContent value="export">
            <ExportPanel
              stats={stats}
              channelFilter={channel}
              marketGroupFilter={marketGroup}
              p0Only={p0Only}
              setChannelFilter={setChannel}
              setMarketGroupFilter={setMarketGroup}
              setP0Only={setP0Only}
            />
          </TabsContent>
        </Tabs>
      </main>
    </div>
  )
}