<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NCard, NEmpty, NIcon, NInput, NSpace, NTag, NDataTable, NPopconfirm, NModal, NForm, NFormItem, NInputNumber, NSelect, useMessage, type DataTableColumns } from 'naive-ui'
import { AddOutline, TrashOutline, RocketOutline } from '@vicons/ionicons5'
import { listSequences, createSequence, deleteSequence, type OutreachSequence, type OutreachStep } from '@/api/agent'

const msg = useMessage()
const items = ref<OutreachSequence[]>([])
const total = ref(0)
const loading = ref(false)
const showCreate = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await listSequences({ page: 1, page_size: 50 })
    items.value = r.items
    total.value = r.total
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ---- 新建表单 ----
const form = ref({
  name: '',
  description: '',
  scenario: 'first_touch',
  steps: [
    { day_offset: 0, channel: 'email', template_hint: '首触' },
    { day_offset: 3, channel: 'email', template_hint: '跟进·案例' },
    { day_offset: 7, channel: 'whatsapp', template_hint: '价值重申' },
  ] as OutreachStep[],
})

const channelOptions = [
  { label: '邮件', value: 'email' },
  { label: 'WhatsApp', value: 'whatsapp' },
  { label: 'LinkedIn', value: 'linkedin' },
  { label: '短信', value: 'sms' },
]

const scenarioOptions = [
  { label: '首触', value: 'first_touch' },
  { label: '唤醒', value: 'wake' },
  { label: '续约', value: 'renew' },
]

function addStep() {
  const last = form.value.steps.at(-1)
  form.value.steps.push({
    day_offset: (last?.day_offset ?? 0) + 3,
    channel: 'email',
    template_hint: '',
  })
}
function removeStep(i: number) {
  form.value.steps.splice(i, 1)
}

async function submit() {
  if (!form.value.name || form.value.steps.length === 0) {
    msg.warning('请填名称 + 至少一步')
    return
  }
  try {
    await createSequence(form.value)
    msg.success('已创建')
    showCreate.value = false
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '创建失败')
  }
}

async function remove(id: number) {
  try {
    await deleteSequence(id)
    msg.success('已删除')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '删除失败')
  }
}

const columns = computed<DataTableColumns<OutreachSequence>>(() => [
  { title: '名称', key: 'name', width: 180 },
  {
    title: '步骤',
    key: 'steps',
    render: (row) =>
      h(
        NSpace,
        { size: 4 },
        {
          default: () =>
            (row.steps ?? []).map((s, i) =>
              h(
                NTag,
                { size: 'small', type: i === 0 ? 'success' : 'default' },
                { default: () => `T+${s.day_offset} ${s.channel}` },
              ),
            ),
        },
      ),
  },
  { title: '场景', key: 'scenario', width: 100 },
  {
    title: '状态',
    key: 'active',
    width: 80,
    render: (row) =>
      h(
        NTag,
        { type: row.active ? 'success' : 'default', size: 'small' },
        { default: () => (row.active ? '启用' : '停用') },
      ),
  },
  {
    title: '操作',
    key: 'op',
    width: 100,
    render: (row) =>
      h(
        NPopconfirm,
        { onPositiveClick: () => remove(row.id) },
        {
          trigger: () =>
            h(NButton, { size: 'small', quaternary: true, type: 'error' }, { default: () => '删除' }),
          default: () => '确认删除？已排程的草稿不会被删除。',
        },
      ),
  },
])
</script>

<template>
  <NCard title="外联序列（多步节奏模板）">
    <template #header-extra>
      <NButton type="primary" @click="showCreate = true">
        <template #icon><NIcon><AddOutline /></NIcon></template>
        新建序列
      </NButton>
    </template>
    <NDataTable
      :columns="columns"
      :data="items"
      :loading="loading"
      :bordered="false"
      :row-key="(r) => r.id"
      size="small"
    />
    <NEmpty v-if="!loading && items.length === 0" description="还没有序列，先建一个吧" />
  </NCard>

  <NModal v-model:show="showCreate" preset="card" title="新建外联序列" style="width: 600px">
    <NForm :model="form" label-placement="left" label-width="80">
      <NFormItem label="名称" required>
        <NInput v-model:value="form.name" placeholder="如 跨境电商·首触" />
      </NFormItem>
      <NFormItem label="场景">
        <NSelect v-model:value="form.scenario" :options="scenarioOptions" />
      </NFormItem>
      <NFormItem label="步骤">
        <NSpace vertical style="width: 100%">
          <div v-for="(s, i) in form.steps" :key="i" style="display: flex; gap: 8px">
            <NInputNumber v-model:value="s.day_offset" :min="0" :max="180" placeholder="T+N 天" style="width: 100px" />
            <NSelect v-model:value="s.channel" :options="channelOptions" style="width: 140px" />
            <NInput v-model:value="s.template_hint" placeholder="提示（如：首触·痛点）" style="flex: 1" />
            <NButton quaternary type="error" @click="removeStep(i)">
              <template #icon><NIcon><TrashOutline /></NIcon></template>
            </NButton>
          </div>
          <NButton @click="addStep" dashed>
            <template #icon><NIcon><AddOutline /></NIcon></template>
            添加一步
          </NButton>
        </NSpace>
      </NFormItem>
    </NForm>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="showCreate = false">取消</NButton>
        <NButton type="primary" @click="submit">
          <template #icon><NIcon><RocketOutline /></NIcon></template>
          创建
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>
