<script setup lang="ts">
/**
 * 工作台首页。
 *
 * 结构：概览卡片（4）→ 快捷入口 → 图表区 → 最近任务记录表格。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { EChartsOption } from 'echarts'
import StatCard from '@/components/StatCard.vue'
import ChartPanel from '@/components/ChartPanel.vue'
import { useWorkbenchStore } from '@/stores/workbench'
import { useTaskStore } from '@/stores/task'
import { exportApi, taskApi } from '@/api'
import { useTaskSocket } from '@/composables/useTaskSocket'
import { authTagType, formatDateTime, formatDuration, statusTagType } from '@/utils/format'
import type { ScrapeTask } from '@/types'

const router = useRouter()
const store = useWorkbenchStore()
const taskStore = useTaskStore()

const recentTasks = ref<ScrapeTask[]>([])
const refreshing = ref(false)

const cards = computed(() => store.cards)
const summary = computed(() => store.dashboard)

/* ---------------- 图表配置 ---------------- */
const axisStyle = {
  axisLine: { lineStyle: { color: '#e6eaf2' } },
  axisLabel: { color: '#6b7a90', fontSize: 11 },
  axisTick: { show: false },
}

const salaryOption = computed<EChartsOption>(() => {
  const data = summary.value?.salary_distribution
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 18, top: 26, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: data?.categories ?? [], ...axisStyle },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f3f9' } }, ...axisStyle },
    series: [
      {
        name: '岗位数',
        type: 'bar',
        barMaxWidth: 34,
        itemStyle: {
          borderRadius: [6, 6, 0, 0],
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: '#5b8dff' },
              { offset: 1, color: '#a9c5ff' },
            ],
          },
        },
        data: data?.series?.[0]?.data ?? [],
      },
    ],
  }
})

const trendOption = computed<EChartsOption>(() => {
  const data = summary.value?.daily_trend
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 8, right: 18, top: 26, bottom: 4, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: data?.categories ?? [], ...axisStyle },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f3f9' } }, ...axisStyle },
    series: [
      {
        name: '新增岗位',
        type: 'line',
        smooth: true,
        symbolSize: 5,
        lineStyle: { width: 2.5, color: '#2f6bff' },
        itemStyle: { color: '#2f6bff' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(47,107,255,0.26)' },
              { offset: 1, color: 'rgba(47,107,255,0.02)' },
            ],
          },
        },
        data: data?.series?.[0]?.data ?? [],
      },
    ],
  }
})

const experienceOption = computed<EChartsOption>(() => {
  const data = summary.value?.experience_distribution
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, icon: 'circle', textStyle: { color: '#6b7a90', fontSize: 11 } },
    series: [
      {
        name: '经验要求',
        type: 'pie',
        radius: ['46%', '68%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        data: (data?.categories ?? []).map((name, index) => ({
          name,
          value: Number(data?.series?.[0]?.data?.[index] ?? 0),
        })),
      },
    ],
  }
})

const cityOption = computed<EChartsOption>(() => {
  const data = summary.value?.city_distribution
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 30, top: 16, bottom: 4, containLabel: true },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f3f9' } }, ...axisStyle },
    yAxis: {
      type: 'category',
      data: [...(data?.categories ?? [])].reverse(),
      ...axisStyle,
    },
    series: [
      {
        name: '岗位数',
        type: 'bar',
        barMaxWidth: 16,
        itemStyle: { borderRadius: [0, 6, 6, 0], color: '#14b87a' },
        data: [...((data?.series?.[0]?.data ?? []) as number[])].reverse(),
      },
    ],
  }
})

/* ---------------- 快捷入口 ---------------- */
const quickActions = [
  {
    label: '开始抓取岗位',
    description: '设置关键词与城市，创建新的抓取任务',
    icon: 'Promotion',
    tone: 'primary',
    to: '/tasks',
  },
  {
    label: '授权 BOSS 账号',
    description: '扫码登录并持久化登录态，供抓取复用',
    icon: 'Key',
    tone: 'warning',
    to: '/auth',
  },
  {
    label: '查看历史记录',
    description: '查看任务状态、日志，支持重试与导出',
    icon: 'Clock',
    tone: 'success',
    to: '/history',
  },
  {
    label: '系统设置',
    description: '调整并发、抓取节奏与页数上限',
    icon: 'Setting',
    tone: 'info',
    to: '/settings',
  },
]

/* ---------------- 数据加载 ---------------- */
async function loadDashboard() {
  await store.loadDashboard()
  recentTasks.value = store.dashboard?.recent_tasks ?? []
}

async function onRefresh() {
  refreshing.value = true
  try {
    await Promise.all([loadDashboard(), store.loadQueue()])
    ElMessage.success('已刷新')
  } finally {
    refreshing.value = false
  }
}

async function exportTask(task: ScrapeTask) {
  try {
    await exportApi.jobs({ fmt: 'csv', task_id: task.id, include_jd: true })
    ElMessage.success(`已导出任务 #${task.id} 的岗位数据`)
  } catch {
    ElMessage.error('导出失败，可能该任务还没有岗位数据')
  }
}

