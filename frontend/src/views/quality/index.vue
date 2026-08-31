<script setup lang="ts">
// 质量抽检中心（§十二 验证闭环）：人工核验 → 准确率指标 vs 目标线
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NEmpty,
  NGi,
  NGrid,
  NInput,
  NProgress,
  NSelect,
  NSpace,
  NTag,
} from 'naive-ui'
import {
  REVIEW_FIELD_LABELS,
  getQualityStats,
  getReviewQueue,
  submitReview,
} from '@/api/quality'
import type { QueueItem, QualityStats, ReviewField } from '@/api/quality'
import { message } from '@/utils/feedback'

const router = useRouter()
const field = ref<ReviewField>('whatsapp')
const items = ref<QueueItem[]>([])
const stats = ref<QualityStats | null>(null)
const loading = ref(false)
const notes = ref<Record<number, string>>({})

const fieldOptions = (['whatsapp', 'overseas', 'contact'] as ReviewField[]).map(f => ({
  label: REVIEW_FIELD_LABELS[f],
  value: f,
}))

async function fetchQueue() {
  loading.value = true
  try {
    items.value = (await getReviewQueue(field.value, 10)).items
  } finally {
    loading.value = false
  }
}

async function fetchStats() {
  stats.value = await getQualityStats()
}

async function refreshAll() {
  await Promise.all([fetchQueue(), fetchStats()])
}

async function mark(item: QueueItem, verdict: 'correct' | 'incorrect' | 'unsure') {
  await submitReview({
    lead_id: item.lead_id,
    field: field.value,
    verdict,
    note: notes.value[item.lead_id] || undefined,
  })
  message.success('已记录，下一家')
  items.value = items.value.filter(i => i.lead_id !== item.lead_id)
  fetchStats()
}

/** 证据区展示：按维度拼核验要点 */
function evidenceLines(item: QueueItem): string[] {
  const e = item.evidence
  const out: string[] = []
  if (e.website) out.push(`官网：${e.website}`)
  if (e.whatsapp_url) out.push(`WA 入口：${e.whatsapp_url}`)
  if (e.whatsapp_numbers?.length) out.push(`WA 号码：${e.whatsapp_numbers.join('、')}`)
  if (e.target_countries?.length) out.push(`投放国家：${e.target_countries.join('、')}`)
  for (const [k, v] of Object.entries(e.overseas_signals ?? {})) {
    if (v.length) out.push(`出海证据 ${k}：${v.join('、')}`)
  }
  for (const c of e.contacts ?? []) {
    out.push(`联系人：${c.email ?? ''}${c.phone ? ` / ${c.phone}` : ''}${c.job_title ? `（${c.job_title}）` : ''}`)
  }
  return out
}

onMounted(refreshAll)
</script>

