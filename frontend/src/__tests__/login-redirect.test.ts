/**
 * login open-redirect 防御 — 第三轮 bug fix 的回归测试。
 *
 * 关键：redirect query 必须是相对路径（以 / 开头且不是 //）。
 * 任何 //evil.com / javascript: 等都会被拒到 /dashboard。
 */
import { describe, it, expect } from 'vitest'
// 测的是生产代码（src/utils/redirect.ts）——login 页跳转前必须经过它
import { sanitizeRedirect } from '@/utils/redirect'

describe('login redirect sanitization', () => {
  it('accepts simple relative path', () => {
    expect(sanitizeRedirect('/dashboard')).toBe('/dashboard')
    expect(sanitizeRedirect('/users/123')).toBe('/users/123')
  })
  it('falls back to /dashboard on undefined', () => {
    expect(sanitizeRedirect(undefined)).toBe('/dashboard')
  })
  it('blocks protocol-relative //evil.com (open redirect)', () => {
    expect(sanitizeRedirect('//evil.com/fake')).toBe('/dashboard')
  })
  it('blocks javascript: URI', () => {
    expect(sanitizeRedirect('javascript:alert(1)')).toBe('/dashboard')
  })
  it('blocks external https URL', () => {
    expect(sanitizeRedirect('https://evil.com')).toBe('/dashboard')
  })
  it('blocks empty string', () => {
    expect(sanitizeRedirect('')).toBe('/dashboard')
  })
})
