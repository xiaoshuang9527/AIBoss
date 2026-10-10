<script setup lang="ts">
/**
 * 顶部状态栏：欢迎语 + 系统运行状态 + 队列情况 + 快捷操作。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useWorkbenchStore } from '@/stores/workbench'
import { useTaskSocket } from '@/composables/useTaskSocket'
import { fromNow } from '@/utils/format'

const router = useRouter()
const store = useWorkbenchStore()

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了，注意休息'
  if (hour < 12) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const statusDotClass = computed(() => `status-dot status-dot--${store.statusLevel}`)

const queueText = computed(() => {
  const queue = store.queue
  if (!queue) return '队列空闲'
  if (!queue.worker_alive) return '队列未运行'
  if (queue.running === 0 && queue.pending === 0) return '队列空闲'
  return `执行中 ${queue.running} · 排队 ${queue.pending}`
})

const queueBusy = computed(() => {
  const queue = store.queue
  return Boolean(queue && (queue.running > 0 || queue.pending > 0))
})

const lastRefreshed = computed(() =>
  store.lastRefreshedAt ? fromNow(store.lastRefreshedAt.toISOString()) : '—',
)

// 实时通道：队列变化即时反映到状态栏
const { connected } = useTaskSocket((event) => {
  if (event.type === 'queue.updated' || event.type === 'queue.started') {
    store.applyQueueEvent(event.data as never)
  }
  if (event.type === 'task.finished' || event.type === 'task.created') {
    store.loadQueue().catch(() => undefined)
  }
})

async function refresh() {
  try {
    await Promise.all([store.loadDashboard(), store.loadQueue()])
    ElMessage.success('数据已刷新')
  } catch {
    ElMessage.error('刷新失败，请检查后端服务')
  }
}
</script>

<template>
  <header class="topbar">
    <div class="topbar__left">
      <div class="topbar__greeting">{{ greeting }}，欢迎使用智能求职辅助工作台</div>
      <div class="topbar__sub">
        一站式完成岗位抓取、结果分析与 BOSS 直聘精准沟通
      </div>
    </div>

    <div class="topbar__right">
      <el-tooltip :content="connected ? '实时通道已连接' : '实时通道断开，正在重连（不影响使用）'" placement="bottom">
        <el-tag :type="connected ? 'success' : 'info'" effect="plain" size="small" round>
          <span class="topbar__tag-inner">
            <el-icon><Connection /></el-icon>
            {{ connected ? '实时' : '轮询' }}
          </span>
        </el-tag>
      </el-tooltip>

      <el-tooltip :content="`最近刷新：${lastRefreshed}`" placement="bottom">
        <div class="topbar__status">
          <span :class="statusDotClass" />
          <span class="topbar__status-text">{{ store.statusText }}</span>
        </div>
      </el-tooltip>

      <el-tag :type="queueBusy ? 'warning' : 'info'" effect="light" size="small" round>
        {{ queueText }}
      </el-tag>

      <el-button size="small" :loading="store.loading" @click="refresh">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>

      <el-button size="small" type="primary" @click="router.push('/tasks')">
        <el-icon><Promotion /></el-icon>
        开始抓取
      </el-button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 24px;
  background: var(--bzp-panel);
  border-bottom: 1px solid var(--bzp-border);
  position: sticky;
  top: 0;
  z-index: 10;
}

.topbar__greeting {
  font-size: 15px;
  font-weight: 600;
  color: var(--bzp-text);
}

.topbar__sub {
  margin-top: 3px;
  font-size: 12px;
  color: var(--bzp-text-secondary);
}

.topbar__right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.topbar__tag-inner {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.topbar__status {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 5px 11px;
  border-radius: 999px;
  background: #f5f8fd;
  border: 1px solid var(--bzp-border);
  font-size: 12px;
  color: var(--bzp-text-secondary);
  cursor: default;
}

.topbar__status-text {
  white-space: nowrap;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot--ok {
  background: var(--bzp-success);
  box-shadow: 0 0 0 3px rgba(20, 184, 122, 0.16);
}

.status-dot--warning {
  background: var(--bzp-warning);
  box-shadow: 0 0 0 3px rgba(245, 166, 35, 0.16);
}

.status-dot--error {
  background: var(--bzp-danger);
  box-shadow: 0 0 0 3px rgba(240, 71, 91, 0.16);
}

@media (max-width: 1100px) {
  .topbar__sub {
    display: none;
  }
}
</style>
