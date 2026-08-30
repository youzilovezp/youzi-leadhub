<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { type FormInst, type FormRules } from 'naive-ui'
import { PersonOutline, LockClosedOutline, ArrowForwardOutline } from '@vicons/ionicons5'
import { useUserStore } from '@/stores/user'
import { sanitizeRedirect } from '@/utils/redirect'
import { message } from '@/utils/feedback'
import { APP_TITLE } from '@/config'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const formRef = ref<FormInst>()
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
})

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '长度 3-50 个字符', trigger: 'blur' },
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const features = [
  '开箱即用的中后台脚手架',
  'Naive UI 全套组件生态',
  '权限 / 角色 / 菜单一体化',
]

// 修复：已登录用户访问 /login 应自动跳走，否则会再次提交无效登录请求
onMounted(() => {
  if (userStore.isLogin) {
    router.replace('/dashboard')
  }
})

async function onSubmit() {
  if (!formRef.value) return
  // naive validate() 失败 reject——catch false 保持与旧实现一致的控制流
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await userStore.login(form)
    message.success('登录成功')
    router.replace(sanitizeRedirect(route.query.redirect as string | undefined))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-root grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
    <!-- 左：品牌面板（移动端隐藏） -->
    <aside class="brand-panel relative hidden overflow-hidden lg:flex lg:flex-col lg:justify-between">
      <!-- 装饰光斑 -->
      <div class="orb orb-a" />
      <div class="orb orb-b" />
      <div class="orb orb-c" />
      <!-- 细网格底纹 -->
      <div class="grid-overlay" />

      <div class="relative z-10 flex items-center gap-3 p-10">
        <img
          src="/youzi-logo.svg"
          alt="logo"
          class="h-10 w-10"
        >
        <span class="text-xl font-semibold text-white">{{ APP_TITLE }}</span>
      </div>

      <div class="relative z-10 px-10 pb-6">
        <h1 class="max-w-md text-4xl font-bold leading-tight text-white">
          轻量、优雅的<br>管理系统起点
        </h1>
        <ul class="mt-6 space-y-3">
          <li
            v-for="f in features"
            :key="f"
            class="flex items-center gap-3 text-white/85"
          >
            <span class="dot" />
            <span>{{ f }}</span>
          </li>
        </ul>
      </div>

      <div class="relative z-10 px-10 pb-8 text-xs text-white/50">
        © {{ new Date().getFullYear() }} {{ APP_TITLE }} · Powered by Naive UI
      </div>
    </aside>

    <!-- 右：登录表单 -->
    <main class="form-panel flex items-center justify-center px-6">
      <div class="w-full max-w-sm">
        <!-- 移动端 logo -->
        <div class="mb-8 flex flex-col items-center gap-2 lg:hidden">
          <img
            src="/youzi-logo.svg"
            alt="logo"
            class="h-14 w-14"
          >
          <h1 class="text-xl font-bold">
            {{ APP_TITLE }}
          </h1>
        </div>

        <h2 class="text-2xl font-bold">
          欢迎回来 👋
        </h2>
        <p
          class="mt-2 text-sm"
          :style="{ color: 'var(--yz-text-secondary)' }"
        >
          登录你的账号继续
        </p>

        <n-form
          ref="formRef"
          :model="form"
          :rules="rules"
          size="large"
          :show-label="false"
          class="mt-8"
        >
          <n-form-item path="username">
            <n-input
              v-model:value="form.username"
              placeholder="用户名"
              :input-props="{ autocomplete: 'username' }"
              @keyup.enter="onSubmit"
            >
              <template #prefix>
                <n-icon
                  :component="PersonOutline"
                  :style="{ color: 'var(--yz-text-secondary)' }"
                />
              </template>
            </n-input>
          </n-form-item>

          <n-form-item path="password">
            <n-input
              v-model:value="form.password"
              type="password"
              show-password-on="click"
              placeholder="密码"
              :input-props="{ autocomplete: 'current-password' }"
              @keyup.enter="onSubmit"
            >
              <template #prefix>
                <n-icon
                  :component="LockClosedOutline"
                  :style="{ color: 'var(--yz-text-secondary)' }"
                />
              </template>
            </n-input>
          </n-form-item>

          <n-button
            type="primary"
            size="large"
            block
            :loading="loading"
            class="mt-2"
            @click="onSubmit"
          >
            <span class="inline-flex items-center gap-1.5">
              登录
              <n-icon :component="ArrowForwardOutline" />
            </span>
          </n-button>
        </n-form>

        <p
          class="mt-8 text-center text-xs"
          :style="{ color: 'var(--yz-text-secondary)' }"
        >
          默认账号 admin，密码见 backend/.env 的 INITIAL_ADMIN_PASSWORD
        </p>
      </div>
    </main>
  </div>
</template>

<style scoped lang="scss">
.login-root {
  background: var(--yz-bg-page);
}

/* ---------- 品牌面板：主色深渐变 + 光斑 ---------- */
.brand-panel {
  background:
    linear-gradient(
      160deg,
      color-mix(in srgb, var(--yz-primary) 78%, #000) 0%,
      color-mix(in srgb, var(--yz-primary) 45%, #101014) 55%,
      #101014 100%
    );
}

.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(70px);
  opacity: 0.5;
  pointer-events: none;
}
.orb-a {
  width: 420px;
  height: 420px;
  top: -120px;
  right: -80px;
  background: color-mix(in srgb, var(--yz-primary) 60%, white);
}
.orb-b {
  width: 300px;
  height: 300px;
  bottom: -60px;
  left: -60px;
  background: color-mix(in srgb, var(--yz-primary) 40%, #ffffff22);
}
.orb-c {
  width: 200px;
  height: 200px;
  bottom: 20%;
  right: 15%;
  background: #ffffff30;
  opacity: 0.25;
}

/* 细网格：5% 白线，营造质感 */
.grid-overlay {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(ellipse at 30% 40%, black 30%, transparent 75%);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: white;
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.18);
  flex-shrink: 0;
}

.form-panel {
  background: var(--yz-bg-card);
}

/* 暗色下右栏与页面底色拉开层次 */
:global(html.dark) .form-panel {
  background: var(--yz-bg-page);
}
</style>
