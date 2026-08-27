/**
 * 登录跳转消毒：只接受站内相对路径（单个 / 开头，拒绝 // 协议相对）。
 * 防 open redirect：/login?redirect=//evil.com 不能把用户带去外部域名。
 */
export function sanitizeRedirect(raw: string | undefined): string {
  if (raw && raw.startsWith('/') && !raw.startsWith('//')) return raw
  return '/dashboard'
}
