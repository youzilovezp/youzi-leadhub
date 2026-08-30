<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NTag, type DataTableColumns, type FormInst, type FormRules } from 'naive-ui'
import * as userApi from '@/api/user'
import * as roleApi from '@/api/role'
import type { UserInfo } from '@/api/types'
import type { Role } from '@/api/role'
import { useUserStore } from '@/stores/user'
import { formatTime } from '@/utils/format'
import { message, confirm } from '@/utils/feedback'

const userStore = useUserStore()
const loading = ref(false)
const tableData = ref<UserInfo[]>([])
const total = ref(0)
const roles = ref<Role[]>([])

const query = reactive({
  page: 1,
  page_size: 20,
  username: '',
  is_active: undefined as number | undefined, // 1/0：FastAPI 自动转 bool
})

const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const formRef = ref<FormInst>()

const formRules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  email: [
    {
      validator: (_r: unknown, v: string) => !v || /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v),
      message: '邮箱格式不正确',
      trigger: 'blur',
    },
  ],
}
const form = reactive({
  id: 0,
  username: '',
  nickname: '',
  email: '',
  phone: '',
  password: '',
  role_id: undefined as number | undefined,
  is_active: true,
})

const roleOptions = () => roles.value.map((r) => ({ label: r.name, value: r.id }))

const columns: DataTableColumns<UserInfo> = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '用户名', key: 'username' },
  { title: '昵称', key: 'nickname' },
  { title: '邮箱', key: 'email', ellipsis: { tooltip: true } },
  { title: '角色', key: 'role_name', width: 110 },
  {
    title: '状态',
    key: 'is_active',
    width: 90,
    render(row) {
      return h(
        NTag,
        { type: row.is_active ? 'success' : 'error', size: 'small', bordered: false },
        { default: () => (row.is_active ? '启用' : '禁用') }
      )
    },
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 170,
    render: (row) => formatTime(row.created_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 130,
    fixed: 'right',
    render(row) {
      return [
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'primary', onClick: () => openEdit(row) },
          { default: () => '编辑' }
        ),
        h(
          NButton,
          { size: 'small', quaternary: true, type: 'error', onClick: () => handleDelete(row) },
          { default: () => '删除' }
        ),
      ]
    },
  },
]

async function fetchData() {
  loading.value = true
  try {
    const data = await userApi.listUsers(query)
    tableData.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function fetchRoles() {
  roles.value = await roleApi.listRoles()
}

function openCreate() {
  dialogMode.value = 'create'
  Object.assign(form, {
    id: 0,
    username: '',
    nickname: '',
    email: '',
    phone: '',
    password: '',
    role_id: undefined,
    is_active: true,
  })
  dialogVisible.value = true
}

function openEdit(row: UserInfo) {
  dialogMode.value = 'edit'
  // 显式 pick 字段，避免 spread 把 is_superuser/avatar/created_at 也注入 form
  // （那些字段不可编辑，注入会导致表单提交时多带参数）
  Object.assign(form, {
    id: row.id,
    username: row.username,
    nickname: row.nickname ?? '',
    email: row.email ?? '',
    phone: row.phone ?? '',
    role_id: row.role_id,
    is_active: row.is_active,
    password: '',
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!formRef.value) return
  // naive validate() 失败 reject——catch false，校验不过不发请求
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  if (dialogMode.value === 'create') {
    await userApi.createUser({
      username: form.username,
      password: form.password,
      nickname: form.nickname,
      email: form.email,
      phone: form.phone,
      role_id: form.role_id,
      is_active: form.is_active,
    })
    message.success('创建成功')
  } else {
    await userApi.updateUser(form.id, {
      nickname: form.nickname,
      email: form.email,
      phone: form.phone,
      role_id: form.role_id,
      is_active: form.is_active,
    })
    message.success('更新成功')
  }
  dialogVisible.value = false
  fetchData()
}

async function handleDelete(row: UserInfo) {
  // 前端二次校验：不能删除自己（防误操作）
  if (row.id === userStore.userInfo?.id) {
    message.error('不能删除自己')
    return
  }
  // 前端提示：删除 superuser 是高危操作（后端也会校验最后一个 superuser）
  const warning = row.is_superuser
    ? `确定要删除超级管理员「${row.username}」吗？删除后系统将无法恢复。`
    : `确定删除用户「${row.username}」吗？`
  if (!(await confirm({ title: '⚠️ 危险操作', content: warning, positiveText: '删除' })))
    return
  await userApi.deleteUser(row.id)
  message.success('已删除')
  fetchData()
}

function handlePageChange(p: number) {
  query.page = p
  fetchData()
}

function handlePageSizeChange(s: number) {
  query.page_size = s
  query.page = 1
  fetchData()
}

onMounted(() => {
  fetchData()
  fetchRoles()
})
</script>

<template>
  <div class="page">
    <!-- 搜索栏 -->
    <div class="mb-4 flex flex-wrap items-center gap-3">
      <n-input
        v-model:value="query.username"
        clearable
        placeholder="用户名模糊搜索"
        style="width: 200px"
      />
      <n-select
        v-model:value="query.is_active"
        clearable
        placeholder="状态"
        :options="[
          { label: '启用', value: 1 },
          { label: '禁用', value: 0 },
        ]"
        style="width: 120px"
      />
      <n-button
        type="primary"
        secondary
        @click="() => { query.page = 1; fetchData() }"
      >
        查询
      </n-button>
      <n-button
        quaternary
        @click="() => { query.username = ''; query.is_active = undefined; fetchData() }"
      >
        重置
      </n-button>
      <div class="flex-1" />
      <n-button
        type="primary"
        @click="openCreate"
      >
        新增用户
      </n-button>
    </div>

    <!-- 表格（服务端分页） -->
    <n-data-table
      remote
      :columns="columns"
      :data="tableData"
      :loading="loading"
      :row-key="(row: UserInfo) => row.id"
      :pagination="{
        page: query.page,
        pageSize: query.page_size,
        itemCount: total,
        pageSizes: [10, 20, 50, 100],
        showSizePicker: true,
        'onUpdate:page': handlePageChange,
        'onUpdate:pageSize': handlePageSizeChange,
      }"
      :scroll-x="900"
    />

    <!-- 新增/编辑弹窗 -->
    <n-modal
      v-model:show="dialogVisible"
      preset="card"
      :title="dialogMode === 'create' ? '新增用户' : '编辑用户'"
      style="width: 480px"
    >
      <n-form
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-placement="left"
        label-width="72"
      >
        <n-form-item
          label="用户名"
          path="username"
        >
          <n-input
            v-model:value="form.username"
            :disabled="dialogMode === 'edit'"
          />
        </n-form-item>
        <n-form-item
          v-if="dialogMode === 'create'"
          label="密码"
          path="password"
        >
          <n-input
            v-model:value="form.password"
            type="password"
            show-password-on="click"
          />
        </n-form-item>
        <n-form-item label="昵称">
          <n-input v-model:value="form.nickname" />
        </n-form-item>
        <n-form-item
          label="邮箱"
          path="email"
        >
          <n-input v-model:value="form.email" />
        </n-form-item>
        <n-form-item label="手机号">
          <n-input v-model:value="form.phone" />
        </n-form-item>
        <n-form-item label="角色">
          <n-select
            v-model:value="form.role_id"
            placeholder="请选择"
            :options="roleOptions()"
          />
        </n-form-item>
        <n-form-item label="状态">
          <n-switch v-model:value="form.is_active" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="dialogVisible = false">
            取消
          </n-button>
          <n-button
            type="primary"
            @click="handleSubmit"
          >
            提交
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>
