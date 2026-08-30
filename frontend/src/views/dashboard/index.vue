<script setup lang="ts">
import { onMounted, ref, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  PeopleOutline,
  CheckmarkCircleOutline,
  PersonCircleOutline,
  SunnyOutline,
  LinkOutline,
  ArrowForwardOutline,
} from '@vicons/ionicons5'
import { listUsers } from '@/api/user'
import { listRoles } from '@/api/role'
import { useUserStore } from '@/stores/user'
import LoginTrendChart from './components/LoginTrendChart.vue'
import RolePieChart from './components/RolePieChart.vue'
import { countByRole, type RoleStat } from './role-stats'

const userStore = useUserStore()

const totalUsers = ref(0)
const activeUsers = ref(0)
const roleCount = ref(0)
const roleDist = ref<RoleStat[]>([])
const roleDistFailed = ref(false)

const today = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
})

const shortcuts: { title: string; desc: string; icon: Component; action: () => void }[] = [
  { title: '用户管理', desc: '增删改查、启用禁用', icon: PeopleOutline, action: () => goto('/system/user') },
  { title: '角色管理', desc: '维护角色与权限', icon: PersonCircleOutline, action: () => goto('/system/role') },
  {
    title: 'Swagger',
    desc: '后端接口文档',
    icon: LinkOutline,
    action: () => window.open('/api/v1/docs', '_blank'),
  },
]

const router = useRouter()
function goto(path: string) {
  router.push(path)
}

onMounted(async () => {
  // 统计卡：失败降级为 0
  try {
    const [all, active] = await Promise.all([
      listUsers({ page: 1, page_size: 1 }),
      listUsers({ page: 1, page_size: 1, is_active: 1 }),
    ])
    totalUsers.value = all.total
    activeUsers.value = active.total
  } catch {
    // 保持 0
  }
  try {
    roleCount.value = (await listRoles()).length
  } catch {
    // 保持 0
  }
  // 角色分布：只取前 100 个用户统计（>100 时展示的是前 100 的分布）
  try {
    const { items } = await listUsers({ page: 1, page_size: 100 })
    roleDist.value = countByRole(items)
  } catch {
    roleDistFailed.value = true
  }
})
</script>

<template>
  <div class="space-y-4">
    <!-- 统计卡 -->
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <div
        v-for="stat in [
          { label: '用户总数', value: totalUsers, icon: PeopleOutline },
          { label: '启用用户', value: activeUsers, icon: CheckmarkCircleOutline },
          { label: '角色数', value: roleCount, icon: PersonCircleOutline },
        ]"
        :key="stat.label"
        class="flex items-center gap-4 rounded-card border border-border bg-bg-card p-5"
      >
        <span
          class="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-full"
          :style="{
            background: 'color-mix(in srgb, var(--yz-primary) 12%, transparent)',
            color: 'var(--yz-primary)',
          }"
        >
          <n-icon
            :size="22"
            :component="stat.icon"
          />
        </span>
        <div>
          <div class="text-text-secondary text-sm">
            {{ stat.label }}
          </div>
          <div class="text-text text-2xl font-semibold">
            {{ stat.value }}
          </div>
        </div>
      </div>

      <!-- 欢迎卡 -->
      <div class="rounded-card border border-border bg-bg-card p-5">
        <div class="flex items-center gap-2">
          <n-icon
            :size="18"
            :style="{ color: 'var(--yz-primary)' }"
            :component="SunnyOutline"
          />
          <span class="text-text-secondary text-sm">{{ today }}</span>
        </div>
        <div class="text-text mt-2 truncate text-lg font-semibold">
          欢迎回来，{{ userStore.displayName }}
        </div>
      </div>
    </div>

    <!-- 图表 -->
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div class="rounded-card border border-border bg-bg-card p-5">
        <div class="text-text mb-2 font-semibold">
          近 7 日登录趋势
        </div>
        <LoginTrendChart />
      </div>
      <div class="rounded-card border border-border bg-bg-card p-5">
        <div class="text-text mb-2 font-semibold">
          角色分布
        </div>
        <n-empty
          v-if="roleDistFailed"
          description="角色分布加载失败"
          size="large"
        />
        <RolePieChart
          v-else
          :data="roleDist"
        />
      </div>
    </div>

    <!-- 快捷入口 -->
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <button
        v-for="item in shortcuts"
        :key="item.title"
        type="button"
        class="flex cursor-pointer items-center justify-between rounded-card border border-border bg-bg-card p-5 text-left transition-colors hover:border-primary"
        @click="item.action"
      >
        <span class="flex items-center gap-3">
          <span
            class="inline-flex h-10 w-10 items-center justify-center rounded-full"
            :style="{
              background: 'color-mix(in srgb, var(--yz-primary) 12%, transparent)',
              color: 'var(--yz-primary)',
            }"
          >
            <n-icon
              :size="18"
              :component="item.icon"
            />
          </span>
          <span>
            <span class="text-text block font-medium">{{ item.title }}</span>
            <span class="text-text-secondary block text-xs">{{ item.desc }}</span>
          </span>
        </span>
        <n-icon
          class="text-text-secondary"
          :component="ArrowForwardOutline"
        />
      </button>
    </div>
  </div>
</template>
