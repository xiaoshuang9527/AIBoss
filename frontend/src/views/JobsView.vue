<script setup lang="ts">
/**
 * 岗位结果页。
 *
 * 核心交互：
 * 1. 多条件筛选（关键词 / 城市 / 薪资 / 经验 / 学历 / 技能 / 只看新增）；
 * 2. 表格与卡片两种视图；
 * 3. 点击「去 BOSS 沟通」精准跳转到该岗位的 BOSS 直聘详情页（带 lid / securityId 参数）；
 * 4. 展开查看岗位职责与岗位要求全文；
 * 5. 导出 CSV / JSON，支持收藏。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { exportApi, taskApi } from '@/api'
import { useJobStore } from '@/stores/job'
import { formatDateTime } from '@/utils/format'
import type { Job, ScrapeTask } from '@/types'

const route = useRoute()
const router = useRouter()
const store = useJobStore()

const viewMode = ref<'table' | 'card'>('table')
const detailVisible = ref(false)
const detailJob = ref<Job | null>(null)
const selectedRows = ref<Job[]>([])
const tasks = ref<ScrapeTask[]>([])
const exporting = ref(false)

/** 筛选条件 */
const filters = reactive({
  keyword: '',
  city: '',
  task_id: undefined as number | undefined,
  experience: '',
  degree: '',
  salary_min: undefined as number | undefined,
  salary_max: undefined as number | undefined,
  skill: '',
  only_new: false,
  only_favorite: false,
  only_with_jd: false,
  order_by: 'created_desc',
})

const salaryRange = ref<[number, number] | null>(null)

const orderOptions = [
  { label: '最新抓取', value: 'created_desc' },
  { label: '薪资从高到低', value: 'salary_desc' },
  { label: '薪资从低到高', value: 'salary_asc' },
]

const hasFilter = computed(() =>
  Boolean(
    filters.keyword ||
      filters.city ||
      filters.task_id ||
      filters.experience ||
      filters.degree ||
      filters.skill ||
      filters.only_new ||
      filters.only_favorite ||
      filters.only_with_jd ||
      salaryRange.value,
  ),
)

/** 当前选中的任务（用于展示上下文） */
const currentTask = computed(() =>
  filters.task_id ? tasks.value.find((item) => item.id === filters.task_id) ?? null : null,
)

/** 用户填写的薪资期望（来自任务），用于高亮达标岗位 */
const salaryExpectation = computed(() => {
  const raw = currentTask.value?.salary
  if (!raw) return null
  const numbers = (raw.match(/\d+(\.\d+)?/g) || []).map(Number)
  if (!numbers.length) return null
  if (numbers.length === 1) return { low: numbers[0], high: null as number | null }
  const [low, high] = numbers.slice(0, 2).sort((a, b) => a - b)
  return { low, high }
})

function isExpectationMet(job: Job): boolean {
  const expectation = salaryExpectation.value
  if (!expectation || job.salary_avg === null) return false
  if (expectation.high === null) return job.salary_avg >= expectation.low
  return job.salary_avg >= expectation.low
}

function syncFromRoute() {
  const query = route.query
  if (query.task_id) filters.task_id = Number(query.task_id)
  if (query.keyword) filters.keyword = String(query.keyword)
  if (query.city) filters.city = String(query.city)
  if (query.skill) filters.skill = String(query.skill)
  if (query.only_new) filters.only_new = query.only_new === '1'
}

function buildQuery() {
  const query: Record<string, unknown> = { order_by: filters.order_by }
  if (filters.keyword) query.keyword = filters.keyword
  if (filters.city) query.city = filters.city
  if (filters.task_id) query.task_id = filters.task_id
  if (filters.experience) query.experience = filters.experience
  if (filters.degree) query.degree = filters.degree
  if (filters.skill) query.skill = filters.skill
  if (filters.only_new) query.only_new = true
  if (filters.only_favorite) query.only_favorite = true
  if (filters.only_with_jd) query.only_with_jd = true
  if (salaryRange.value) {
    query.salary_min = salaryRange.value[0]
    query.salary_max = salaryRange.value[1]
  }
  return query
}

