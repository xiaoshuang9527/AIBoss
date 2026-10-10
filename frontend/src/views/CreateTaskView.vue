<script setup lang="ts">
/**
 * 创建抓取任务。
 *
 * 表单字段对应后端的 TaskCreate：
 * 关键词（必填）、城市（必填）、薪资/经验/学历（选填）、抓取页数上限。
 * 提交后进入后台队列，这里用 WebSocket + 轮询双保险展示实时进度。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { taskApi } from '@/api'
import { useWorkbenchStore } from '@/stores/workbench'
import { useTaskSocket } from '@/composables/useTaskSocket'
import { formatDuration, statusTagType } from '@/utils/format'
import type { ScrapeTask } from '@/types'

const router = useRouter()
const store = useWorkbenchStore()

const formRef = ref<FormInstance>()
const submitting = ref(false)

/** 常用城市（顺序即下拉展示顺序） */
const CITY_OPTIONS = [
  '全国',
  '北京',
  '上海',
  '深圳',
  '广州',
  '杭州',
  '成都',
  '武汉',
  '南京',
  '西安',
  '苏州',
  '长沙',
  '重庆',
  '天津',
  '青岛',
  '郑州',
  '合肥',
  '厦门',
  '福州',
  '东莞',
  '佛山',
  '宁波',
  '无锡',
  '济南',
  '大连',
  '沈阳',
  '哈尔滨',
  '昆明',
  '南昌',
  '贵阳',
  '南宁',
  '石家庄',
  '太原',
  '兰州',
  '乌鲁木齐',
  '海口',
  '赣州',
]

/** 经验要求代码（与 boss-zhipin-scraper 的 --experience 一致） */
const EXPERIENCE_OPTIONS = [
  { label: '不限', value: '' },
  { label: '在校生', value: '108' },
  { label: '应届生', value: '102' },
  { label: '经验不限', value: '101' },
  { label: '1 年以内', value: '103' },
  { label: '1-3 年', value: '104' },
  { label: '3-5 年', value: '105' },
  { label: '5-10 年', value: '106' },
  { label: '10 年以上', value: '107' },
]

/** 学历要求代码（--degree） */
const DEGREE_OPTIONS = [
  { label: '不限', value: '' },
  { label: '初中及以下', value: '209' },
  { label: '中专/中技', value: '208' },
  { label: '高中', value: '206' },
  { label: '大专', value: '202' },
  { label: '本科', value: '203' },
  { label: '硕士', value: '204' },
  { label: '博士', value: '205' },
]

/** 公司规模代码（--scale） */
const SCALE_OPTIONS = [
  { label: '不限', value: '' },
  { label: '0-20 人', value: '301' },
  { label: '20-99 人', value: '302' },
  { label: '100-499 人', value: '303' },
  { label: '500-999 人', value: '304' },
  { label: '1000-9999 人', value: '305' },
  { label: '10000 人以上', value: '306' },
]

const form = reactive({
  keyword: '',
  city: '深圳',
  salary: '',
  experience: '',
  degree: '',
  company_scale: '',
  max_pages: 3,
  with_detail: true,
  note: '',
})

const rules: FormRules = {
  keyword: [
    { required: true, message: '请输入求职方向 / 关键词', trigger: 'blur' },
    { min: 1, max: 128, message: '长度需在 1-128 个字符之间', trigger: 'blur' },
  ],
  city: [{ required: true, message: '请选择或输入目标城市', trigger: 'change' }],
  max_pages: [{ required: true, message: '请设置抓取页数上限', trigger: 'change' }],
}

const maxPagesLimit = computed(() => store.settings?.max_pages_per_task ?? 10)

/** 本次抓取的预计岗位数（每页 15 条，属经验值提示） */
const estimatedJobs = computed(() => form.max_pages * 15)

/** 当前跟踪的任务（提交后展示实时进度） */
const trackedTask = ref<ScrapeTask | null>(null)
const taskLogLines = ref<string[]>([])
const polling = ref(false)
let pollTimer: number | null = null

const delayHint = computed(() => {
  const settings = store.settings
  if (!settings) return '任务之间会自动插入随机延迟，降低触发风控的概率。'
  return `任务之间会在 ${settings.task_delay_min}-${settings.task_delay_max} 秒之间随机等待，最多同时执行 ${settings.max_concurrent_tasks} 个任务。`
})

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  if (store.cards.auth_status !== 'active') {
    try {
      await ElMessageBoxConfirm()
    } catch {
      return
    }
  }

  submitting.value = true
  try {
    const result = await taskApi.create({
      keyword: form.keyword.trim(),
      city: form.city.trim(),
      salary: form.salary.trim() || null,
      experience: form.experience || null,
      degree: form.degree || null,
      company_scale: form.company_scale || null,
      max_pages: form.max_pages,
      with_detail: form.with_detail,
      note: form.note.trim() || null,
    })
    trackedTask.value = result.task
    taskLogLines.value = []
    store.applyQueueEvent(result.queue)
    ElMessage.success(`任务 #${result.task.id} 已进入队列`)
    startPolling(result.task.id)
  } catch {
    // http 拦截器已经提示
  } finally {
    submitting.value = false
  }
}

