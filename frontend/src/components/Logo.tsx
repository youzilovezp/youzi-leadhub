/**
 * youzi 品牌标 —— WhatsApp-inspired 黑白优化版。
 *
 * 专业 UI 设计师视角（致敬 WhatsApp + 设计演进）：
 *
 *  **保留的 DNA（识别锚点 = 一眼读作"messaging"）**
 *    • 圆角方块容器
 *    • 白色对话气泡
 *    • 气泡左下角的咬合尾巴
 *    • 内部电话听筒（WhatsApp 最具识别度的元素）
 *
 *  **设计优化（区别于 WhatsApp 原版）**
 *    1. **配色**：zinc-800/950 渐变（非绿），黑白极简
 *    2. **气泡**：rx=2 圆角（比 WhatsApp 的"胖圆角"更克制精致）
 *    3. **尾巴**：3 单位高 × 4 单位宽的等腰小三角（垂直底边在 x=11，
 *       顶尖 (11,25)，底边右端 (15,21)），比 WhatsApp 的不规则
 *       尖锐尾巴更几何、更精致
 *    4. **电话听筒**：单一闭合 path 绘制 C 形曲线（"earpiece-mic"连续形），
 *       比 WhatsApp 那种粗线条卡通电话更细腻
 *    5. **内描边 7% 白**：Apple-style inner border，方块 → 实物
 *
 *  **反 AI-cliché**
 *    零紫色光晕，零 emoji，零"创意"渐变，纯锌系中性。
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
      <defs>
        {/* 容器渐变：zinc-800 → zinc-950 */}
        <linearGradient id="youzi-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#27272A" />
          <stop offset="100%" stopColor="#09090B" />
        </linearGradient>
      </defs>
      {/* 容器 */}
      <rect width="32" height="32" rx="8" fill="url(#youzi-bg)" />
      {/* 内描边：7% 白 */}
      <rect
        x="0.5" y="0.5" width="31" height="31" rx="7.5"
        fill="none"
        stroke="#FFFFFF" strokeOpacity="0.07"
      />

      {/* === 对话气泡 + 尾巴（WhatsApp-style，优化版）=== */}
      {/* 主体：(9,9)→(23,9)→(23,21)→(15,21)，rx=2 圆角
          尾巴：(11,21)→(11,25)→(15,21)，垂直左边 + 等腰三角 */}
      <path
        d="M 11 9
           Q 9 9 9 11
           V 19
           Q 9 21 11 21
           L 11 25
           L 15 21
           H 21
           Q 23 21 23 19
           V 11
           Q 23 9 21 9
           Z"
        fill="#FAFAFA"
      />

      {/* === 电话听筒（WhatsApp 标志性元素，C 形连续曲线）=== */}
      <path
        d="M 14 11.5
           C 13.4 11.5 13 12 13 12.5
           C 13 15 14.7 17.3 17.5 17.5
           C 18 17.5 18.5 17 18.5 16.5
           L 18.5 16
           C 18.5 15.5 18 15 17.5 15
           L 17 15
           C 16 15 15.5 14.5 15.5 13.5
           L 15.5 13
           C 15.5 12.5 15 12 14.5 12
           Z"
        fill="#18181B"
      />
    </svg>
  )
}

type LogoMarkProps = SVGProps<SVGSVGElement> & {
  size?: number
}

export function LogoMark({ size = 32, ...props }: LogoMarkProps) {
  return <Logo size={size} {...props} />
}