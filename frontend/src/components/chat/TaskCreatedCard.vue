<script setup lang="ts">
/**
 * 「抓取任务已创建」卡片。
 *
 * 为什么要有这张卡（而不是让 AI 用文字说）
 * ----------------------------------------
 * 发起抓取是**有副作用的操作**，用户需要确认三件事：
 *   1. 到底发出去了没有（任务编号是多少）
 *   2. 什么时候能好（要等 10-20 分钟，不能让人以为点一下就好）
 *   3. 去哪里看进度
 *
 * 这些都更适合用结构化卡片表达，而不是埋在模型的长段落里。
 *
 * 另外这里会显示**薪资档位差异**：BOSS 只有粗档位筛选，用户要「15-25K」
 * 实际会落到「10-20K」档，必须如实告知，否则他会疑惑"为什么给我 45K 的岗位"。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { TaskCreatedPayload } from '@/types/chat'

const props = defineProps<{ task: TaskCreatedPayload }>()

const router = useRouter()

const isDuplicate = computed(() => props.task.duplicate === true)

const STATUS_TONE = computed(() => (isDuplicate.value ? 'warning' : 'success'))

function goHistory() {
  router.push({ path: '/history', query: { task: String(props.task.task_id) } })
}
</script>

<template>
  <div class="task-card" :class="{ 'task-card--dup': isDuplicate }">
    <div class="task-card__head">
      <el-icon class="task-card__icon">
        <component :is="isDuplicate ? 'WarningFilled' : 'Promotion'" />
      </el-icon>
      <span class="task-card__title">
        {{ isDuplicate ? '已有相同任务在进行' : '抓取任务已创建' }}
      </span>
      <el-tag :type="STATUS_TONE" size="small" effect="dark" round>
        #{{ task.task_id }}
      </el-tag>
    </div>

    <div class="task-card__grid">
      <div class="task-card__cell">
        <span class="task-card__label">关键词</span>
        <span class="task-card__value">{{ task.keyword }}</span>
      </div>
      <div class="task-card__cell">
        <span class="task-card__label">城市</span>
        <span class="task-card__value">{{ task.city }}</span>
      </div>
      <div class="task-card__cell">
        <span class="task-card__label">抓取页数</span>
        <span class="task-card__value">{{ task.max_pages }} 页</span>
      </div>
      <div class="task-card__cell">
        <span class="task-card__label">含 JD 详情</span>
        <span class="task-card__value">{{ task.with_detail ? '是' : '否' }}</span>
      </div>
      <div v-if="task.experience" class="task-card__cell">
        <span class="task-card__label">经验要求</span>
        <span class="task-card__value">{{ task.experience }}</span>
      </div>
      <div v-if="task.degree" class="task-card__cell">
        <span class="task-card__label">学历要求</span>
        <span class="task-card__value">{{ task.degree }}</span>
      </div>
    </div>

    <!-- 薪资档位：必须解释清楚，否则用户会疑惑筛出来的岗位薪资不对 -->
    <el-alert
      v-if="task.salary && task.salary.note"
      type="warning"
      :closable="false"
      show-icon
      class="task-card__alert"
    >
      <template #title>薪资筛选已按最接近的档位处理</template>
      <template #default>
        <div class="task-card__salary-detail">
          <div>
            你的要求：<b>{{ task.salary.raw }}</b>
            → 实际档位：<b>{{ task.salary.label }}</b>
          </div>
          <div class="task-card__note">{{ task.salary.note }}</div>
        </div>
      </template>
    </el-alert>

    <div v-else-if="task.salary && task.salary.code !== '0'" class="task-card__salary-ok">
      薪资档位：{{ task.salary.label }}
    </div>

    <div class="task-card__foot">
      <span class="task-card__eta">
        <el-icon><Clock /></el-icon>
        {{ isDuplicate ? '与前一个任务共用同一批数据' : `预计 ${task.estimated_minutes} 分钟左右完成` }}
      </span>
      <el-button type="primary" size="small" plain @click="goHistory">
        <el-icon><List /></el-icon>
        去历史记录查看进度
      </el-button>
    </div>

    <div class="task-card__hint">
      任务在后台异步执行，可以先去忙别的。完成后回来让我检索新数据即可。
    </div>
  </div>
</template>

<style scoped>
.task-card {
  border: 1px solid var(--bzp-border);
  border-left: 3px solid var(--bzp-success);
  border-radius: 9px;
  padding: 12px 14px;
  background: #fbfefc;
  margin: 4px 0 10px;
}

.task-card--dup {
  border-left-color: var(--bzp-warning);
  background: #fffdf7;
}

.task-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.task-card__icon {
  font-size: 16px;
  color: var(--bzp-success);
}

.task-card--dup .task-card__icon {
  color: var(--bzp-warning);
}

.task-card__title {
  flex: 1;
  font-size: 13.5px;
  font-weight: 600;
}

.task-card__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px 16px;
  font-size: 12.5px;
}

.task-card__cell {
  display: flex;
  gap: 6px;
  min-width: 0;
}

.task-card__label {
  color: var(--bzp-text-secondary);
  flex-shrink: 0;
}

.task-card__value {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-card__alert {
  margin-top: 12px;
}

.task-card__salary-detail {
  font-size: 12px;
  line-height: 1.75;
}

.task-card__note {
  color: #8a6d3b;
  margin-top: 2px;
}

.task-card__salary-ok {
  margin-top: 10px;
  font-size: 12px;
  color: var(--bzp-text-secondary);
}

.task-card__foot {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--bzp-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.task-card__eta {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #b07d00;
  font-weight: 500;
}

.task-card__hint {
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}
</style>
