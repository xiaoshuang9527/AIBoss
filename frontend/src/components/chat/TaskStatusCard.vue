<script setup lang="ts">
/**
 * 抓取任务进度卡片（tool: check_task_status）。
 *
 * 用户问「任务好了吗」时，模型会调用 check_task_status 拿到真实状态，
 * 这里把它渲染成进度卡片 —— 比让模型用文字复述更准确，也不会漏字段。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { TaskStatusItem } from '@/types/chat'

const props = defineProps<{ tasks: TaskStatusItem[] }>()

const router = useRouter()

const TONE: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'primary'> = {
  succeeded: 'success',
  running: 'primary',
  pending: 'info',
  needs_attention: 'warning',
  failed: 'danger',
  cancelled: 'info',
}

function toneOf(task: TaskStatusItem) {
  return TONE[task.status] ?? 'info'
}

function goHistory(taskId: number) {
  router.push({ path: '/history', query: { task: String(taskId) } })
}

/** 耗时显示：秒 → 人话 */
function durationText(task: TaskStatusItem): string | null {
  const seconds = task.duration_seconds
  if (!seconds) return null
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  return `${Math.round(seconds / 60)} 分钟`
}

const hasActive = computed(() => props.tasks.some((item) => item.is_active))
</script>

<template>
  <div class="status-card">
    <div class="status-card__head">
      <el-icon><DataLine /></el-icon>
      <span>抓取任务进度</span>
    </div>

    <div class="status-card__list">
      <div v-for="task in tasks" :key="task.task_id" class="status-row">
        <div class="status-row__top">
          <el-tag :type="toneOf(task)" size="small" effect="light" round>
            {{ task.status_label }}
          </el-tag>
          <span class="status-row__id">#{{ task.task_id }}</span>
          <span class="status-row__title">{{ task.keyword }} @ {{ task.city }}</span>
          <span v-if="durationText(task)" class="status-row__dur">用时 {{ durationText(task) }}</span>
        </div>

        <!-- 进行中：显示进度条 -->
        <div v-if="task.is_active" class="status-row__progress">
          <el-progress :percentage="task.progress" :stroke-width="6" striped striped-flow />
          <span v-if="task.stage_text" class="status-row__stage">{{ task.stage_text }}</span>
        </div>

        <!-- 已结束：显示结果统计 -->
        <div v-else class="status-row__stats">
          <span>抓取 <b>{{ task.total_found }}</b> 条</span>
          <span class="status-row__new">新增 <b>{{ task.new_count }}</b> 条</span>
          <span v-if="task.detail_count">详情 <b>{{ task.detail_count }}</b> 条</span>
        </div>

        <el-alert
          v-if="task.error_message"
          type="error"
          :closable="false"
          show-icon
          class="status-row__error"
        >
          <template #title>{{ task.error_message }}</template>
        </el-alert>

        <div class="status-row__actions">
          <el-button link size="small" type="primary" @click="goHistory(task.task_id)">
            查看详情
          </el-button>
        </div>
      </div>
    </div>

    <div v-if="hasActive" class="status-card__hint">
      有任务还在进行中。抓取期间可以继续聊天，完成后让我检索新数据即可。
    </div>
    <div v-else-if="tasks.length" class="status-card__hint">
      任务都已结束。可以说「帮我检索一下新抓到的岗位」来看结果。
    </div>
  </div>
</template>

<style scoped>
.status-card {
  border: 1px solid var(--bzp-border);
  border-radius: 9px;
  padding: 12px 14px;
  background: #fbfcfe;
  margin: 4px 0 10px;
}

.status-card__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--bzp-text-secondary);
  margin-bottom: 10px;
}

.status-card__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.status-row {
  border: 1px solid var(--bzp-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}

.status-row__top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12.5px;
}

.status-row__id {
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: var(--bzp-text-secondary);
  font-size: 11.5px;
}

.status-row__title {
  font-weight: 500;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-row__dur {
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}

.status-row__progress {
  margin-top: 9px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.status-row__stage {
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}

.status-row__stats {
  margin-top: 8px;
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--bzp-text-secondary);
}

.status-row__stats b {
  color: var(--bzp-text);
}

.status-row__new b {
  color: var(--bzp-success);
}

.status-row__error {
  margin-top: 8px;
}

.status-row__actions {
  margin-top: 6px;
  text-align: right;
}

.status-card__hint {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--bzp-border);
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.7;
}
</style>