async function retryTask(task: ScrapeTask) {
  try {
    await ElMessageBox.confirm(
      `确认重新执行任务 #${task.id}（${task.keyword} @ ${task.city}）？`,
      '重试任务',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await taskStore.retryTask(task.id)
  ElMessage.success('任务已重新入队')
  await onRefresh()
}

async function cancelTask(task: ScrapeTask) {
  try {
    await taskApi.cancel(task.id)
    ElMessage.success('已请求取消任务')
    await onRefresh()
  } catch {
    ElMessage.error('取消失败')
  }
}

/* ---------------- 实时更新 ---------------- */
useTaskSocket((event) => {
  if (event.type === 'queue.updated' || event.type === 'queue.started') {
    store.applyQueueEvent(event.data as never)
    return
  }
  if (event.type === 'task.finished' || event.type === 'task.created') {
    loadDashboard().catch(() => undefined)
    store.loadQueue().catch(() => undefined)
  }
})

onMounted(loadDashboard)
</script>

<template>
  <div class="bzp-page">
    <!-- ============ 概览卡片 ============ -->
    <section class="bzp-grid dashboard__cards">
      <StatCard
        title="累计抓取岗位数"
        :value="cards.total_jobs"
        suffix="条"
        icon="Briefcase"
        tone="primary"
        :hint="`收藏 ${cards.favorite_jobs} 条`"
        hint-type="info"
        :loading="store.loading"
        clickable
        @click="router.push('/jobs')"
      />
      <StatCard
        title="今日新增岗位数"
        :value="cards.today_new_jobs"
        suffix="条"
        icon="TrendCharts"
        tone="success"
        :hint="`今日任务 ${cards.today_tasks} 个`"
        hint-type="success"
        :loading="store.loading"
        clickable
        @click="router.push('/jobs?only_new=1')"
      />
      <StatCard
        title="监控任务数"
        :value="cards.active_tasks"
        suffix="个"
        icon="Loading"
        tone="warning"
        :hint="
          store.queue
            ? `并发上限 ${store.queue.max_concurrent} · 空闲 ${store.queue.slots_free}`
            : '排队中 / 执行中'
        "
        hint-type="warning"
        :loading="store.loading"
        clickable
        @click="router.push('/history')"
      />
      <StatCard
        title="账号授权状态"
        :value="cards.auth_status_label"
        icon="Key"
        :tone="cards.auth_status === 'active' ? 'success' : 'danger'"
        :hint="cards.auth_account || '前往账号授权页扫码登录'"
        :hint-type="authTagType(cards.auth_status)"
        :loading="store.loading"
        clickable
        @click="router.push('/auth')"
      />
    </section>

    <!-- ============ 快捷入口 ============ -->
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><Grid /></el-icon>
          快捷入口
        </div>
        <span class="bzp-panel__subtitle">常用操作一步直达</span>
      </div>
      <div class="bzp-panel__body">
        <div class="bzp-grid dashboard__quick">
          <button
            v-for="action in quickActions"
            :key="action.label"
            class="quick-action"
            :class="`quick-action--${action.tone}`"
            type="button"
            @click="router.push(action.to)"
          >
            <div class="quick-action__icon">
              <el-icon><component :is="action.icon" /></el-icon>
            </div>
            <div class="quick-action__body">
              <div class="quick-action__label">{{ action.label }}</div>
              <div class="quick-action__desc">{{ action.description }}</div>
            </div>
            <el-icon class="quick-action__arrow"><ArrowRight /></el-icon>
          </button>
        </div>
      </div>
    </section>

    <!-- ============ 图表区 ============ -->
    <section class="bzp-grid dashboard__charts">
      <div class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><Histogram /></el-icon>
            薪资分布
          </div>
          <span class="bzp-panel__subtitle">按岗位月薪均值分桶</span>
        </div>
        <div class="bzp-panel__body">
          <ChartPanel
            :option="salaryOption"
            :empty="!(summary?.salary_distribution?.categories?.length)"
            empty-text="抓取岗位后展示薪资分布"
          />
        </div>
      </div>

      <div class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><TrendCharts /></el-icon>
            近 14 天新增趋势
          </div>
        </div>
        <div class="bzp-panel__body">
          <ChartPanel
            :option="trendOption"
            :empty="!(summary?.daily_trend?.categories?.length)"
            empty-text="暂无趋势数据"
          />
        </div>
      </div>

      <div class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><PieChart /></el-icon>
            经验要求分布
          </div>
        </div>
        <div class="bzp-panel__body">
          <ChartPanel
            :option="experienceOption"
            :empty="!(summary?.experience_distribution?.categories?.length)"
            empty-text="暂无经验要求数据"
          />
        </div>
      </div>

      <div class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><Location /></el-icon>
            城市分布 Top 10
          </div>
        </div>
        <div class="bzp-panel__body">
          <ChartPanel
            :option="cityOption"
            :empty="!(summary?.city_distribution?.categories?.length)"
            empty-text="暂无城市数据"
          />
        </div>
      </div>
    </section>

    <!-- ============ 技能与热门公司 ============ -->
    <section class="bzp-grid bzp-grid--2">
      <div class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><MagicStick /></el-icon>
            高频技能标签
          </div>
          <span class="bzp-panel__subtitle">从 JD 全文自动提取</span>
        </div>
        <div class="bzp-panel__body">
          <div v-if="summary?.top_skills?.length" class="skills">
            <el-tag
              v-for="item in summary.top_skills"
              :key="item.skill"
              class="skills__tag"
              effect="light"
              round
              @click="router.push(`/jobs?skill=${encodeURIComponent(item.skill)}`)"
            >
              {{ item.skill }}
              <span class="skills__count">{{ item.count }}</span>
            </el-tag>
          </div>
          <el-empty v-else description="抓取岗位后展示技能词频" :image-size="70" />
        </div>
      </div>

      <div class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><OfficeBuilding /></el-icon>
            热门招聘公司
          </div>
        </div>
        <div class="bzp-panel__body">
          <el-table
            v-if="summary?.top_companies?.length"
            :data="summary.top_companies"
            size="small"
            :show-header="true"
          >
            <el-table-column type="index" label="#" width="52" />
            <el-table-column prop="value" label="公司" show-overflow-tooltip />
            <el-table-column prop="count" label="岗位数" width="90" align="right" />
          </el-table>
          <el-empty v-else description="暂无公司数据" :image-size="70" />
        </div>
      </div>
    </section>

    <!-- ============ 最近任务记录 ============ -->
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><List /></el-icon>
          最近任务记录
        </div>
        <div class="dashboard__table-actions">
          <el-button size="small" :loading="refreshing" @click="onRefresh">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
          <el-button size="small" text type="primary" @click="router.push('/history')">
            全部记录
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </div>

      <el-table v-loading="store.loading" :data="recentTasks" size="default" stripe>
        <el-table-column prop="created_at" label="生成时间" min-width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="city" label="目标城市" width="100" />
        <el-table-column prop="keyword" label="搜索关键词" min-width="130" show-overflow-tooltip />
        <el-table-column label="抓取数量" width="110" align="right">
          <template #default="{ row }">
            <span>{{ row.total_found }}</span>
            <span v-if="row.new_count" class="dashboard__new">+{{ row.new_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ formatDuration(row.duration_seconds) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tooltip
              :disabled="!row.error_message"
              :content="row.error_message || ''"
              placement="top"
            >
              <el-tag :type="statusTagType(row.status)" size="small" effect="light">
                {{ row.status_label }}
              </el-tag>
            </el-tooltip>
            <el-progress
              v-if="row.status === 'running'"
              :percentage="row.progress"
              :stroke-width="3"
              :show-text="false"
              style="margin-top: 4px"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="router.push(`/jobs?task_id=${row.id}`)">
              查看详情
            </el-button>
            <el-button
              v-if="row.status === 'running' || row.status === 'pending'"
              link
              size="small"
              type="warning"
              @click="cancelTask(row)"
            >
              取消
            </el-button>
            <el-button
              v-else-if="row.status !== 'succeeded'"
              link
              size="small"
              type="primary"
              @click="retryTask(row)"
            >
              重试
            </el-button>
            <el-button
              v-if="row.total_found > 0"
              link
              size="small"
              @click="exportTask(row)"
            >
              导出
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有任务记录，点击「开始抓取岗位」创建第一个任务">
            <el-button type="primary" @click="router.push('/tasks')">开始抓取岗位</el-button>
          </el-empty>
        </template>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.dashboard__cards {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.dashboard__quick {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.dashboard__charts {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.quick-action {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 10px;
  border: 1px solid var(--bzp-border);
  background: #fbfcfe;
  cursor: pointer;
  text-align: left;
  transition: all 0.16s ease;
  font-family: inherit;
}

.quick-action:hover {
  border-color: #c9d9ff;
  background: #f4f8ff;
  transform: translateY(-1px);
  box-shadow: 0 6px 18px rgba(47, 107, 255, 0.1);
}

.quick-action__icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
  background: #eef3ff;
  color: var(--bzp-primary);
}

.quick-action--warning .quick-action__icon {
  background: #fff5e5;
  color: var(--bzp-warning);
}
.quick-action--success .quick-action__icon {
  background: #e9f9f2;
  color: var(--bzp-success);
}
.quick-action--info .quick-action__icon {
  background: #eef1f6;
  color: #64748b;
}

.quick-action__body {
  flex: 1;
  min-width: 0;
}

.quick-action__label {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--bzp-text);
}

.quick-action__desc {
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.5;
}

.quick-action__arrow {
  color: #b9c4d6;
  flex-shrink: 0;
}

.skills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.skills__tag {
  cursor: pointer;
}

.skills__count {
  margin-left: 5px;
  color: var(--bzp-text-secondary);
  font-size: 11px;
}

.dashboard__new {
  margin-left: 6px;
  color: var(--bzp-success);
  font-size: 12px;
}

.dashboard__table-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 1400px) {
  .dashboard__cards,
  .dashboard__quick {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .dashboard__cards,
  .dashboard__quick,
  .dashboard__charts {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
