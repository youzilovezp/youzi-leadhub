import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Check,
  Moon,
  RefreshCw,
  Sun,
} from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

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
  lang: string | null
  p0: number
  score: number
  phones: string
}

type Theme = 'light' | 'dark' | 'system'

const PAGE_SIZE = 20

/* ---- 主题（Q9：三态切换，localStorage 持久化） ---- */
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

/* ---- 数据加载（Q7：错误态与空态分离；Q14：手动刷新；Q15：limit=500） ---- */
function usePanelData() {
  const [stats, setStats] = useState<Stat[] | null>(null)
  const [leads, setLeads] = useState<Lead[] | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [channel, setChannel] = useState('all')
  const [p0Only, setP0Only] = useState(false)
  const [tick, setTick] = useState(0)

  const refresh = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(false)

    const qs = new URLSearchParams({ limit: '500' })
    if (channel !== 'all') qs.set('channel', channel)
    if (p0Only) qs.set('p0', '1')

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
      })
      .catch(() => alive && setError(true))
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [channel, p0Only, tick])

  return {
    stats, leads, error, loading,
    channel, setChannel, p0Only, setP0Only, refresh,
  }
}

/* ---- 分页页码窗口（Q18：页码组 + 省略号） ---- */
function pageWindow(current: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const out: (number | '…')[] = [1]
  const lo = Math.max(2, current - 1)
  const hi = Math.min(total - 1, current + 1)
  if (lo > 2) out.push('…')
  for (let i = lo; i <= hi; i++) out.push(i)
  if (hi < total - 1) out.push('…')
  out.push(total)
  return out
}

