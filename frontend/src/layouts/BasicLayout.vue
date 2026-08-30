<script setup lang="ts">
import { computed, h, type Component } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NIcon, type MenuOption } from 'naive-ui'
import {
  HomeOutline,
  SettingsOutline,
  PeopleOutline,
  PersonCircleOutline,
  SunnyOutline,
  MoonOutline,
  ColorPaletteOutline,
  MenuOutline,
  ChevronDownOutline,
  LogOutOutline,
  PersonOutline,
  CompassOutline,
  FlameOutline,
  ListOutline,
  DownloadOutline,
  FlashOutline,
  PulseOutline,
  NotificationsOutline,
} from '@vicons/ionicons5'
import { useUserStore } from '@/stores/user'
import { useAppStore, THEME_PRESETS } from '@/stores/app'
import { confirm } from '@/utils/feedback'
import { APP_TITLE } from '@/config'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const appStore = useAppStore()

function renderIcon(icon: Component) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const menus: MenuOption[] = [
  { label: '首页', key: '/dashboard', icon: renderIcon(HomeOutline) },
  {
    label: '线索采集',
    key: 'collect',
    icon: renderIcon(CompassOutline),
    children: [
      { label: '今日商机', key: '/collect/daily', icon: renderIcon(FlameOutline) },
      { label: '线索列表', key: '/collect/lead', icon: renderIcon(ListOutline) },
      { label: '采集任务', key: '/collect/task', icon: renderIcon(DownloadOutline) },
    ],
  },
  {
    label: '销售工作台',
    key: 'sales',
    icon: renderIcon(FlashOutline),
    children: [
      { label: '数据源管理', key: '/sales', icon: renderIcon(PulseOutline) },
      { label: '高价值预警', key: '/sales/alerts', icon: renderIcon(NotificationsOutline) },
    ],
  },
  {
    label: '系统管理',
    key: 'system',
    icon: renderIcon(SettingsOutline),
    children: [
      { label: '用户管理', key: '/system/user', icon: renderIcon(PeopleOutline) },
      { label: '角色管理', key: '/system/role', icon: renderIcon(PersonCircleOutline) },
    ],
  },
]

const ADMIN_MENU_KEYS = new Set(['/system/user', '/system/role'])

const menuOptions = computed<MenuOption[]>(() => {
  const isAdmin = userStore.isSuperuser
  return menus
    .map((m) => {
      if (m.children) {
        const children = (m.children as MenuOption[]).filter(
          (c) => !ADMIN_MENU_KEYS.has(String(c.key)) || isAdmin
        )
        return children.length ? { ...m, children } : null
      }
      return m
    })
    .filter((m): m is MenuOption => m !== null)
})

const activeMenu = computed(() => route.path)

function handleMenuSelect(key: string) {
  router.push(key)
}

const userOptions = [
  { label: '个人中心', key: 'profile', icon: renderIcon(PersonOutline) },
  { type: 'divider', key: 'd1' },
  { label: '退出登录', key: 'logout', icon: renderIcon(LogOutOutline) },
]

async function handleUserCommand(key: string) {
  if (key !== 'logout') return
  // confirm：确认 true / 取消 false
  if (!(await confirm({ title: '提示', content: '确定要退出登录吗？', positiveText: '退出' })))
    return
  await userStore.logout()
  // replace 而非 push：避免在历史栈留一条已退出的回边
  router.replace('/login')
}
</script>

