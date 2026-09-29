/** youzi-LeadHub 品牌 Logo ——「Hub + Satellites」概念。
 *
 * 视觉语义：
 * - 中央实心圆 = LeadHub 本身（中心节点）
 * - 4 道卫星点（N/S/E/W）= 已发现的销售线索
 * - 连接短线 = 信号/关联通路
 *
 * 单文件 SVG component。深墨底 + emerald 强调色 — 用颜色本身传达"WhatsApp
 * 业务接入"语义，但不再做"绿松渐变 + 雷达圈"那种通用 SaaS 图标套路。
 */
import { type SVGProps } from 'react'

type LogoProps = {
  size?: number
  /** "full"（默认）= 中心 + 4 卫星 + 连接线；"compact" = 仅中心 + 卫星（favicon/小尺寸） */
  variant?: 'full' | 'compact'
  className?: string
}

export function Logo({ size = 32, variant = 'full', className }: LogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden
      role="img"
    >
      {/* 深墨底 — 不再用渐变，告别"默认 app icon"质感 */}
      <rect width="32" height="32" rx="7" fill="#0F172A" />

      {/* 4 条连接线（compact 模式隐藏）：从中心到 4 个方向卫星 */}
      {variant === 'full' && (
        <g stroke="#10B981" strokeWidth="1" strokeLinecap="round" opacity="0.55">
          <line x1="16" y1="13" x2="16" y2="7.6" />
          <line x1="19" y1="16" x2="24.4" y2="16" />
          <line x1="16" y1="19" x2="16" y2="24.4" />
          <line x1="13" y1="16" x2="7.6" y2="16" />
        </g>
      )}

      {/* 4 个卫星点（NSEW）：已发现的线索 */}
      <g fill="#10B981">
        <circle cx="16" cy="6" r="1.7" />
        <circle cx="26" cy="16" r="1.7" />
        <circle cx="16" cy="26" r="1.7" />
        <circle cx="6" cy="16" r="1.7" />
      </g>

      {/* 中央 hub：本工具本身 */}
      <circle cx="16" cy="16" r="3.4" fill="#10B981" />

      {/* hub 内层镂空圆：制造"中空环"的层次感，避免与卫星点同质 */}
      <circle cx="16" cy="16" r="1.3" fill="#0F172A" />
    </svg>
  )
}

type LogoMarkProps = SVGProps<SVGSVGElement> & {
  size?: number
}

/** 仅 Logo 图形（不含外层包装），用于嵌入其他 SVG 组合或 favicon。 */
export function LogoMark({ size = 32, ...props }: LogoMarkProps) {
  return <Logo size={size} {...props} />
}