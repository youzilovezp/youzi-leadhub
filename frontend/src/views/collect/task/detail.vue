<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NTag } from 'naive-ui'
import * as collectApi from '@/api/collect'
import type { CollectTask, TaskLog } from '@/api/collect'
import { useUserStore } from '@/stores/user'
import { formatTime } from '@/utils/format'
import { message } from '@/utils/feedback'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const taskId = Number(route.params.id)

const task = ref<CollectTask | null>(null)
const logs = ref<TaskLog[]>([])
const logLoading = ref(false)
const logAutoScroll = ref(true)

const statusTag: Record<string, { type: 'default' | 'info' | 'success' | 'error' | 'warning'; label: string }> = {
  pending: { type: 'default', label: '待执行' },
  queued: { type: 'info', label: '排队中' },
  running: { type: 'warning', label: '运行中' },
  completed: { type: 'success', label: '已完成' },
  failed: { type: 'error', label: '失败' },
  cancelled: { type: 'default', label: '已取消' },
}

const active = computed(() => task.value && ['queued', 'running'].includes(task.value.status))
const progressPercent = computed(() => {
  if (!task.value || !task.value.progress_total) return 0
  return Math.round((task.value.progress_done / task.value.progress_total) * 100)
})

async function fetchTask() {
  task.value = await collectApi.getTask(taskId)
}

let lastLogId = 0
async function fetchLogs() {
  logLoading.value = true
  try {
    const data = await collectApi.getTaskLogs(taskId, lastLogId)
    if (data.items.length) {
      logs.value.push(...data.items)
      const last = data.items[data.items.length - 1]
      if (last) lastLogId = last.id
      if (logAutoScroll.value) requestAnimationFrame(scrollLogBottom)
    }
  } finally {
    logLoading.value = false
  }
}

const logBox = ref<HTMLElement | null>(null)
function scrollLogBottom() {
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
}

let timer: ReturnType<typeof setInterval> | null = null
function schedulePolling() {
  if (active.value && !timer) {
    timer = setInterval(async () => {
      await Promise.all([fetchTask(), fetchLogs()])
      if (!active.value) {
        if (timer) clearInterval(timer)
        timer = null
      }
    }, 2000)
  }
}

async function handleCancel() {
  await collectApi.cancelTask(taskId)
  message.success('已请求取消')
  fetchTask()
}

async function handleRun() {
  await collectApi.runTask(taskId)
  message.success('已入队')
  await fetchTask()
  schedulePolling()
}

function levelColor(level: string) {
  return level === 'error' ? '#d03050' : level === 'warn' ? '#f0a020' : '#888'
}

onMounted(async () => {
  await Promise.all([fetchTask(), fetchLogs()])
  schedulePolling()
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div
    v-if="task"
    class="page"
  >
    <n-card
      size="small"
      class="mb-4"
    >
      <div class="flex items-center gap-3">
        <n-button
          quaternary
          size="small"
          @click="router.push('/collect/task')"
        >
          ← 返回
        </n-button>
        <h2 class="detail-title">
          任务 #{{ task.id }} {{ task.name }}
        </h2>
        <n-tag
          :type="(statusTag[task.status]?.type as any) || 'default'"
          size="small"
        >
          {{ statusTag[task.status]?.label || task.status }}
        </n-tag>
        <div class="flex-1" />
        <!-- 取消/重跑是管理员操作，销售只读（与列表页门控一致） -->
        <n-button
          v-if="active && userStore.isSuperuser"
          type="warning"
          secondary
          size="small"
          @click="handleCancel"
        >
          取消任务
        </n-button>
        <n-button
          v-else-if="userStore.isSuperuser"
          type="primary"
          secondary
          size="small"
          @click="handleRun"
        >
          再次执行
        </n-button>
      </div>
    </n-card>

    <div class="grid gap-4 detail-grid">
      <n-card
        size="small"
        title="参数与进度"
      >
        <div class="kv">
          <span class="k">采集器</span><span>{{ task.collector }}</span>
          <span class="k">定时</span><span>{{ task.cron_expr || '手动' }}</span>
          <span class="k">参数</span>
          <span class="mono">{{ JSON.stringify(task.params) }}</span>
          <span class="k">开始</span><span>{{ task.started_at ? formatTime(task.started_at) : '—' }}</span>
          <span class="k">结束</span><span>{{ task.finished_at ? formatTime(task.finished_at) : '—' }}</span>
          <template v-if="active">
            <span class="k">进度</span>
            <span>
              <n-progress
                type="line"
                :percentage="progressPercent"
                :height="10"
                style="width: 220px; display: inline-block; vertical-align: middle"
              />
              {{ task.progress_done }}/{{ task.progress_total }}
            </span>
          </template>
          <span class="k">线索</span>
          <span>新增 {{ task.leads_added }} · 合并 {{ task.leads_merged }}</span>
          <template v-if="task.error">
            <span class="k">错误</span>
            <span class="error-text">{{ task.error }}</span>
          </template>
        </div>
      </n-card>

      <n-card
        size="small"
        title="执行日志"
      >
        <template #header-extra>
          <n-checkbox
            v-model:checked="logAutoScroll"
            size="small"
          >
            自动滚动
          </n-checkbox>
        </template>
        <div
          ref="logBox"
          class="log-box"
        >
          <div
            v-if="!logs.length && !logLoading"
            class="log-empty"
          >
            暂无日志
          </div>
          <div
            v-for="log in logs"
            :key="log.id"
            class="log-line"
          >
            <span class="log-time">{{ formatTime(log.created_at) }}</span>
            <span :style="{ color: levelColor(log.level) }">[{{ log.level }}]</span>
            <span class="log-msg">{{ log.message }}</span>
          </div>
        </div>
      </n-card>
    </div>
  </div>
</template>

<style scoped>
.detail-title {
  font-size: 16px;
  margin: 0;
}
.detail-grid {
  grid-template-columns: 1fr;
}
.kv {
  display: grid;
  grid-template-columns: 64px 1fr;
  gap: 8px 12px;
  font-size: 13px;
}
.kv .k {
  color: var(--yz-text-secondary, #888);
}
.mono {
  font-family: monospace;
  word-break: break-all;
}
.error-text {
  color: #d03050;
}
.log-box {
  max-height: 420px;
  overflow-y: auto;
  background: var(--yz-bg-page, #f7f7f7);
  border-radius: 4px;
  padding: 8px 12px;
  font-family: monospace;
  font-size: 12px;
}
.log-line {
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-time {
  color: #999;
  margin-right: 8px;
}
.log-msg {
  margin-left: 4px;
}
.log-empty {
  color: #999;
  padding: 12px 0;
  text-align: center;
}
</style>
