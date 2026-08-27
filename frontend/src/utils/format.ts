/**
 * 时间显示：SQLite 返回无时区后缀的 UTC 字符串（如 2026-08-26T02:50:37），
 * new Date() 会按本地时区解析导致差 N 小时。统一：无后缀视为 UTC。
 */
export function formatTime(value: string | Date | null | undefined): string {
  if (!value) return '-'
  const d = typeof value === 'string' && !/[Z+]/.test(value.slice(-6))
    ? new Date(value + 'Z')
    : new Date(value)
  return isNaN(d.getTime()) ? String(value) : d.toLocaleString()
}