export default function App() {
  const [theme, setTheme] = useTheme()
  const {
    stats, leads, error, loading,
    channel, setChannel, p0Only, setP0Only, refresh,
  } = usePanelData()
  const [page, setPage] = useState(1)

  // 筛选变化回到第一页（Q12/Q17）
  useEffect(() => setPage(1), [channel, p0Only])

  const totalPages = Math.max(1, Math.ceil((leads?.length ?? 0) / PAGE_SIZE))
  const pageLeads = useMemo(
    () => leads?.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE) ?? [],
    [leads, page]
  )

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      >
        跳到主内容
      </a>

      {/* 顶栏（Q10）：sticky，操作右置 */}
      <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 md:px-8">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold md:text-lg">
              youzi-bsp 线索面板
            </h1>
            <p className="hidden truncate text-xs text-muted-foreground sm:block">
              WhatsApp BSP 线索获取 · 只发现不外联（合规红线）
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              onClick={refresh}
              disabled={loading}
              aria-label="刷新数据"
            >
              <RefreshCw className={cn('size-4', loading && 'animate-spin')} />
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon" aria-label="切换主题">
                  {theme === 'dark' ? (
                    <Moon className="size-4" />
                  ) : (
                    <Sun className="size-4" />
                  )}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {(
                  [
                    ['light', '浅色'],
                    ['dark', '深色'],
                    ['system', '跟随系统'],
                  ] as [Theme, string][]
                ).map(([value, label]) => (
                  <DropdownMenuItem key={value} onClick={() => setTheme(value)}>
                    <Check
                      className={cn(
                        'size-4',
                        theme !== value && 'opacity-0'
                      )}
                    />
                    {label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-6xl space-y-10 px-4 py-8 md:px-8">
        {/* 渠道命中率（Q11：大数字 + h-1 细进度条） */}
        <section aria-labelledby="stats-heading" className="space-y-4">
          <h2 id="stats-heading" className="text-sm font-medium text-muted-foreground">
            渠道命中率
          </h2>
          {error ? (
            <PanelError onRetry={refresh} label="无法加载统计数据" />
          ) : stats === null ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-28 w-full" />
              ))}
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {stats.map((s) => (
                <div key={s.channel} className="rounded-lg border bg-card p-5">
                  <p className="text-sm text-muted-foreground">{s.channel}</p>
                  <p className="mt-1 text-3xl font-semibold tabular-nums">
                    {(s.hit_rate * 100).toFixed(1)}
                    <span className="text-lg text-muted-foreground">%</span>
                  </p>
                  <div
                    className="mt-3 h-1 overflow-hidden rounded-full bg-muted"
                    role="meter"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(s.hit_rate * 100)}
                    aria-label={`${s.channel} 命中率 ${(s.hit_rate * 100).toFixed(1)}%`}
                  >
                    <div
                      className="h-full rounded-full bg-primary transition-all"
                      style={{ width: `${Math.min(100, s.hit_rate * 100)}%` }}
                    />
                  </div>
                  <p className="mt-3 text-xs text-muted-foreground tabular-nums">
                    命中 {s.hits} / {s.total} · P0（中国出海）{s.p0} 条
                  </p>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* 线索表（Q12 筛选 / Q13 列视觉 / Q17-18 分页） */}
        <section aria-labelledby="leads-heading" className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="leads-heading" className="text-sm font-medium text-muted-foreground">
              线索（按分数）
              {leads !== null && !error && (
                <span className="ml-2 tabular-nums">{leads.length} 条</span>
              )}
            </h2>
            <div className="flex items-center gap-4">
              <Select value={channel} onValueChange={setChannel}>
                <SelectTrigger className="w-32" aria-label="按渠道筛选">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部渠道</SelectItem>
                  {stats?.map((s) => (
                    <SelectItem key={s.channel} value={s.channel}>
                      {s.channel}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <Switch
                  checked={p0Only}
                  onCheckedChange={setP0Only}
                  aria-label="仅看 P0（中国出海）线索"
                />
                仅看 P0
              </label>
            </div>
          </div>

          {error ? (
            <PanelError onRetry={refresh} label="无法加载线索列表" />
          ) : leads === null ? (
            <Skeleton className="h-64 w-full" />
          ) : leads.length === 0 ? (
            <div className="flex h-40 flex-col items-center justify-center gap-1 rounded-lg border border-dashed text-sm text-muted-foreground">
              <p>暂无线索</p>
              <p className="text-xs">先跑 seed + crawl（见 README），完成后点右上角刷新。</p>
            </div>
          ) : (
            <>
              <Table>
                <caption className="sr-only">线索列表，按分数降序排列</caption>
                <TableHeader>
                  <TableRow>
                    <TableHead>企业</TableHead>
                    <TableHead className="hidden md:table-cell">渠道</TableHead>
                    <TableHead className="hidden md:table-cell">市场</TableHead>
                    <TableHead className="hidden md:table-cell">语言</TableHead>
                    <TableHead>P0</TableHead>
                    <TableHead className="text-right">分数</TableHead>
                    <TableHead>号码</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageLeads.map((l) => {
                    const phones = l.phones.split(',')
                    return (
                      <TableRow key={l.entity}>
                        <TableCell className="font-medium">{l.entity}</TableCell>
                        <TableCell className="hidden text-muted-foreground md:table-cell">
                          {l.channel}
                        </TableCell>
                        <TableCell className="hidden text-muted-foreground md:table-cell">
                          {l.market ?? '—'}
                        </TableCell>
                        <TableCell className="hidden text-muted-foreground md:table-cell">
                          {l.lang ?? '—'}
                        </TableCell>
                        <TableCell>
                          {l.p0 ? <Badge>P0</Badge> : <span className="text-muted-foreground">—</span>}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{l.score}</TableCell>
                        <TableCell
                          className="max-w-40 truncate font-mono text-xs tabular-nums md:max-w-none"
                          title={l.phones}
                        >
                          {phones[0]}
                          {phones.length > 1 && (
                            <span className="ml-1 text-muted-foreground">
                              +{phones.length - 1}
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>

              {totalPages > 1 && (
                <nav aria-label="线索分页">
                  <Pagination>
                    <PaginationContent>
                      <PaginationItem>
                        <PaginationPrevious
                          href="#"
                          aria-disabled={page === 1}
                          className={page === 1 ? 'pointer-events-none opacity-50' : ''}
                          onClick={(e) => {
                            e.preventDefault()
                            setPage((p) => Math.max(1, p - 1))
                          }}
                        />
                      </PaginationItem>
                      {pageWindow(page, totalPages).map((p, i) =>
                        p === '…' ? (
                          <PaginationItem key={`e${i}`}>
                            <PaginationEllipsis />
                          </PaginationItem>
                        ) : (
                          <PaginationItem key={p}>
                            <PaginationLink
                              href="#"
                              isActive={p === page}
                              aria-label={`第 ${p} 页`}
                              aria-current={p === page ? 'page' : undefined}
                              onClick={(e) => {
                                e.preventDefault()
                                setPage(p)
                              }}
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
                          onClick={(e) => {
                            e.preventDefault()
                            setPage((p) => Math.min(totalPages, p + 1))
                          }}
                        />
                      </PaginationItem>
                    </PaginationContent>
                  </Pagination>
                </nav>
              )}
            </>
          )}
        </section>
      </main>
    </div>
  )
}

/* ---- 错误态（Q7：destructive Alert + 重试，与空态区分） ---- */
function PanelError({ onRetry, label }: { onRetry: () => void; label: string }) {
  return (
    <Alert variant="destructive">
      <AlertCircle className="size-4" />
      <AlertTitle>{label}</AlertTitle>
      <AlertDescription className="flex items-center gap-3">
        后端未响应，请确认 API 服务已启动。
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </AlertDescription>
    </Alert>
  )
}
