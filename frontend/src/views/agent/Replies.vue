<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NCard, NDataTable, NEmpty, NIcon, NSelect, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { RefreshCircleOutline } from '@vicons/ionicons5'
import { listReplies, overrideReplyLabel, syncReplyCRM, type Reply } from '@/api/agent'

const msg = useMessage()

interface RowReply extends Reply {
  lead_name: string
}

const items = ref<RowReply[]>([])
const total = ref(0)
const loading = ref(false)
const filterIntent = ref<string>('')
const filterSentiment = ref<string>('')
const filterUnprocessed = ref(false)

const intentOptions = [
  { label: '全部意图', value: '' },
  { label: '有兴趣', value: 'interested' },
  { label: '购买信号', value: 'buy_signal' },
  { label: '提问', value: 'question' },
  { label: '异议', value: 'objection' },
  { label: '退订', value: 'unsubscribe' },
  { label: '非对接人', value: 'wrong_person' },
  { label: '外出/自动', value: 'out_of_office' },
  { label: '其他', value: 'other' },
]

const intentOnlyOptions = intentOptions.filter((o) => o.value)

const sentimentOptions = [
  { label: '全部情感', value: '' },
  { label: '正向', value: 'positive' },
  { label: '中性', value: 'neutral' },
  { label: '负向', value: 'negative' },
]

const intentColor: Record<string, string> = {
  interested: 'success',
  buy_signal: 'warning',
  question: 'info',
  objection: 'error',
  unsubscribe: 'default',
  wrong_person: 'warning',
  out_of_office: 'default',
  other: 'default',
}

const sentimentColor: Record<string, string> = {
  positive: 'success',
  neutral: 'default',
  negative: 'error',
}

async function load() {
  loading.value = true
  try {
    const r = await listReplies({
      page: 1,
      page_size: 50,
      ...(filterIntent.value ? { intent: filterIntent.value } : {}),
      ...(filterSentiment.value ? { sentiment: filterSentiment.value } : {}),
      unprocessed_only: filterUnprocessed.value,
    })
    items.value = r.items
    total.value = r.total
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function reprocess(id: number) {
  try {
    await syncReplyCRM({ reply_id: id })
    msg.success('CRM 同步已重跑')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '同步失败')
  }
}

async function overrideIntent(id: number, intent: string) {
  try {
    await overrideReplyLabel(id, { intent })
    msg.success('已覆盖标注（CRM 同步会跟着跑）')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '覆盖失败')
  }
}

const columns = computed<DataTableColumns<RowReply>>(() => [
  { title: '企业', key: 'lead_name', width: 140 },
  {
    title: '情感',
    key: 'sentiment',
    width: 80,
    render: (r) =>
      r.sentiment
        ? h(
            NTag,
            {
              size: 'small',
              type: (sentimentColor[r.sentiment] ?? 'default') as 'default' | 'success' | 'error',
            },
            { default: () => r.sentiment },
          )
        : '-',
  },
  {
    title: '意图',
    key: 'intent',
    width: 110,
    render: (r) =>
      r.intent
        ? h(
            NTag,
            {
              size: 'small',
              type: (intentColor[r.intent] ?? 'default') as
                | 'default'
                | 'success'
                | 'info'
                | 'warning'
                | 'error',
              title: r.overridden ? '人工覆盖' : 'LLM',
            },
            { default: () => r.intent },
          )
        : '-',
  },
  {
    title: '摘要',
    key: 'summary',
    render: (r) =>
      h('div', { style: 'max-width: 380px' }, [
        r.summary ? h('div', { style: 'color: #18a058; font-weight: 600' }, r.summary) : null,
        h(
          'div',
          {
            style:
              'font-size:12px; color:#666; white-space:nowrap; overflow:hidden; text-overflow:ellipsis',
          },
          r.body.slice(0, 100),
        ),
      ]),
  },
  {
    title: 'CRM 处理',
    key: 'crm_action',
    width: 200,
    render: (r) =>
      r.processed_at
        ? h('span', { style: 'color:#18a058; font-size:12px' }, r.crm_action || '已处理')
        : h(NTag, { size: 'small', type: 'warning' }, { default: () => '待处理' }),
  },
  {
    title: '操作',
    key: 'op',
    width: 240,
    render: (r) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(
            NSelect,
            {
              size: 'small',
              style: 'width: 130px',
              value: r.intent,
              options: intentOnlyOptions,
              placeholder: '覆盖意图',
              onUpdateValue: (v: string) => overrideIntent(r.id, v),
            },
          ),
          h(
            NButton,
            { size: 'small', onClick: () => reprocess(r.id) },
            { default: () => '重跑 CRM' },
          ),
        ],
      }),
  },
])
</script>

<template>
  <NCard title="回复收件箱（自动 LLM 标注 → 自动 CRM 同步）">
    <template #header-extra>
      <NSpace>
        <NSelect v-model:value="filterIntent" :options="intentOptions" size="small" style="width: 140px" @update:value="load" />
        <NSelect v-model:value="filterSentiment" :options="sentimentOptions" size="small" style="width: 130px" @update:value="load" />
        <NButton
          size="small"
          :type="filterUnprocessed ? 'warning' : 'default'"
          @click="
            () => {
              filterUnprocessed = !filterUnprocessed
              load()
            }
          "
        >
          <template #icon><NIcon><RefreshCircleOutline /></NIcon></template>
          仅待处理
        </NButton>
      </NSpace>
    </template>
    <NDataTable :columns="columns" :data="items" :loading="loading" :bordered="false" :row-key="(r) => r.id" size="small" />
    <NEmpty v-if="!loading && items.length === 0" description="没有符合条件的回复" />
  </NCard>
</template>