<template>
  <n-layout
    class="layout-container"
    has-sider
  >
    <!-- 侧边栏 -->
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed="appStore.sidebarCollapsed"
      :collapsed-width="64"
      :width="220"
      show-trigger="bar"
      class="layout-aside"
      :native-scrollbar="false"
    >
      <div class="logo">
        <img
          src="/youzi-logo.svg"
          alt="logo"
          class="logo-img"
        >
        <span
          v-if="!appStore.sidebarCollapsed"
          class="logo-text"
        >{{ APP_TITLE }}</span>
      </div>
      <n-menu
        :value="activeMenu"
        :options="menuOptions"
        :collapsed="appStore.sidebarCollapsed"
        :collapsed-width="64"
        :collapsed-icon-size="20"
        @update:value="handleMenuSelect"
      />
    </n-layout-sider>

    <n-layout class="layout-right">
      <!-- 顶栏：毛玻璃 -->
      <header class="layout-header">
        <div class="header-left">
          <n-button
            quaternary
            circle
            @click="appStore.toggleSidebar"
          >
            <template #icon>
              <n-icon :component="MenuOutline" />
            </template>
          </n-button>
          <n-breadcrumb>
            <n-breadcrumb-item @click="router.push('/dashboard')">
              首页
            </n-breadcrumb-item>
            <n-breadcrumb-item v-if="route.meta.title">
              {{ route.meta.title }}
            </n-breadcrumb-item>
          </n-breadcrumb>
        </div>
        <div class="header-right">
          <!-- 暗色切换 -->
          <n-button
            quaternary
            circle
            :title="appStore.isDark ? '切换亮色模式' : '切换暗色模式'"
            @click="appStore.toggleDark"
          >
            <template #icon>
              <n-icon :component="appStore.isDark ? SunnyOutline : MoonOutline" />
            </template>
          </n-button>

          <!-- 主题色 -->
          <n-popover
            trigger="click"
            placement="bottom-end"
            :width="220"
          >
            <template #trigger>
              <n-button
                quaternary
                circle
                title="主题色"
              >
                <template #icon>
                  <n-icon :component="ColorPaletteOutline" />
                </template>
              </n-button>
            </template>
            <div class="swatch-grid">
              <div
                v-for="p in THEME_PRESETS"
                :key="p.color"
                class="swatch-item"
              >
                <button
                  type="button"
                  class="swatch"
                  :class="{ active: p.color === appStore.primaryColor }"
                  :style="{ background: p.color }"
                  :title="p.name"
                  @click="appStore.setPrimaryColor(p.color)"
                />
                <span class="swatch-name">{{ p.name }}</span>
              </div>
            </div>
          </n-popover>

          <!-- 用户 -->
          <n-dropdown
            :options="userOptions"
            @select="handleUserCommand"
          >
            <span class="user-info">
              <n-avatar
                round
                :size="30"
                :style="{ background: 'var(--yz-primary)' }"
              >
                {{ userStore.userInfo?.nickname?.charAt(0) || 'U' }}
              </n-avatar>
              <span>{{ userStore.displayName }}</span>
              <n-icon :component="ChevronDownOutline" />
            </span>
          </n-dropdown>
        </div>
      </header>

      <!-- 主内容 -->
      <n-layout-content
        class="layout-main"
        :native-scrollbar="false"
      >
        <router-view v-slot="{ Component }">
          <transition name="fade">
            <component :is="Component" />
          </transition>
        </router-view>
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<style scoped lang="scss">
.layout-container {
  height: 100vh;
}

.layout-aside {
  .logo {
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    padding: 0 16px;
    gap: 8px;
    font-size: 16px;
    font-weight: bold;
    overflow: hidden;
  }

  .logo-img {
    height: 36px;
    width: 36px;
    flex-shrink: 0;
  }

  .logo-text {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.layout-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  border-bottom: 1px solid var(--yz-border);
  background: color-mix(in srgb, var(--yz-bg-card) 70%, transparent);
  backdrop-filter: blur(12px);
  position: sticky;
  top: 0;
  z-index: 10;

  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.swatch-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px 8px;
}

.swatch-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.swatch {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: none;
  padding: 0;
  cursor: pointer;
  transition: transform 0.15s;

  &:hover {
    transform: scale(1.15);
  }

  &.active {
    box-shadow:
      0 0 0 2px var(--yz-bg-card),
      0 0 0 4px var(--yz-primary);
  }
}

.swatch-name {
  font-size: 12px;
  color: var(--yz-text-secondary);
}

.layout-main {
  padding: 16px;
  background: var(--yz-bg-page);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
