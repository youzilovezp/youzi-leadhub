/**
 * 时间显示：SQLite 返回无时区后缀的 UTC 字符串（如 2026-08-26T02:50:37），
 * new Date() 会按本地时区解析导致差 N 小时。统一：无后缀视为 UTC。
 */
/**
 * 解析后端时间串为 Date：无时区后缀（Z / +hh:mm）的按 UTC 补 'Z' 后解析。
 * 需要拿后端时间做比较 / 计算时必须用这个，不能裸 new Date(str)（会按本地时区解释）。
 */
export function parseUtc(iso: string): Date {
  return !/[Z+]/.test(iso.slice(-6)) ? new Date(iso + 'Z') : new Date(iso)
}

export function formatTime(value: string | Date | null | undefined): string {
  if (!value) return '-'
  const d = typeof value === 'string' ? parseUtc(value) : new Date(value)
  return isNaN(d.getTime()) ? String(value) : d.toLocaleString()
}