async function search(resetPage = true) {
  if (resetPage) store.page = 1
  await store.loadJobs({ ...buildQuery(), page: resetPage ? 1 : store.page })
}

function resetFilters() {
  filters.keyword = ''
  filters.city = ''
  filters.task_id = undefined
  filters.experience = ''
  filters.degree = ''
  filters.skill = ''
  filters.only_new = false
  filters.only_favorite = false
  filters.only_with_jd = false
  filters.order_by = 'created_desc'
  salaryRange.value = null
  router.replace({ path: '/jobs' })
  search()
}

function openDetail(job: Job) {
  detailJob.value = job
  detailVisible.value = true
  // 列表接口已带 JD 全文，若为空再单独拉一次详情
  if (!job.jd) {
    import('@/api').then(({ jobApi }) =>
      jobApi.detail(job.id).then((full) => {
        detailJob.value = full
      }),
    )
  }
}

/** 精准跳转到 BOSS 直聘岗位详情页 */
function goBoss(job: Job) {
  const url = job.job_link
  if (!url) {
    ElMessage.warning('该岗位缺少原始链接，无法跳转')
    return
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}

/** 跳转到公司主页 */
function openCompany(job: Job) {
  if (!job.company_link) return
  window.open(job.company_link, '_blank', 'noopener,noreferrer')
}

async function toggleFavorite(job: Job) {
  const value = await store.toggleFavorite(job)
  ElMessage.success(value ? '已收藏' : '已取消收藏')
}

async function exportJobs(fmt: 'csv' | 'json') {
  exporting.value = true
  try {
    await exportApi.jobs({
      fmt,
      task_id: filters.task_id,
      keyword: filters.keyword || undefined,
      city: filters.city || undefined,
      include_jd: true,
    })
    ElMessage.success(`已导出 ${store.total} 条岗位数据（${fmt.toUpperCase()}）`)
  } catch {
    ElMessage.error('导出失败，可能没有符合条件的数据')
  } finally {
    exporting.value = false
  }
}

async function removeSelected() {
  if (!selectedRows.value.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedRows.value.length} 条岗位记录？`,
      '删除岗位',
      { type: 'warning' },
    )
  } catch {
    return
  }
  const deleted = await store.removeJobs(selectedRows.value.map((item) => item.id))
  ElMessage.success(`已删除 ${deleted} 条`)
  selectedRows.value = []
  await store.loadFacets()
}

/** 详情弹窗里分段渲染 JD */
const detailSections = computed(() => {
  const job = detailJob.value
  if (!job) return []
  const sections: { title: string; content: string }[] = []
  if (job.job_duty) sections.push({ title: '岗位职责', content: job.job_duty })
  if (job.job_requirement) sections.push({ title: '岗位要求', content: job.job_requirement })
  if (!sections.length && job.jd) sections.push({ title: '岗位详情', content: job.jd })
  return sections
})

watch(
  () => route.query,
  async () => {
    syncFromRoute()
    await search()
  },
)

onMounted(async () => {
  syncFromRoute()
  await Promise.all([
    store.loadFacets().catch(() => undefined),
    taskApi.list({ page: 1, page_size: 50 }).then((response) => {
      tasks.value = response.items
    }),
    search(false),
  ])
})
</script>

<template>
  <div class="bzp-page">
    <!-- ============ 筛选区 ============ -->
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><Filter /></el-icon>
          岗位筛选
        </div>
        <div class="jobs__toolbar">
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button value="table">
              <el-icon><List /></el-icon>
              表格
            </el-radio-button>
            <el-radio-button value="card">
              <el-icon><Grid /></el-icon>
              卡片
            </el-radio-button>
          </el-radio-group>
          <el-button size="small" type="primary" plain :loading="exporting" @click="exportJobs('csv')">
            <el-icon><Download /></el-icon>
            导出 CSV
          </el-button>
          <el-button size="small" plain :loading="exporting" @click="exportJobs('json')">
            导出 JSON
          </el-button>
        </div>
      </div>

      <div class="bzp-panel__body">
        <el-form :inline="true" class="jobs__filters">
          <el-form-item label="关键词">
            <el-input
              v-model="filters.keyword"
              placeholder="岗位 / 公司 / JD 内容"
              clearable
              style="width: 200px"
              @keyup.enter="search()"
            />
          </el-form-item>

          <el-form-item label="城市">
            <el-select
              v-model="filters.city"
              placeholder="全部"
              clearable
              filterable
              style="width: 140px"
            >
              <el-option
                v-for="item in store.facets?.cities ?? []"
                :key="item.value"
                :label="`${item.value} (${item.count})`"
                :value="item.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="来源任务">
            <el-select
              v-model="filters.task_id"
              placeholder="全部任务"
              clearable
              filterable
              style="width: 220px"
            >
              <el-option
                v-for="task in tasks"
                :key="task.id"
                :label="`#${task.id} ${task.keyword} @ ${task.city}`"
                :value="task.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="经验">
            <el-select v-model="filters.experience" placeholder="不限" clearable style="width: 130px">
              <el-option
                v-for="item in store.facets?.experiences ?? []"
                :key="item.value"
                :label="item.value"
                :value="item.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="学历">
            <el-select v-model="filters.degree" placeholder="不限" clearable style="width: 120px">
              <el-option
                v-for="item in store.facets?.degrees ?? []"
                :key="item.value"
                :label="item.value"
                :value="item.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="月薪(K)">
            <el-slider
              v-model="salaryRange"
              range
              :min="0"
              :max="100"
              :step="5"
              style="width: 190px"
              :marks="{ 0: '0', 50: '50', 100: '100+' }"
            />
          </el-form-item>

          <el-form-item label="技能">
            <el-select
              v-model="filters.skill"
              placeholder="不限"
              clearable
              filterable
              style="width: 150px"
            >
              <el-option
                v-for="item in store.facets?.skills ?? []"
                :key="item.value"
                :label="`${item.value} (${item.count})`"
                :value="item.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="排序">
            <el-select v-model="filters.order_by" style="width: 150px">
              <el-option
                v-for="item in orderOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item>
            <el-checkbox v-model="filters.only_new">只看新增</el-checkbox>
            <el-checkbox v-model="filters.only_favorite">只看收藏</el-checkbox>
            <el-checkbox v-model="filters.only_with_jd">只看有 JD</el-checkbox>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="store.loading" @click="search()">
              <el-icon><Search /></el-icon>
              查询
            </el-button>
            <el-button :disabled="!hasFilter" @click="resetFilters">重置</el-button>
          </el-form-item>
        </el-form>

        <div v-if="currentTask" class="jobs__context">
          <el-icon><InfoFilled /></el-icon>
          当前查看任务 #{{ currentTask.id }}：<strong>{{ currentTask.keyword }}</strong>
          @ {{ currentTask.city }}
          <span v-if="salaryExpectation" class="jobs__expectation">
            · 期望薪资 {{ currentTask.salary }}，达标岗位会显示
            <el-tag type="success" size="small" effect="light" round>达标</el-tag>
          </span>
        </div>

        <div class="jobs__summary">
          共 <strong>{{ store.total }}</strong> 条岗位
          <template v-if="selectedRows.length">
            · 已选 {{ selectedRows.length }} 条
            <el-button link type="danger" size="small" @click="removeSelected">删除选中</el-button>
          </template>
        </div>
      </div>
    </section>

    <!-- ============ 表格视图 ============ -->
    <section v-if="viewMode === 'table'" class="bzp-panel">
      <el-table
        v-loading="store.loading"
        :data="store.jobs"
        stripe
        row-key="id"
        @selection-change="(rows: Job[]) => (selectedRows = rows)"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column label="岗位名称" min-width="200">
          <template #default="{ row }">
            <div class="jobs__title-cell">
              <el-button link type="primary" class="jobs__title" @click="openDetail(row)">
                {{ row.title }}
              </el-button>
              <el-tag v-if="row.is_new" type="success" size="small" effect="light" round>新</el-tag>
              <el-tag
                v-if="isExpectationMet(row)"
                type="success"
                size="small"
                effect="dark"
                round
              >
                达标
              </el-tag>
              <el-tag v-if="row.is_favorite" type="warning" size="small" effect="light" round>
                已收藏
              </el-tag>
            </div>
            <div class="jobs__sub bzp-muted">{{ row.company }}</div>
          </template>
        </el-table-column>

        <el-table-column label="薪资范围" width="130" align="right">
          <template #default="{ row }">
            <span class="jobs__salary">{{ row.salary || '面议' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="经验/学历" width="140">
          <template #default="{ row }">
            <div>{{ row.experience || '经验不限' }}</div>
            <div class="bzp-muted jobs__sub">{{ row.degree || '学历不限' }}</div>
          </template>
        </el-table-column>

        <el-table-column label="城市" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.location || row.city || '—' }}</template>
        </el-table-column>

        <el-table-column label="技能标签" min-width="220">
          <template #default="{ row }">
            <template v-if="row.skill_keywords.length">
              <el-tag
                v-for="skill in row.skill_keywords.slice(0, 4)"
                :key="skill"
                size="small"
                effect="plain"
                class="jobs__skill"
              >
                {{ skill }}
              </el-tag>
              <el-tooltip v-if="row.skill_keywords.length > 4" placement="top">
                <template #content>
                  <div>{{ row.skill_keywords.join('、') }}</div>
                </template>
                <el-tag size="small" type="info" effect="plain" class="jobs__skill">
                  +{{ row.skill_keywords.length - 4 }}
                </el-tag>
              </el-tooltip>
            </template>
            <span v-else class="bzp-muted">—</span>
          </template>
        </el-table-column>

        <el-table-column label="公司信息" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="jobs__sub">{{ row.company_scale || '—' }}</div>
            <div class="bzp-muted jobs__sub">{{ row.company_stage || '—' }}</div>
          </template>
        </el-table-column>

        <el-table-column label="招聘者" width="130" show-overflow-tooltip>
          <template #default="{ row }">
            <div>{{ row.boss_name || '—' }}</div>
            <div class="bzp-muted jobs__sub">
              {{ row.boss_active_status || row.boss_title || '—' }}
            </div>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" @click="goBoss(row)">
              <el-icon><ChatDotRound /></el-icon>
              去 BOSS 沟通
            </el-button>
            <el-button link size="small" @click="openDetail(row)">详情</el-button>
            <el-button link size="small" @click="toggleFavorite(row)">
              {{ row.is_favorite ? '取消收藏' : '收藏' }}
            </el-button>
            <el-button
              v-if="row.company_link"
              link
              size="small"
              @click="openCompany(row)"
            >
              公司
            </el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="还没有岗位数据，先去创建一个抓取任务吧">
            <el-button type="primary" @click="router.push('/tasks')">开始抓取岗位</el-button>
          </el-empty>
        </template>
      </el-table>

      <div class="jobs__pagination">
        <el-pagination
          v-model:current-page="store.page"
          v-model:page-size="store.pageSize"
          :total="store.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="() => store.loadJobs({ ...buildQuery(), page: store.page })"
          @size-change="() => search()"
        />
      </div>
    </section>

    <!-- ============ 卡片视图 ============ -->
    <section v-else v-loading="store.loading" class="jobs__cards">
      <article v-for="job in store.jobs" :key="job.id" class="job-card bzp-panel">
        <header class="job-card__head">
          <div class="job-card__title-wrap">
            <h3 class="job-card__title" @click="openDetail(job)">{{ job.title }}</h3>
            <div class="job-card__company">
              {{ job.company }}
              <el-tag v-if="job.is_new" type="success" size="small" effect="light" round>新</el-tag>
              <el-tag v-if="isExpectationMet(job)" type="success" size="small" effect="dark" round>
                达标
              </el-tag>
            </div>
          </div>
          <div class="job-card__salary">{{ job.salary || '面议' }}</div>
        </header>

        <div class="job-card__meta">
          <span><el-icon><Location /></el-icon>{{ job.location || job.city || '—' }}</span>
          <span><el-icon><Suitcase /></el-icon>{{ job.experience || '经验不限' }}</span>
          <span><el-icon><Reading /></el-icon>{{ job.degree || '学历不限' }}</span>
        </div>

        <div v-if="job.skill_keywords.length" class="job-card__skills">
          <el-tag
            v-for="skill in job.skill_keywords.slice(0, 6)"
            :key="skill"
            size="small"
            effect="plain"
          >
            {{ skill }}
          </el-tag>
        </div>

        <p v-if="job.jd" class="job-card__jd">{{ job.jd.slice(0, 120) }}…</p>

        <footer class="job-card__footer">
          <span class="bzp-muted job-card__time">{{ formatDateTime(job.scraped_at) }}</span>
          <div class="job-card__actions">
            <el-button link size="small" @click="toggleFavorite(job)">
              {{ job.is_favorite ? '取消收藏' : '收藏' }}
            </el-button>
            <el-button link size="small" @click="openDetail(job)">详情</el-button>
            <el-button type="primary" size="small" @click="goBoss(job)">
              去 BOSS 沟通
            </el-button>
          </div>
        </footer>
      </article>

      <el-empty v-if="!store.jobs.length && !store.loading" description="没有符合条件的岗位">
        <el-button type="primary" @click="router.push('/tasks')">开始抓取岗位</el-button>
      </el-empty>

      <div v-if="store.jobs.length" class="jobs__pagination jobs__pagination--full">
        <el-pagination
          v-model:current-page="store.page"
          v-model:page-size="store.pageSize"
          :total="store.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="() => store.loadJobs({ ...buildQuery(), page: store.page })"
          @size-change="() => search()"
        />
      </div>
    </section>

    <!-- ============ 岗位详情弹窗 ============ -->
    <el-dialog
      v-model="detailVisible"
      :title="detailJob?.title || '岗位详情'"
      width="820px"
      top="6vh"
      destroy-on-close
    >
      <template v-if="detailJob">
        <div class="detail__head">
          <div>
            <div class="detail__company">{{ detailJob.company }}</div>
            <div class="detail__tags">
              <el-tag type="danger" effect="light">{{ detailJob.salary || '面议' }}</el-tag>
              <el-tag type="info" effect="plain">{{ detailJob.experience || '经验不限' }}</el-tag>
              <el-tag type="info" effect="plain">{{ detailJob.degree || '学历不限' }}</el-tag>
              <el-tag v-if="detailJob.city" type="info" effect="plain">{{ detailJob.city }}</el-tag>
              <el-tag v-if="detailJob.company_scale" type="info" effect="plain">
                {{ detailJob.company_scale }}
              </el-tag>
              <el-tag v-if="detailJob.company_stage" type="info" effect="plain">
                {{ detailJob.company_stage }}
              </el-tag>
            </div>
          </div>
          <el-button type="primary" @click="goBoss(detailJob)">
            <el-icon><ChatDotRound /></el-icon>
            去 BOSS 沟通
          </el-button>
        </div>

        <el-descriptions :column="2" size="small" border class="detail__desc">
          <el-descriptions-item label="详细地址">
            {{ detailJob.location || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="招聘者">
            {{ detailJob.boss_name || '—' }}
            <span v-if="detailJob.boss_title" class="bzp-muted">· {{ detailJob.boss_title }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="活跃状态">
            {{ detailJob.boss_active_status || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="所属行业">
            {{ detailJob.company_industry || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="福利待遇" :span="2">
            {{ detailJob.welfare || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="抓取时间" :span="2">
            {{ formatDateTime(detailJob.scraped_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="detailJob.skill_keywords.length" class="detail__skills">
          <div class="detail__section-title">技能标签</div>
          <el-tag
            v-for="skill in detailJob.skill_keywords"
            :key="skill"
            effect="plain"
            class="jobs__skill"
          >
            {{ skill }}
          </el-tag>
        </div>

        <div class="detail__jd">
          <template v-if="detailSections.length">
            <div v-for="section in detailSections" :key="section.title">
              <div class="bzp-jd__section-title">{{ section.title }}</div>
              <div class="bzp-jd">{{ section.content }}</div>
            </div>
          </template>
          <el-empty v-else description="该岗位未抓取到详情全文（可在创建任务时开启「抓取岗位详情」）" :image-size="70" />
        </div>

        <div v-if="detailJob.job_link" class="detail__link">
          <span class="bzp-muted">原始链接：</span>
          <a :href="detailJob.job_link" target="_blank" rel="noopener noreferrer">
            {{ detailJob.job_link }}
          </a>
        </div>
      </template>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button
          v-if="detailJob"
          type="primary"
          plain
          @click="detailJob && toggleFavorite(detailJob)"
        >
          {{ detailJob.is_favorite ? '取消收藏' : '收藏岗位' }}
        </el-button>
        <el-button type="primary" @click="detailJob && goBoss(detailJob)">
          去 BOSS 沟通
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.jobs__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.jobs__filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 0 4px;
}

.jobs__context {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 9px 14px;
  border-radius: 8px;
  background: #f4f8ff;
  border: 1px solid #dbe6ff;
  font-size: 12.5px;
  color: #2b4a80;
  margin-bottom: 12px;
}

.jobs__expectation {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.jobs__summary {
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.jobs__title-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.jobs__title {
  font-size: 13.5px;
  font-weight: 600;
  text-align: left;
  padding: 0;
  height: auto;
}

.jobs__sub {
  font-size: 11.5px;
  margin-top: 2px;
}

.jobs__salary {
  color: var(--bzp-danger);
  font-weight: 600;
}

.jobs__skill {
  margin: 0 4px 4px 0;
}

.jobs__pagination {
  display: flex;
  justify-content: flex-end;
  padding: 14px 18px;
  border-top: 1px solid var(--bzp-border);
}

.jobs__pagination--full {
  width: 100%;
  border: none;
}

.jobs__cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.job-card {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.job-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 26px rgba(16, 26, 44, 0.1);
}

.job-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.job-card__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  color: var(--bzp-text);
}

.job-card__title:hover {
  color: var(--bzp-primary);
}

.job-card__company {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.job-card__salary {
  color: var(--bzp-danger);
  font-weight: 700;
  font-size: 15px;
  white-space: nowrap;
}

.job-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: var(--bzp-text-secondary);
}

.job-card__meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.job-card__skills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.job-card__jd {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: #5a6b85;
}

.job-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px solid var(--bzp-border);
  padding-top: 10px;
  margin-top: auto;
}

.job-card__time {
  font-size: 11.5px;
}

.job-card__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* ---- 详情弹窗 ---- */
.detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.detail__company {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 8px;
}

.detail__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.detail__desc {
  margin-bottom: 14px;
}

.detail__section-title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--bzp-text);
}

.detail__skills {
  margin-bottom: 14px;
}

.detail__jd {
  max-height: 360px;
  overflow-y: auto;
  padding: 14px 16px;
  border-radius: 8px;
  background: #fafbfe;
  border: 1px solid var(--bzp-border);
}

.detail__link {
  margin-top: 12px;
  font-size: 12px;
  word-break: break-all;
}
</style>
