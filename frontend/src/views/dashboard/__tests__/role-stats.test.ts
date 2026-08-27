import { describe, it, expect } from 'vitest'
import { countByRole } from '@/views/dashboard/role-stats'

describe('countByRole 角色分布统计', () => {
  it('按 role_name 分组计数，缺失角色归入未分配', () => {
    const items = [
      { role_name: '管理员' },
      { role_name: '管理员' },
      { role_name: '普通用户' },
      {},
      { role_name: undefined },
    ]
    expect(countByRole(items)).toEqual([
      { name: '管理员', value: 2 },
      { name: '普通用户', value: 1 },
      { name: '未分配', value: 2 },
    ])
  })

  it('空数组返回空列表', () => {
    expect(countByRole([])).toEqual([])
  })
})
