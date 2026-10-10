<script setup lang="ts">
/**
 * 历史记录页。
 *
 * 字段：生成时间、目标城市、搜索关键词、抓取数量、状态。
 * 操作：查看详情（含实时日志与统计）、重试、取消、导出 CSV / JSON、删除。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { EChartsOption } from 'echarts'
import ChartPanel from '@/components/ChartPanel.vue'
import { exportApi, taskApi } from '@/api'
import { useTaskStore } from '@/stores/task'
import { useWorkbenchStore } from '@/stores/workbench'
import { useTaskSocket } from '@/composables/useTaskSocket'
import { formatDateTime, formatDuration, statusTagType } from '@/utils/format'
import type { ScrapeTask, TaskLog, TaskSummary } from '@/types'

const router = useRouter()
const store = useTaskStore()
const workbench = useWorkbenchStore()

const filters = reactive({ status: '', keyword: '', city: '' })

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '排队中', value: 'pending' },
  { label: '进行中', value: 'running' },
  { label: '成功', value: 'succeeded' },
  { label: '失败', value: 'failed' },
  { label: '待处理', value: 'needs_attention' },
  { label: '已取消', value: 'cancelled' },
]

/* ---------------- 详情弹窗 ---------------- */
const detailVisible = ref(false)
const currentTask = ref<ScrapeTask | null>(null)
const taskLog = ref<TaskLog | null>(null)
const taskSummary = ref<TaskSummary | null>(null)
const detailLoading = ref(false)
let detailTimer: number | null = null

const logText = computed(() => (taskLog.value?.lines ?? []).slice(-150).join('\n'))

const summarySalaryOption = computed<EChartsOption>(() => {
  const rows = taskSummary.value?.salary_distribution ?? []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 16, top: 20, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: rows.map((item) => item.bucket),
      axisLabel: { color: '#6b7a90', fontSize: 11 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e6eaf2' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#6b7a90', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f0f3f9' } },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 30,
        itemStyle: { borderRadius: [6, 6, 0, 0], color: '#5b8dff' },
        data: rows.map((item) => item.count),
      },
    ],
  }
})

async function openDetail(task: ScrapeTask) {
  currentTask.value = task
  detailVisible.value = true
  await refreshDetail()
  startDetailPolling(task.id)
}

async function refreshDetail() {
  const task = currentTask.value
  if (!task) return
  detailLoading.value = true
  try {
    const [fresh, logs, summary] = await Promise.all([
      taskApi.detail(task.id),
      taskApi.logs(task.id, 300),
      taskApi.summary(task.id).catch(() => null),
    ])
    currentTask.value = fresh
    taskLog.value = logs
    taskSummary.value = summary
  } finally {
    detailLoading.value = false
  }
}

function startDetailPolling(taskId: number) {
  stopDetailPolling()
  detailTimer = window.setInterval(async () => {
    const status = currentTask.value?.status
    if (!status || !['running', 'pending'].includes(status)) {
      stopDetailPolling()
      return
    }
    await refreshDetail()
  }, 2500)
}

function stopDetailPolling() {
  if (detailTimer !== null) {
    window.clearInterval(detailTimer)
    detailTimer = null
  }
}

/* ---------------- 列表操作 ---------------- */
async function load() {
  await store.loadTasks({
    page: store.page,
    page_size: store.pageSize,
    status: filters.status || undefined,
  })
}

async function onSearch() {
  store.page = 1
  await load()
}

async function onReset() {
  filters.status = ''
  filters.keyword = ''
  filters.city = ''
  await onSearch()
}

