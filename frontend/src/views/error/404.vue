<script setup lang="ts">
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template>
  <div class="not-found">
    <div class="content">
      <h1>404</h1>
      <p>页面不存在</p>
      <n-button type="primary" @click="router.push('/')">返回首页</n-button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.not-found {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--yz-bg-page);
}
.content {
  text-align: center;
  h1 {
    font-size: 96px;
    color: var(--yz-primary);
    margin: 0;
  }
  p {
    color: var(--yz-text-secondary);
    margin-bottom: 24px;
  }
}
</style>
