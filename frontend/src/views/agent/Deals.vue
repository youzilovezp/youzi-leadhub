<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NEmpty,
  NGrid,
  NGridItem,
  NIcon,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NStatistic,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { AddOutline, CameraOutline } from '@vicons/ionicons5'
import {
  createDeal,
  deleteDeal,
  getForecastSummary,
  listDeals,
  takeSnapshot,
  updateDeal,
  type ForecastDeal,
  type ForecastSummary,
} from '@/api/agent'

const msg = useMessage()

interface RowDeal extends ForecastDeal {
  lead_name: string
}

const items = ref<RowDeal[]>([])
const total = ref(0)
const loading = ref(false)
const summary = ref<ForecastSummary | null>(null)

const filterStage = ref<string>('')
const filterOpen = ref<boolean | null>(null)

const stageOptions = [
  { label: '全部阶段', value: '' },
  { label: '待跟进', value: 'pending' },
  { label: '已联系', value: 'contacted' },
  { label: '已回复', value: 'replied' },
  { label: '有效商机', value: 'opportunity' },
  { label: '报价', value: 'quote' },
  { label: '谈判', value: 'negotiation' },
  { label: '成交', value: 'won' },
  { label: '输单', value: 'lost' },
]

const stageOnlyOptions = stageOptions.filter((o) => o.value)

const stageColor: Record<string, string> = {
  pending: 'default',
  contacted: 'info',
  replied: 'info',
  opportunity: 'success',
  quote: 'warning',
  negotiation: 'warning',
  won: 'success',
  lost: 'error',
}

async function load() {
  loading.value = true
  try {
    const [r, s] = await Promise.all([
      listDeals({
        page: 1,
        page_size: 50,
        ...(filterStage.value ? { stage: filterStage.value } : {}),
        ...(filterOpen.value !== null ? { is_open: filterOpen.value as boolean } : {}),
      }),
      getForecastSummary(),
    ])
    items.value = r.items
    total.value = r.total
    summary.value = s
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function snapshot(period: 'weekly' | 'monthly') {
  try {
    await takeSnapshot(period)
    msg.success(`${period} 快照已记录`)
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '快照失败')
  }
}

async function remove(id: number) {
  try {
    await deleteDeal(id)
    msg.success('已删除')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '删除失败')
  }
}

async function setStage(deal: RowDeal, stage: string | null) {
  if (!stage) return
  try {
    await updateDeal(deal.id, { stage })
    msg.success('阶段已更新')
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '更新失败')
  }
}

// ---- 新建商机 ----
const showCreate = ref(false)
const form = ref({
  lead_id: null as number | null,
  name: '',
  stage: 'opportunity',
  amount: 0,
  probability: null as number | null,
  is_primary: true,
})

async function submit() {
  if (!form.value.lead_id || !form.value.name) {
    msg.warning('请填线索 ID + 商机名')
    return
  }
  try {
    await createDeal(form.value as unknown as Parameters<typeof createDeal>[0])
    msg.success('已创建')
    showCreate.value = false
    load()
  } catch (e: unknown) {
    msg.error((e as Error)?.message ?? '创建失败')
  }
}

const columns = computed<DataTableColumns<RowDeal>>(() => [
  { title: '商机', key: 'name', width: 200 },
  { title: '企业', key: 'lead_name', width: 140 },
  {
    title: '阶段',
    key: 'stage',
    width: 130,
    render: (r) =>
      h(
        NSelect,
        {
          size: 'small',
          value: r.stage,
          options: stageOnlyOptions,
          onUpdateValue: (v: string) => setStage(r, v),
        },
      ),
  },
  {
    title: '金额',
    key: 'amount',
    width: 110,
    render: (r) => `¥${Number(r.amount).toLocaleString()}`,
  },
  { title: '概率', key: 'probability', width: 80, render: (r) => `${r.probability}%` },
  {
    title: '加权金额',
    key: 'weighted_amount',
    width: 120,
    render: (r) =>
      h(
        'span',
        { style: 'color:#18a058; font-weight:600' },
        `¥${Number(r.weighted_amount).toLocaleString()}`,
      ),
  },
  {
    title: '预计成交',
    key: 'close_date',
    width: 110,
    render: (r) => r.close_date?.slice(0, 10) ?? '-',
  },
  {
    title: '主商机',
    key: 'is_primary',
    width: 80,
    render: (r) =>
      r.is_primary ? h(NTag, { size: 'small', type: 'success' }, { default: () => '主' }) : '-',
  },
  {
    title: '操作',
    key: 'op',
    width: 90,
    render: (r) =>
      h(
        NButton,
        { size: 'small', quaternary: true, type: 'error', onClick: () => remove(r.id) },
        { default: () => '删除' },
      ),
  },
])