async function retry(task: ScrapeTask) {
  try {
    await ElMessageBox.confirm(
      `确认重新执行任务 #${task.id}（${task.keyword} @ ${task.city}）？`,
      '重试任务',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await store.retryTask(task.id)
  ElMessage.success('已重新入队')
  await load()
}

async function cancel(task: ScrapeTask) {
  await taskApi.cancel(task.id)
  ElMessage.success('已请求取消')
  await load()
}

async function remove(task: ScrapeTask) {
  try {
    await ElMessageBox.confirm(
      `确认删除任务 #${task.id} 的记录？已抓取的岗位数据会保留。`,
      '删除任务',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await store.removeTask(task.id)
  ElMessage.success('已删除')
  await load()
}

async function exportTask(task: ScrapeTask, fmt: 'csv' | 'json') {
  if (task.total_found <= 0) {
    ElMessage.warning('该任务没有岗位数据可导出')
    return
  }
  try {
    await exportApi.jobs({ fmt, task_id: task.id, include_jd: true })
    ElMessage.success(`已导出任务 #${task.id} 的岗位数据`)
  } catch {
    ElMessage.error('导出失败')
  }
}

/* ---------------- 实时更新 ---------------- */
useTaskSocket((event) => {
  const data = event.data as { task_id?: number; status?: string; progress?: number; stage_text?: string }

  if (event.type === 'task.progress' && data.task_id) {
    store.patchTask(data.task_id, {
      progress: data.progress ?? 0,
      stage_text: data.stage_text ?? null,
      status: (data.status as ScrapeTask['status']) ?? 'running',
    })
    return
  }

  if (event.type === 'task.finished' && data.task_id) {
    store.patchTask(data.task_id, {
      status: (data.status as ScrapeTask['status']) ?? 'succeeded',
      stage_text: '已结束',
    })
    load().catch(() => undefined)
    workbench.loadDashboard().catch(() => undefined)
  }

  if (event.type === 'task.created') {
    load().catch(() => undefined)
  }
})

onMounted(async () => {
  await load()
  await workbench.loadQueue().catch(() => undefined)
})

onBeforeUnmount(stopDetailPolling)
</script>

<template>
  <div class="bzp-page">
    <!-- ============ 筛选 ============ -->
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><Clock /></el-icon>
          任务历史记录
        </div>
        <div class="history__toolbar">
          <el-tag v-if="workbench.queue" type="info" size="small" effect="plain" round>
            队列：执行 {{ workbench.queue.running }} · 排队 {{ workbench.queue.pending }}
          </el-tag>
          <el-button size="small" @click="load">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
          <el-button size="small" type="primary" @click="router.push('/tasks')">
            <el-icon><Plus /></el-icon>
            新建任务
          </el-button>
        </div>
      </div>

      <div class="bzp-panel__body">
        <el-form :inline="true">
          <el-form-item label="状态">
            <el-select v-model="filters.status" style="width: 150px" @change="onSearch">
              <el-option
                v-for="item in statusOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="关键词">
            <el-input
              v-model="filters.keyword"
              placeholder="搜索关键词"
              clearable
              style="width: 180px"
              @keyup.enter="onSearch"
            />
          </el-form-item>
          <el-form-item label="城市">
            <el-input
              v-model="filters.city"
              placeholder="城市"
              clearable
              style="width: 130px"
              @keyup.enter="onSearch"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="onSearch">
              <el-icon><Search /></el-icon>
              查询
            </el-button>
            <el-button @click="onReset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>
    </section>

    <!-- ============ 任务表 ============ -->
    <section class="bzp-panel">
      <el-table v-loading="store.loading" :data="store.tasks" stripe row-key="id">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column label="生成时间" min-width="165">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="city" label="目标城市" width="100" />
        <el-table-column prop="keyword" label="搜索关键词" min-width="140" show-overflow-tooltip />
        <el-table-column label="筛选条件" min-width="150">
          <template #default="{ row }">
            <div class="history__filters">
              <el-tag v-if="row.salary" size="small" effect="plain">{{ row.salary }}</el-tag>
              <el-tag v-if="row.experience" size="small" effect="plain">经验码 {{ row.experience }}</el-tag>
              <el-tag v-if="row.degree" size="small" effect="plain">学历码 {{ row.degree }}</el-tag>
              <span v-if="!row.salary && !row.experience && !row.degree" class="bzp-muted">—</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="抓取数量" width="120" align="right">
          <template #default="{ row }">
            <div>{{ row.total_found }}</div>
            <div class="bzp-muted history__sub">
              新增 {{ row.new_count }} · 详情 {{ row.detail_count }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="页数" width="80" align="center">
          <template #default="{ row }">{{ row.max_pages }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ formatDuration(row.duration_seconds) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="170">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.error_message" placement="top">
              <template #content>
                <div style="max-width: 320px">{{ row.error_message }}</div>
              </template>
              <el-tag :type="statusTagType(row.status)" size="small" effect="light">
                {{ row.status_label }}
              </el-tag>
            </el-tooltip>
            <el-progress
              v-if="row.status === 'running'"
              :percentage="row.progress"
              :stroke-width="3"
              :show-text="false"
              style="margin-top: 5px"
            />
            <div v-if="row.stage_text" class="bzp-muted history__stage">{{ row.stage_text }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="290" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="openDetail(row)">查看详情</el-button>
            <el-button
              v-if="['running', 'pending'].includes(row.status)"
              link
              size="small"
              type="warning"
              @click="cancel(row)"
            >
              取消
            </el-button>
            <el-button
              v-else
              link
              size="small"
              type="primary"
              @click="retry(row)"
            >
              重试
            </el-button>
            <el-dropdown trigger="click" @command="(fmt: 'csv' | 'json') => exportTask(row, fmt)">
              <el-button link size="small">导出<el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="csv">导出 CSV</el-dropdown-item>
                  <el-dropdown-item command="json">导出 JSON</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button link size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="暂无任务记录">
            <el-button type="primary" @click="router.push('/tasks')">创建抓取任务</el-button>
          </el-empty>
        </template>
      </el-table>

      <div class="history__pagination">
        <el-pagination
          v-model:current-page="store.page"
          v-model:page-size="store.pageSize"
          :total="store.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="load"
          @size-change="onSearch"
        />
      </div>
    </section>

    <!-- ============ 详情弹窗 ============ -->
    <el-dialog
      v-model="detailVisible"
      :title="currentTask ? `任务 #${currentTask.id} · ${currentTask.keyword} @ ${currentTask.city}` : '任务详情'"
      width="880px"
      top="5vh"
      destroy-on-close
      @closed="stopDetailPolling"
    >
      <div v-if="currentTask" v-loading="detailLoading" class="history__detail">
        <el-descriptions :column="4" size="small" border>
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(currentTask.status)" size="small" effect="light">
              {{ currentTask.status_label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="进度">{{ currentTask.progress }}%</el-descriptions-item>
          <el-descriptions-item label="尝试次数">{{ currentTask.attempts }}</el-descriptions-item>
          <el-descriptions-item label="耗时">
            {{ formatDuration(currentTask.duration_seconds) }}
          </el-descriptions-item>
          <el-descriptions-item label="命中岗位">{{ currentTask.total_found }}</el-descriptions-item>
          <el-descriptions-item label="新增入库">{{ currentTask.new_count }}</el-descriptions-item>
          <el-descriptions-item label="详情抓取">{{ currentTask.detail_count }}</el-descriptions-item>
          <el-descriptions-item label="页数上限">{{ currentTask.max_pages }}</el-descriptions-item>
          <el-descriptions-item label="创建时间" :span="2">
            {{ formatDateTime(currentTask.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="开始时间" :span="2">
            {{ formatDateTime(currentTask.started_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="currentTask.error_message"
          :title="currentTask.error_message"
          :type="currentTask.status === 'needs_attention' ? 'warning' : 'error'"
          :closable="false"
          show-icon
          class="history__alert"
        />

        <el-progress
          v-if="['running', 'pending'].includes(currentTask.status)"
          :percentage="currentTask.progress"
          :stroke-width="10"
          striped
          striped-flow
          class="history__progress"
        />

        <div v-if="taskSummary && taskSummary.total_jobs" class="history__summary">
          <div class="history__section-title">任务统计</div>
          <div class="bzp-grid bzp-grid--2">
            <ChartPanel
              :option="summarySalaryOption"
              height="200px"
              :empty="!taskSummary.salary_distribution.length"
              empty-text="暂无薪资数据"
            />
            <div>
              <div class="history__sub-title">高频技能</div>
              <div class="history__skills">
                <el-tag
                  v-for="item in taskSummary.top_skills.slice(0, 12)"
                  :key="item.skill"
                  size="small"
                  effect="plain"
                  class="history__skill"
                >
                  {{ item.skill }} <span class="bzp-muted">{{ item.count }}</span>
                </el-tag>
                <span v-if="!taskSummary.top_skills.length" class="bzp-muted">—</span>
              </div>
              <div class="history__sub-title">高频公司</div>
              <div class="history__skills">
                <el-tag
                  v-for="item in taskSummary.top_companies.slice(0, 8)"
                  :key="item.value"
                  size="small"
                  effect="plain"
                  type="info"
                  class="history__skill"
                >
                  {{ item.value }} <span class="bzp-muted">{{ item.count }}</span>
                </el-tag>
                <span v-if="!taskSummary.top_companies.length" class="bzp-muted">—</span>
              </div>
            </div>
          </div>
        </div>

        <div class="history__section-title">运行日志</div>
        <div class="bzp-terminal">
          {{ logText || '（暂无日志输出）' }}
        </div>
      </div>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button v-if="currentTask" @click="refreshDetail">刷新</el-button>
        <el-button
          v-if="currentTask && ['running', 'pending'].includes(currentTask.status)"
          type="warning"
          plain
          @click="currentTask && cancel(currentTask)"
        >
          取消任务
        </el-button>
        <el-button
          v-else-if="currentTask"
          type="primary"
          plain
          @click="currentTask && retry(currentTask)"
        >
          重试任务
        </el-button>
        <el-button
          v-if="currentTask"
          type="primary"
          @click="router.push(`/jobs?task_id=${currentTask.id}`)"
        >
          查看岗位结果
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.history__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.history__filters {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.history__sub,
.history__stage {
  font-size: 11px;
  margin-top: 2px;
}

.history__stage {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}

.history__pagination {
  display: flex;
  justify-content: flex-end;
  padding: 14px 18px;
  border-top: 1px solid var(--bzp-border);
}

.history__detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.history__alert,
.history__progress {
  margin: 0;
}

.history__section-title {
  font-weight: 600;
  font-size: 13.5px;
  color: var(--bzp-text);
}

.history__sub-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--bzp-text-secondary);
  margin-bottom: 8px;
}

.history__skills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 14px;
}

.history__skill {
  margin: 0;
}
</style>