async function ElMessageBoxConfirm() {
  const { ElMessageBox } = await import('element-plus')
  return ElMessageBox.confirm(
    '当前尚未授权 BOSS 直聘账号，抓取可能会因为登录态缺失而失败。是否仍要提交？',
    '登录态提示',
    { type: 'warning', confirmButtonText: '仍要提交', cancelButtonText: '去授权' },
  )
}

/* ---------------- 实时进度 ---------------- */
function startPolling(taskId: number) {
  stopPolling()
  polling.value = true
  pollTimer = window.setInterval(async () => {
    try {
      const [task, logs] = await Promise.all([taskApi.detail(taskId), taskApi.logs(taskId, 120)])
      trackedTask.value = task
      taskLogLines.value = logs.lines
      if (['succeeded', 'failed', 'cancelled', 'needs_attention'].includes(task.status)) {
        stopPolling()
        await store.loadDashboard()
        if (task.status === 'succeeded') {
          ElMessage.success(`抓取完成，新增入库 ${task.new_count} 条岗位`)
        } else {
          ElMessage.warning(task.error_message || '任务未能成功完成')
        }
      }
    } catch {
      stopPolling()
    }
  }, 2000)
}

function stopPolling() {
  polling.value = false
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

const { connected } = useTaskSocket((event) => {
  const data = event.data as { task_id?: number; progress?: number; stage_text?: string; status?: string }
  if (!trackedTask.value || data.task_id !== trackedTask.value.id) return

  if (event.type === 'task.progress') {
    trackedTask.value = {
      ...trackedTask.value,
      progress: data.progress ?? trackedTask.value.progress,
      stage_text: data.stage_text ?? trackedTask.value.stage_text,
    }
  }
  if (event.type === 'task.finished') {
    taskApi.detail(trackedTask.value.id).then((task) => {
      trackedTask.value = task
    })
    stopPolling()
    store.loadDashboard().catch(() => undefined)
  }
})

function resetForm() {
  formRef.value?.resetFields()
  form.keyword = ''
  form.salary = ''
  form.experience = ''
  form.degree = ''
  form.company_scale = ''
  form.max_pages = 3
  form.with_detail = true
  form.note = ''
}

async function cancelTracked() {
  if (!trackedTask.value) return
  await taskApi.cancel(trackedTask.value.id)
  ElMessage.success('已请求取消任务')
}

onMounted(async () => {
  if (!store.settings) {
    await store.loadSettings().catch(() => undefined)
  }
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="bzp-page">
    <div class="bzp-grid create__layout">
      <!-- ============ 表单 ============ -->
      <section class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><Promotion /></el-icon>
            创建抓取任务
          </div>
          <el-tag v-if="store.cards.auth_status !== 'active'" type="warning" size="small" effect="light">
            未授权，建议先扫码登录
          </el-tag>
        </div>

        <div class="bzp-panel__body">
          <el-form
            ref="formRef"
            :model="form"
            :rules="rules"
            label-width="112px"
            label-position="right"
          >
            <el-form-item label="求职方向" prop="keyword">
              <el-input
                v-model="form.keyword"
                placeholder="如：Python后端、前端、产品经理、AI Agent"
                clearable
                maxlength="128"
                show-word-limit
              />
            </el-form-item>

            <el-form-item label="目标城市" prop="city">
              <el-select
                v-model="form.city"
                filterable
                allow-create
                default-first-option
                placeholder="选择或输入城市"
                style="width: 100%"
              >
                <el-option v-for="city in CITY_OPTIONS" :key="city" :label="city" :value="city" />
              </el-select>
            </el-form-item>

            <el-form-item label="薪资要求">
              <el-input v-model="form.salary" placeholder="选填，如 15k-25k" clearable maxlength="64">
                <template #append>仅作记录</template>
              </el-input>
              <div class="create__hint">
                用于标注期望区间并高亮达标岗位，不会限制抓取结果（BOSS 接口按薪资码筛选）。
              </div>
            </el-form-item>

            <div class="bzp-grid bzp-grid--2">
              <el-form-item label="经验要求" label-width="112px">
                <el-select v-model="form.experience" placeholder="不限" clearable style="width: 100%">
                  <el-option
                    v-for="item in EXPERIENCE_OPTIONS"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-select>
              </el-form-item>

              <el-form-item label="学历要求" label-width="112px">
                <el-select v-model="form.degree" placeholder="不限" clearable style="width: 100%">
                  <el-option
                    v-for="item in DEGREE_OPTIONS"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-select>
              </el-form-item>
            </div>

            <el-form-item label="公司规模">
              <el-select v-model="form.company_scale" placeholder="不限" clearable style="width: 100%">
                <el-option
                  v-for="item in SCALE_OPTIONS"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="抓取数量上限" prop="max_pages">
              <el-slider
                v-model="form.max_pages"
                :min="1"
                :max="maxPagesLimit"
                :step="1"
                show-stops
                show-input
                :show-input-controls="false"
              />
              <div class="create__hint">
                最多 {{ maxPagesLimit }} 页（约 {{ estimatedJobs }} 条岗位）。页数越多耗时越久，
                也越容易触发风控，建议 2-3 页。
              </div>
            </el-form-item>

            <el-form-item label="抓取岗位详情">
              <el-switch v-model="form.with_detail" active-text="抓取" inactive-text="跳过" />
              <div class="create__hint">
                开启后会逐个打开岗位详情页提取「岗位职责 / 岗位要求」全文与技能标签，
                耗时明显增加，但结果更完整。
              </div>
            </el-form-item>

            <el-form-item label="备注">
              <el-input
                v-model="form.note"
                placeholder="选填，方便日后识别这条任务"
                clearable
                maxlength="255"
              />
            </el-form-item>

            <el-form-item>
              <el-button type="primary" :loading="submitting" @click="onSubmit">
                <el-icon><VideoPlay /></el-icon>
                提交并开始抓取
              </el-button>
              <el-button @click="resetForm">重置</el-button>
              <el-button text type="primary" @click="router.push('/auth')">
                去授权账号
              </el-button>
            </el-form-item>
          </el-form>
        </div>
      </section>

      <!-- ============ 进度 / 说明 ============ -->
      <div class="create__side">
        <section class="bzp-panel">
          <div class="bzp-panel__header">
            <div class="bzp-panel__title">
              <el-icon><Loading /></el-icon>
              任务实时进度
            </div>
            <el-tag :type="connected ? 'success' : 'info'" size="small" effect="plain">
              {{ connected ? '实时推送' : '轮询模式' }}
            </el-tag>
          </div>

          <div class="bzp-panel__body">
            <template v-if="trackedTask">
              <div class="progress__head">
                <div>
                  <div class="progress__title">
                    {{ trackedTask.keyword }} @ {{ trackedTask.city }}
                  </div>
                  <div class="bzp-muted progress__id">
                    任务 #{{ trackedTask.id }} · 最多 {{ trackedTask.max_pages }} 页
                  </div>
                </div>
                <el-tag :type="statusTagType(trackedTask.status)" size="small" effect="light">
                  {{ trackedTask.status_label }}
                </el-tag>
              </div>

              <el-progress
                :percentage="trackedTask.progress"
                :status="trackedTask.status === 'failed' ? 'exception' : undefined"
                :stroke-width="10"
                striped
                :striped-flow="trackedTask.status === 'running'"
              />

              <div class="progress__stage">{{ trackedTask.stage_text || '等待执行…' }}</div>

              <el-descriptions :column="3" size="small" border class="progress__stats">
                <el-descriptions-item label="命中">{{ trackedTask.total_found }}</el-descriptions-item>
                <el-descriptions-item label="新增">{{ trackedTask.new_count }}</el-descriptions-item>
                <el-descriptions-item label="详情">{{ trackedTask.detail_count }}</el-descriptions-item>
              </el-descriptions>

              <el-alert
                v-if="trackedTask.error_message"
                :title="trackedTask.error_message"
                type="warning"
                :closable="false"
                show-icon
                class="progress__alert"
              />

              <div v-if="taskLogLines.length" class="bzp-terminal progress__terminal">
                {{ taskLogLines.slice(-60).join('\n') }}
              </div>

              <div class="progress__actions">
                <el-button
                  v-if="['running', 'pending'].includes(trackedTask.status)"
                  size="small"
                  type="warning"
                  plain
                  @click="cancelTracked"
                >
                  取消任务
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  @click="router.push(`/jobs?task_id=${trackedTask.id}`)"
                >
                  查看抓取结果
                </el-button>
                <el-button size="small" text @click="router.push('/history')">查看历史记录</el-button>
              </div>
            </template>

            <el-empty v-else description="提交任务后，这里会实时展示抓取进度与日志" :image-size="72" />
          </div>
        </section>

        <section class="bzp-panel">
          <div class="bzp-panel__header">
            <div class="bzp-panel__title">
              <el-icon><InfoFilled /></el-icon>
              抓取节奏与风控提示
            </div>
          </div>
          <div class="bzp-panel__body">
            <ul class="tips">
              <li>{{ delayHint }}</li>
              <li>任务在后台队列中串行/并行执行，关闭页面不会中断抓取。</li>
              <li>登录态失效或触发风控时，任务会标记为「待处理」而不会盲目重试。</li>
              <li>每页约 15 条岗位；抓取频率过高会触发 BOSS 的安全校验，请耐心等待。</li>
              <li>数据仅供个人学习与求职分析使用，请勿用于商业倒卖。</li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.create__layout {
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  align-items: start;
}

.create__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.create__hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.6;
}

.progress__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}

.progress__title {
  font-size: 14px;
  font-weight: 600;
}

.progress__id {
  font-size: 11.5px;
  margin-top: 2px;
}

.progress__stage {
  margin: 10px 0;
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
  min-height: 18px;
}

.progress__stats {
  margin-bottom: 10px;
}

.progress__alert {
  margin-bottom: 10px;
}

.progress__terminal {
  max-height: 220px;
  margin-bottom: 12px;
}

.progress__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tips {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 2;
  color: var(--bzp-text-secondary);
}

@media (max-width: 1200px) {
  .create__layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
