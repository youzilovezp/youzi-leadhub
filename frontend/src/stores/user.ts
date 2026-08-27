// 用户状态
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '@/api/auth'
import type { LoginPayload, UserInfo } from '@/api/types'
import { TOKEN_KEY } from '@/config'

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')
  const userInfo = ref<UserInfo | null>(null)

  const isLogin = computed(() => !!token.value)
  const isSuperuser = computed(() => userInfo.value?.is_superuser ?? false)
  const displayName = computed(
    () => userInfo.value?.nickname || userInfo.value?.username || '未登录'
  )

  async function login(payload: LoginPayload) {
    const data = await authApi.login(payload)
    token.value = data.access_token
    userInfo.value = data.user
    localStorage.setItem(TOKEN_KEY, data.access_token)
    return data
  }

  async function fetchProfile() {
    if (!token.value) return null
    userInfo.value = await authApi.getMe()
    return userInfo.value
  }

  async function logout() {
    try {
      await authApi.logout()
    } catch (e) {
      // 忽略网络错误，仍然清除本地状态
    }
    token.value = ''
    userInfo.value = null
    localStorage.removeItem(TOKEN_KEY)
  }

  return {
    token,
    userInfo,
    isLogin,
    isSuperuser,
    displayName,
    login,
    fetchProfile,
    logout,
  }
})
