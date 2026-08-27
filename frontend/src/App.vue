<script setup lang="ts">
// 根组件：Naive UI 全局配置（暗色主题 + 主题色 overrides 来自 app store）
import { NConfigProvider, NMessageProvider, NDialogProvider, zhCN, dateZhCN } from 'naive-ui'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
</script>

<template>
  <NConfigProvider
    :theme="appStore.naiveTheme"
    :theme-overrides="appStore.naiveThemeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
    style="height: 100%"
  >
    <NMessageProvider>
      <NDialogProvider>
        <router-view />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style lang="scss">
html,
body,
#app {
  height: 100%;
  margin: 0;
  padding: 0;
}
</style>
