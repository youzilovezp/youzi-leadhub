// 角色分布统计：按 role_name 分组计数（无角色的用户归入"未分配"）
import type { UserInfo } from '@/api/types'

export interface RoleStat {
  name: string
  value: number
}

export function countByRole(items: Pick<UserInfo, 'role_name'>[]): RoleStat[] {
  const map = new Map<string, number>()
  for (const u of items) {
    const name = u.role_name || '未分配'
    map.set(name, (map.get(name) ?? 0) + 1)
  }
  return [...map.entries()].map(([name, value]) => ({ name, value }))
}