// 派生看板数据（按阶段排序展示）
const stageSummary = computed(() => {
  if (!summary.value) return []
  return Object.entries(summary.value.by_stage)
    .map(([stage, v]) => ({ stage, ...v }))
    .sort((a, b) => b.weighted - a.weighted)
})
</script>

<template>
  <NGrid :cols="4" :x-gap="12" :y-gap="12" style="margin-bottom: 12px">
    <NGridItem>
      <NCard size="small">
        <NStatistic label="加权总额" :value="summary?.weighted_total ?? 0">
          <template #prefix>¥</template>
        </NStatistic>
      </NCard>
    </NGridItem>
    <NGridItem>
      <NCard size="small">
        <NStatistic label="开口加权" :value="summary?.open_weighted ?? 0">
          <template #prefix>¥</template>
        </NStatistic>
      </NCard>
    </NGridItem>
    <NGridItem>
      <NCard size="small">
        <NStatistic label="商机数" :value="summary?.deal_count ?? 0" />
      </NCard>
    </NGridItem>
    <NGridItem>
      <NCard size="small">
        <NStatistic label="开口商机" :value="summary?.open_deal_count ?? 0" />
      </NCard>
    </NGridItem>
  </NGrid>

  <NCard title="按阶段分布" size="small" style="margin-bottom: 12px">
    <NSpace>
      <div v-for="s in stageSummary" :key="s.stage" style="text-align: center">
        <NTag :type="(stageColor[s.stage] ?? 'default') as 'default' | 'success' | 'info' | 'warning' | 'error'" size="medium">
          {{ s.stage }}
        </NTag>
        <div style="font-size: 20px; font-weight: 600; color: #18a058; margin-top: 4px">
          ¥{{ Number(s.weighted).toLocaleString() }}
        </div>
        <div style="font-size: 12px; color: #888">
          {{ s.count }} 个 · 总 ¥{{ Number(s.amount).toLocaleString() }}
        </div>
      </div>
      <NEmpty v-if="stageSummary.length === 0" description="还没有商机" />
    </NSpace>
  </NCard>

  <NCard title="商机预测">
    <template #header-extra>
      <NSpace>
        <NSelect
          v-model:value="filterStage"
          :options="stageOptions"
          size="small"
          style="width: 130px"
          @update:value="load"
        />
        <NButton
          size="small"
          :type="filterOpen === true ? 'warning' : 'default'"
          @click="
            () => {
              filterOpen = filterOpen === true ? null : true
              load()
            }
          "
        >
          仅开口
        </NButton>
        <NButton size="small" @click="snapshot('weekly')">
          <template #icon><NIcon><CameraOutline /></NIcon></template>
          周快照
        </NButton>
        <NButton size="small" @click="snapshot('monthly')">
          <template #icon><NIcon><CameraOutline /></NIcon></template>
          月快照
        </NButton>
        <NButton type="primary" size="small" @click="showCreate = true">
          <template #icon><NIcon><AddOutline /></NIcon></template>
          新建商机
        </NButton>
      </NSpace>
    </template>

    <NDataTable
      :columns="columns"
      :data="items"
      :loading="loading"
      :bordered="false"
      :row-key="(r) => r.id"
      size="small"
    />
    <NEmpty v-if="!loading && items.length === 0" description="还没有商机；CRM 流里 lead 进入 opportunity/quote 后会自动建占位" />
  </NCard>

  <NModal v-model:show="showCreate" preset="card" title="新建商机" style="width: 500px">
    <NSpace vertical>
      <NInputNumber v-model:value="form.lead_id" :min="1" placeholder="线索 ID" />
      <NInputNumber v-model:value="form.amount" :min="0" placeholder="金额（¥）" />
      <NSelect v-model:value="form.stage" :options="stageOnlyOptions" />
      <NInputNumber v-model:value="form.probability" :min="0" :max="100" placeholder="概率（%，留空按阶段默认）" />
    </NSpace>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="showCreate = false">取消</NButton>
        <NButton type="primary" @click="submit">创建</NButton>
      </NSpace>
    </template>
  </NModal>
</template>
