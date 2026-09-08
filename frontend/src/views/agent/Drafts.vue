<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NCard, NDataTable, NEmpty, NIcon, NSelect, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { listMessages, approveMessage, markSent, markNoReply, draftOne, type OutreachMessage } from '@/api/agent'
import { confirm } from '@/utils/feedback'

const msg = useMessage()

interface RowMessage extends OutreachMessage {
  lead_name: string
  contact_name: string
}

const items = ref<RowMessage[]>([])
const total = ref(0)
const loading = ref(false)
const filterStatus = ref<string>('')
const filterChannel = ref<string>('')

// sentinel: '' = 全部（不过滤）；实际值传给后端时跳过空串
const statusOptions = [
  { label: '全部', value: '' },
  { label: '草稿', value: 'draft' },
  { label: '已审批', value: 'approved' },
  { label: '已发送', value: 'sent' },
  { label: '已回复', value: 'replied' },
  { label: '无回复', value: 'no_reply' },
  { label: '退信', value: 'bounced' },
  { label: '成交', value: 'won' },
  { label: '输单', value: 'lost' },
]

const channelOptions = [
  { label: '全部通道', value: '' },
  { label: '邮件', value: 'email' },
  { label: 'WhatsApp', value: 'whatsapp' },
  { label: 'LinkedIn', value: 'linkedin' },
]

async function load() {
  loading.value = true
  try {
    const r = await listMessages({
      page: 1,
      page_size: 50,
      ...(filterStatus.value ? { status: filterStatus.value } : {}),
      ...(filterChannel.value ? { channel: filterChannel.value } : {}),
    })
    items.value = r.items
    total.value = r.total
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function approve(id: number) {
  try {
    await approveMessage(id)
    msg.success('已审批（字段已锁）')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '审批失败')
  }
}

async function send(id: number) {
  try {
    await markSent(id)
    msg.success('已标记发送')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '发送失败')
  }
}

async function noReply(id: number) {
  const ok = await confirm({
    title: '标记无回复',
    content: '标记为无回复？销售可继续推进 CRM 状态。',
  })
  if (!ok) return
  try {
    await markNoReply(id)
    msg.success('已标记无回复')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '操作失败')
  }
}

const statusColor: Record<string, string> = {
  draft: 'default',
  approved: 'info',
  sent: 'warning',
  replied: 'success',
  no_reply: 'default',
  bounced: 'error',
  won: 'success',
  lost: 'error',
}

const columns = computed<DataTableColumns<RowMessage>>(() => [
  { title: '企业', key: 'lead_name', width: 160 },
  { title: '通道', key: 'channel', width: 80, render: (r) => h(NTag, { size: 'small' }, { default: () => r.channel }) },
  {
    title: '主题 / 摘要',
    key: 'subject',
    render: (r) =>
      h('div', { style: 'max-width: 360px' }, [
        h('div', { style: 'font-weight: 600' }, r.subject || '(无主题)'),
        h(
          'div',
          {
            style:
              'color:#888; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis',
          },
          r.body.slice(0, 80),
        ),
      ]),
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (r) =>
      h(
        NTag,
        { type: (statusColor[r.status] ?? 'default') as 'default' | 'success' | 'info' | 'warning' | 'error', size: 'small' },
        { default: () => r.status },
      ),
  },
  {
    title: '生成方式',
    key: 'generated_by',
    width: 90,
    render: (r) =>
      h(
        NTag,
        { size: 'small', type: r.llm_generated ? 'success' : 'default' },
        { default: () => r.generated_by },
      ),
  },
  { title: '联系人', key: 'contact_name', width: 120 },
  {
    title: '操作',
    key: 'op',
    width: 280,
    render: (r) => {
      const buttons: ReturnType<typeof h>[] = []
      if (r.status === 'draft') {
        buttons.push(
          h(
            NButton,
            { size: 'small', type: 'primary', onClick: () => approve(r.id) },
            { default: () => '审批' },
          ),
        )
      }
      if (r.status === 'approved') {
        buttons.push(
          h(
            NButton,
            { size: 'small', type: 'success', onClick: () => send(r.id) },
            { default: () => '标记已发' },
          ),
        )
      }
      if (r.status === 'sent') {
        buttons.push(
          h(NButton, { size: 'small', onClick: () => noReply(r.id) }, { default: () => '标记无回复' }),
        )
      }
      if (r.lead_id && r.status === 'draft' && !r.sequence_id) {
        buttons.push(
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              onClick: async () => {
                try {
                  await draftOne({ lead_id: r.lead_id, channel: r.channel })
                  msg.success('已重新起草')
                  load()
                } catch (e: unknown) {
                  msg.error((e as Error)?.message ?? '失败')
                }
              },
            },
            { default: () => '重写' },
          ),
        )
      }
      return h(NSpace, { size: 4 }, { default: () => buttons })
    },
  },
])
</script>

<template>
  <NCard title="草稿箱 / 已发外联">
    <template #header-extra>
      <NSpace>
        <NSelect
          v-model:value="filterStatus"
          :options="statusOptions"
          size="small"
          style="width: 130px"
          @update:value="load"
        />
        <NSelect
          v-model:value="filterChannel"
          :options="channelOptions"
          size="small"
          style="width: 130px"
          @update:value="load"
        />
      </NSpace>
    </template>
    <NDataTable :columns="columns" :data="items" :loading="loading" :bordered="false" :row-key="(r) => r.id" size="small" />
    <NEmpty v-if="!loading && items.length === 0" description="没有符合条件的外联" />
  </NCard>
</template>
