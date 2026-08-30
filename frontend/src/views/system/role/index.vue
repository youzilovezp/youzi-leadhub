<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, type DataTableColumns, type FormInst, type FormRules } from 'naive-ui'
import * as roleApi from '@/api/role'
import type { Role } from '@/api/role'
import { formatTime } from '@/utils/format'
import { message, confirm } from '@/utils/feedback'

const loading = ref(false)
const tableData = ref<Role[]>([])

const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const formRef = ref<FormInst>()
const formRules: FormRules = {
  name: [{ required: true, message: '请输入角色名', trigger: 'blur' }],
  code: [{ required: true, message: '请输入角色编码', trigger: 'blur' }],
}
const form = reactive({
  id: 0,
  name: '',
  code: '',
  remark: '',
})

const columns: DataTableColumns<Role> = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '角色名', key: 'name' },
  { title: '角色编码', key: 'code' },
  { title: '备注', key: 'remark', ellipsis: { tooltip: true } },
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
    tableData.value = await roleApi.listRoles()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  dialogMode.value = 'create'
  Object.assign(form, { id: 0, name: '', code: '', remark: '' })
  dialogVisible.value = true
}

function openEdit(row: Role) {
  dialogMode.value = 'edit'
  // 显式 pick 字段，避免 spread 把 created_at 也注入 form
  Object.assign(form, {
    id: row.id,
    name: row.name,
    code: row.code,
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!formRef.value) return
  // naive validate() 失败 reject——catch false，校验不过不发请求
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  if (dialogMode.value === 'create') {
    await roleApi.createRole({ name: form.name, code: form.code, remark: form.remark })
    message.success('创建成功')
  } else {
    await roleApi.updateRole(form.id, { name: form.name, code: form.code, remark: form.remark })
    message.success('更新成功')
  }
  dialogVisible.value = false
  fetchData()
}

async function handleDelete(row: Role) {
  if (!(await confirm({ title: '提示', content: `确定删除角色「${row.name}」吗？`, positiveText: '删除' })))
    return
  await roleApi.deleteRole(row.id)
  message.success('已删除')
  fetchData()
}

onMounted(fetchData)
</script>

<template>
  <div class="page">
    <div class="mb-4 flex justify-end">
      <n-button
        type="primary"
        @click="openCreate"
      >
        新增角色
      </n-button>
    </div>

    <n-data-table
      :columns="columns"
      :data="tableData"
      :loading="loading"
      :row-key="(row: Role) => row.id"
    />

    <n-modal
      v-model:show="dialogVisible"
      preset="card"
      :title="dialogMode === 'create' ? '新增角色' : '编辑角色'"
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
          label="角色名"
          path="name"
        >
          <n-input v-model:value="form.name" />
        </n-form-item>
        <n-form-item
          label="角色编码"
          path="code"
        >
          <n-input
            v-model:value="form.code"
            :disabled="dialogMode === 'edit'"
          />
        </n-form-item>
        <n-form-item label="备注">
          <n-input
            v-model:value="form.remark"
            type="textarea"
            :rows="3"
          />
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
