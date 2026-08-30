<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import * as salesApi from '@/api/sales'
import type { SalesMessage } from '@/api/sales'
import { MESSAGE_STATUS_LABELS, messageStatusTagType } from '@/api/sales'
import { useUserStore } from '@/stores/user'
import { formatTime } from '@/utils/format'
import { confirm, message } from '@/utils/feedback'

const router = useRouter()
const userStore = useUserStore()
const items = ref<SalesMessage[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const query = reactive({ status: null as string | null })

/** 话术预览弹窗（复制发送入口） */
const preview = ref<SalesMessage | null>(null)

async function fetchList() {
  loading.value = true
  try {
    const data = await salesApi.listMessages({
      page: page.value,
      page_size: pageSize,
      status: query.status || undefined,
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function handlePageChange(p: number) {
  page.value = p
  fetchList()
}

async function act(msg: SalesMessage, action: 'approve' | 'reject' | 'mark_sent') {
  const labels = { approve: '通过', reject: '驳回', mark_sent: '标记已发送' } as const
  if (action === 'reject' && !(await confirm({ title: '提示', content: '驳回这条话术？', positiveText: '驳回' })))
    return
  await salesApi.reviewMessage(msg.id, action)
  message.success(`已${labels[action]}`)
  fetchList()
}

async function copyContent(msg: SalesMessage) {
  try {
    await navigator.clipboard.writeText(msg.content)
    message.success('话术已复制，去 WhatsApp 粘贴发送；发送后回来点「标记已发送」')
  } catch {
    preview.value = msg // 剪贴板不可用（http 环境）→ 弹窗手动复制
  }
}

const columns: DataTableColumns<SalesMessage> = [
  { title: '时间', key: 'created_at', width: 150, render: (r) => formatTime(r.created_at) },
  {
    title: '企业',
    key: 'lead_name',
    minWidth: 160,
    render: (r) =>
      h(
        NButton,
        { size: 'small', quaternary: true, type: 'primary', onClick: () => router.push(`/collect/lead/${r.lead_id}`) },
        () => r.lead_name || `#${r.lead_id}`,
      ),
  },
  {
    title: '内容',
    key: 'content',
    ellipsis: { tooltip: true },
    render: (r) => r.content.slice(0, 60) + (r.content.length > 60 ? '…' : ''),
  },
  {
    title: '来源',
    key: 'generated_by',
    width: 80,
    render: (r) => h(NTag, { size: 'small', bordered: false }, () => (r.generated_by === 'llm' ? 'AI' : '模板')),
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (r) => h(NTag, { size: 'small', type: messageStatusTagType(r.status) }, () => MESSAGE_STATUS_LABELS[r.status] ?? r.status),
  },
  {
    title: '操作',
    key: 'actions',
    width: 230,
    render: (r) => {
      const buttons = []
      if (r.status === 'draft') {
        buttons.push(h(NButton, { size: 'tiny', type: 'primary', secondary: true, onClick: () => act(r, 'approve') }, () => '通过'))
        buttons.push(h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => act(r, 'reject') }, () => '驳回'))
      }
      if (r.status === 'approved') {
        buttons.push(h(NButton, { size: 'tiny', type: 'success', secondary: true, onClick: () => copyContent(r) }, () => '复制发送'))
        buttons.push(h(NButton, { size: 'tiny', quaternary: true, type: 'success', onClick: () => act(r, 'mark_sent') }, () => '标记已发'))
      }
      buttons.push(h(NButton, { size: 'tiny', quaternary: true, onClick: () => (preview.value = r) }, () => '查看'))
      return h('div', { class: 'flex gap-1' }, buttons)
    },
  },
]

onMounted(fetchList)
</script>

<template>
  <div class="page">
    <n-card
      size="small"
      class="mb-4"
    >
      <div class="flex flex-wrap items-center gap-3">
        <h2 class="page-title">
          话术审核队列
        </h2>
        <span class="hint">生成（AI/模板）→ 审核通过 → 复制发送 → 标记已发（不自动外发，销售把关）</span>
        <div class="flex-1" />
        <n-select
          v-model:value="query.status"
          :options="[
            { label: '待审核', value: 'draft' },
            { label: '已通过', value: 'approved' },
            { label: '已发送', value: 'sent' },
            { label: '已驳回', value: 'rejected' },
          ]"
          placeholder="状态"
          clearable
          style="width: 120px"
          @update:value="() => { page = 1; fetchList() }"
        />
      </div>
    </n-card>
    <n-data-table
      remote
      :columns="columns"
      :data="items"
      :loading="loading"
      :row-key="(r: SalesMessage) => r.id"
      :pagination="{ page, pageSize, itemCount: total, onChange: handlePageChange }"
    />

    <!-- 话术全文预览 -->
    <n-modal
      :show="!!preview"
      preset="card"
      :title="`话术 · ${preview?.lead_name ?? ''}`"
      style="width: 560px"
      @update:show="(v: boolean) => !v && (preview = null)"
    >
      <div class="script-body">
        {{ preview?.content }}
      </div>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button @click="preview = null">
            关闭
          </n-button>
          <n-button
            v-if="preview"
            type="primary"
            @click="copyContent(preview)"
          >
            复制
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.page-title {
  font-size: 16px;
  margin: 0;
}
.hint {
  font-size: 12px;
  color: var(--yz-text-secondary, #888);
}
.script-body {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.8;
}
</style>