<template>
  <div class="p-4">
    <!-- 抽检说明：核验系统判断是否属实，准确率不达标说明采集规则要调 -->
    <n-alert
      type="info"
      title="这个页面做什么"
      class="mb-4"
    >
      <ul style="margin: 0; padding-left: 18px; line-height: 1.9">
        <li>
          <b>抽查系统判断得对不对</b>：每行展示系统判断的依据（官网、WhatsApp 入口、出海证据等），
          核实后标「正确 / 错误 / 无法判定」。三个维度各有一条准确率线：WhatsApp 识别 ≥90%、
          出海判定 ≥80%、联系方式 ≥60%。
        </li>
        <li>
          <b>优先推重要的</b>：S/A 级、带 WhatsApp 证据、联系人待补全的线索排前面。
          同一条可以改判，统计按每个人最新一次算。
        </li>
        <li>
          <b>准确率掉了怎么办</b>：说明采集环节的关键词或识别规则该收紧了——准确率长期低于目标线，
          优先去调采集任务的用词，而不是继续抽查。
        </li>
        <li><b>S+A 占比 ≥20%</b>：高分商机浓度。不够通常说明广告库没跑够量。</li>
      </ul>
    </n-alert>

    <n-card
      :bordered="false"
      class="mb-4"
    >
      <div class="flex items-center gap-3 flex-wrap">
        <span style="font-size: 18px; font-weight: 600">🧪 质量抽检中心</span>
        <n-tag
          v-if="stats"
          :bordered="false"
          type="info"
        >
          S+A 占比 {{ (stats.sa_ratio.value * 100).toFixed(1) }}% / 目标 20%
        </n-tag>
        <span
          v-if="stats"
          style="color: #999; font-size: 12px"
        >{{ stats.note }}</span>
        <div class="flex-1" />
        <n-button
          quaternary
          :loading="loading"
          @click="refreshAll"
        >
          刷新
        </n-button>
      </div>
    </n-card>

    <!-- 指标卡：三准确率 vs 目标线 -->
    <n-grid
      v-if="stats"
      :x-gap="12"
      :y-gap="12"
      :cols="3"
      class="mb-4"
      responsive="screen"
      item-responsive
    >
      <n-gi
        v-for="(f, key) in stats.fields"
        :key="key"
        span="3 m:1"
      >
        <n-card
          size="small"
          :bordered="false"
        >
          <div class="flex items-center justify-between mb-2">
            <span style="font-weight: 600">{{ f.label }}</span>
            <n-tag
              size="small"
              :type="f.meets_target === null ? 'default' : f.meets_target ? 'success' : 'error'"
              :bordered="false"
            >
              目标 ≥{{ (f.target * 100).toFixed(0) }}%
            </n-tag>
          </div>
          <div style="font-size: 26px; font-weight: 700; line-height: 1.2">
            {{ f.accuracy === null ? '—' : `${(f.accuracy * 100).toFixed(1)}%` }}
          </div>
          <n-progress
            type="line"
            :percentage="(f.accuracy ?? 0) * 100"
            :status="f.meets_target === false ? 'error' : 'success'"
            :show-indicator="false"
            style="margin-top: 6px"
          />
          <div style="color: #999; font-size: 12px; margin-top: 6px">
            已检 {{ f.reviewed }}（对 {{ f.correct }} / 错 {{ f.incorrect }} / 存疑 {{ f.unsure }}）·
            待检池 {{ stats.coverage[key as ReviewField]?.pool ?? '—' }}
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 抽检队列 -->
    <n-card :bordered="false">
      <template #header>
        <n-space align="center">
          <span>抽检队列</span>
          <n-select
            v-model:value="field"
            :options="fieldOptions"
            size="small"
            style="width: 150px"
            @update:value="fetchQueue"
          />
        </n-space>
      </template>
      <n-empty
        v-if="!items.length && !loading"
        description="该维度暂无可抽检线索（或已全部检完）"
      />
      <n-space
        v-else
        vertical
        size="large"
      >
        <n-card
          v-for="item in items"
          :key="item.lead_id"
          size="small"
          bordered
        >
          <div class="flex items-center gap-2 flex-wrap">
            <n-tag
              size="small"
              :type="item.grade === 'S' ? 'error' : item.grade === 'A' ? 'warning' : 'default'"
            >
              {{ item.grade }} · {{ item.score }}
            </n-tag>
            <n-button
              size="small"
              quaternary
              type="primary"
              @click="router.push(`/collect/lead/${item.lead_id}`)"
            >
              {{ item.name }}
            </n-button>
            <span style="color: #999; font-size: 12px">ID {{ item.lead_id }}</span>
          </div>
          <ul style="margin: 8px 0 10px; padding-left: 18px; color: #666; font-size: 13px">
            <li
              v-for="(line, i) in evidenceLines(item)"
              :key="i"
            >
              {{ line }}
            </li>
          </ul>
          <div class="flex items-center gap-2 flex-wrap">
            <n-input
              v-model:value="notes[item.lead_id]"
              size="small"
              placeholder="备注（可选）：核验发现"
              style="width: 260px"
            />
            <n-button
              size="small"
              type="success"
              secondary
              @click="mark(item, 'correct')"
            >
              ✓ 判定正确
            </n-button>
            <n-button
              size="small"
              type="error"
              secondary
              @click="mark(item, 'incorrect')"
            >
              ✗ 判定错误
            </n-button>
            <n-button
              size="small"
              quaternary
              @click="mark(item, 'unsure')"
            >
              ? 无法判定
            </n-button>
          </div>
        </n-card>
      </n-space>
    </n-card>
  </div>
</template>
