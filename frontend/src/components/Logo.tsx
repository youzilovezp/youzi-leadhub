/** youzi LeadHub 品牌 Logo —— 极简字母标 "L"。
 *
 * 单元素几何：粗体无衬线 L（Lead 首字母），统一描边宽度，
 * 黑底白字，无装饰、无 accent、无渐变。
 */
import { type SVGProps } from 'react'

type LogoProps = {
  size?: number
  className?: string
}

export function Logo({ size = 32, className }: LogoProps) {
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
      <rect width="32" height="32" rx="7" fill="#18181B" />
      <path d="M 11 8 L 14 8 L 14 21 L 21 21 L 21 24 L 11 24 Z" fill="#FAFAFA" />
    </svg>
  )
}

type LogoMarkProps = SVGProps<SVGSVGElement> & {
  size?: number
}

export function LogoMark({ size = 32, ...props }: LogoMarkProps) {
  return <Logo size={size} {...props} />
}
