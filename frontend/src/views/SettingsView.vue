<script setup lang="ts">
/**
 * 系统设置页。
 *
 * 只包含运行参数：并发数、抓取间隔、重试次数、页数上限、超时与请求预算。
 * 再次强调：本工具为个人求职辅助，**没有任何额度 / 套餐 / 计费设置**。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { systemApi } from '@/api'
import ModelManagerPanel from '@/components/models/ModelManagerPanel.vue'
import UsagePanel from '@/components/chat/UsagePanel.vue'
import { useModelStore } from '@/stores/model'
import { useWorkbenchStore } from '@/stores/workbench'
import { formatBytes, formatDateTime } from '@/utils/format'
import type { ScraperHealth, StorageUsage, SystemLog } from '@/types'

const store = useWorkbenchStore()
const modelStore = useModelStore()

/** 当前 Tab。模型管理独立成一个 Tab，避免系统设置页被撑得过长 */
const activeTab = ref<'system' | 'models' | 'usage'>('system')

const saving = ref(false)
const loadingLogs = ref(false)
const health = ref<ScraperHealth | null>(null)
const storage = ref<StorageUsage | null>(null)
const logs = ref<SystemLog[]>([])
const logPage = ref(1)
const logTotal = ref(0)
const logLevel = ref('')
const logCategory = ref('')

const form = reactive({
  max_concurrent_tasks: 2,
  task_delay_min: 3,
  task_delay_max: 5,
  max_retries: 1,
  max_pages_per_task: 10,
  task_timeout_seconds: 1800,
  request_budget: 500,
})

const levelOptions = [
  { label: '全部级别', value: '' },
  { label: '调试', value: 'debug' },
  { label: '信息', value: 'info' },
  { label: '警告', value: 'warning' },
  { label: '错误', value: 'error' },
]

const categoryOptions = [
  { label: '全部分类', value: '' },
  { label: '系统', value: 'system' },
  { label: '授权', value: 'auth' },
  { label: '任务', value: 'task' },
  { label: '爬虫', value: 'scraper' },
  { label: '导出', value: 'export' },
]

const levelTagType = (level: string) => {
  if (level === 'error') return 'danger'
  if (level === 'warning') return 'warning'
  if (level === 'debug') return 'info'
  return 'success'
}

const scraperOk = computed(() => health.value?.entry_exists === true)

async function loadSettings() {
  const settings = await store.loadSettings()
  Object.assign(form, {
    max_concurrent_tasks: settings.max_concurrent_tasks,
    task_delay_min: settings.task_delay_min,
    task_delay_max: settings.task_delay_max,
    max_retries: settings.max_retries,
    max_pages_per_task: settings.max_pages_per_task,
    task_timeout_seconds: settings.task_timeout_seconds,
    request_budget: settings.request_budget,
  })
}

async function loadHealth() {
  health.value = await systemApi.scraperHealth().catch(() => null)
  storage.value = await systemApi.storage().catch(() => null)
}

async function loadLogs() {
  loadingLogs.value = true
  try {
    const response = await systemApi.logs({
      page: logPage.value,
      page_size: 30,
      level: logLevel.value || undefined,
      category: logCategory.value || undefined,
    })
    logs.value = response.items
    logTotal.value = response.meta.total
  } finally {
    loadingLogs.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await store.updateSettings({
      max_concurrent_tasks: form.max_concurrent_tasks,
      task_delay_min: form.task_delay_min,
      task_delay_max: form.task_delay_max,
      max_retries: form.max_retries,
      max_pages_per_task: form.max_pages_per_task,
      task_timeout_seconds: form.task_timeout_seconds,
      request_budget: form.request_budget,
    })
    ElMessage.success('设置已保存（对后续任务生效）')
    await loadSettings()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function purgeLogs() {
  try {
    await ElMessageBox.confirm('确认清理 30 天前的系统日志？', '清理日志', { type: 'warning' })
  } catch {
    return
  }
  await systemApi.purgeLogs(30)
  ElMessage.success('已清理')
  await loadLogs()
}

async function scanStale() {
  const result = await systemApi.scanStale()
  ElMessage.success(`扫描完成，修正 ${result.fixed} 个异常任务`)
}

onMounted(async () => {
  await Promise.all([
    loadSettings().catch(() => ElMessage.error('读取设置失败')),
    loadHealth(),
    loadLogs(),
  ])
})
</script>

<template>
  <div class="bzp-page">
    <el-tabs v-model="activeTab" class="settings__tabs">
      <!-- ============================================================
           Tab 1：系统设置（原有内容）
           ============================================================ -->
      <el-tab-pane name="system">
        <template #label>
          <span class="settings__tab-label">
            <el-icon><Setting /></el-icon>
            系统设置
          </span>
        </template>

        <div class="settings__tab-body">
          <div class="bzp-grid bzp-grid--2">
      <!-- ============ 抓取参数 ============ -->
      <section class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><Setting /></el-icon>
            抓取与队列参数
          </div>
          <el-button size="small" type="primary" :loading="saving" @click="save">
            <el-icon><Check /></el-icon>
            保存
          </el-button>
        </div>

        <div class="bzp-panel__body">
          <el-form label-width="150px" label-position="right">
            <el-form-item label="并发任务上限">
              <el-input-number v-model="form.max_concurrent_tasks" :min="1" :max="4" />
              <div class="settings__hint">同时执行的爬虫任务数，建议 1-2，过高会显著提高风控概率。</div>
            </el-form-item>

            <el-form-item label="任务间隔（秒）">
              <div class="settings__inline">
                <el-input-number v-model="form.task_delay_min" :min="0" :max="120" :step="1" />
                <span class="bzp-muted">至</span>
                <el-input-number v-model="form.task_delay_max" :min="0" :max="300" :step="1" />
              </div>
              <div class="settings__hint">相邻任务之间随机等待的区间，用于模拟人类节奏。</div>
            </el-form-item>

            <el-form-item label="失败自动重试次数">
              <el-input-number v-model="form.max_retries" :min="0" :max="5" />
              <div class="settings__hint">
                仅对网络抖动类失败重试；登录态失效与风控拦截不会重试。
              </div>
            </el-form-item>

            <el-form-item label="单任务页数上限">
              <el-input-number v-model="form.max_pages_per_task" :min="1" :max="10" />
              <div class="settings__hint">创建任务时允许选择的最大页数（每页约 15 条岗位）。</div>
            </el-form-item>

            <el-form-item label="单任务超时（秒）">
              <el-input-number v-model="form.task_timeout_seconds" :min="120" :max="7200" :step="60" />
              <div class="settings__hint">超过该时长会终止抓取并保留已抓到的数据。</div>
            </el-form-item>

            <el-form-item label="单次请求预算">
              <el-input-number v-model="form.request_budget" :min="50" :max="5000" :step="50" />
              <div class="settings__hint">软性提示值，用于自我约束单次任务的页面请求数量。</div>
            </el-form-item>
          </el-form>

          <el-alert type="info" :closable="false" class="settings__alert">
            <template #title>关于计费</template>
            <template #default>
              本工作台是纯个人求职辅助工具，<strong>不存在任何额度、套餐、充值或计费功能</strong>，
              上方的“请求预算”只是给自己看的抓取频率约束。
            </template>
          </el-alert>
        </div>
      </section>

      <!-- ============ 环境信息 ============ -->
      <div class="settings__side">
        <section class="bzp-panel">
          <div class="bzp-panel__header">
            <div class="bzp-panel__title">
              <el-icon><Cpu /></el-icon>
              爬虫集成状态
            </div>
            <el-tag
              :type="scraperOk ? (health?.authorized ? 'success' : 'warning') : 'danger'"
              size="small"
              effect="light"
            >
              {{ scraperOk ? (health?.authorized ? '已就绪' : '待登录') : '未就绪' }}
            </el-tag>
          </div>
          <div class="bzp-panel__body">
            <el-alert
              v-if="health?.message"
              :type="health.authorized ? 'success' : 'warning'"
              :title="health.message"
              :closable="false"
              show-icon
              class="settings__health-alert"
            />
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="爬虫目录">
                <span class="bzp-mono">{{ health?.scraper_dir || '—' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="主脚本">
                <el-tag :type="health?.entry_exists ? 'success' : 'danger'" size="small" effect="plain">
                  {{ health?.entry_exists ? '存在' : '缺失' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="爬虫版本">
                {{ health?.version || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="Python 解释器">
                <span class="bzp-mono">{{ health?.python_executable || '—' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="CDP 端口">
                {{ health?.cdp_port }} ·
                <el-tag :type="health?.cdp_reachable ? 'success' : 'info'" size="small" effect="plain">
                  {{ health?.cdp_reachable ? '已连接' : '未启动' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="登录态">
                <el-tag :type="health?.authorized ? 'success' : 'danger'" size="small" effect="plain">
                  {{ health?.authorized ? '已授权' : '未登录' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="隔离 Profile">
                <el-tag :type="health?.profile_exists ? 'success' : 'info'" size="small" effect="plain">
                  {{ health?.profile_exists ? '已创建' : '未创建' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="存储目录">
                <span class="bzp-mono">{{ store.settings?.storage_root || '—' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="数据库">
                {{ store.settings?.database_dialect || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="后端版本">
                {{ store.settings?.app_version || '—' }}
              </el-descriptions-item>
            </el-descriptions>

            <div class="settings__actions">
              <el-button size="small" @click="loadHealth">
                <el-icon><Refresh /></el-icon>
                重新检测
              </el-button>
              <el-button size="small" @click="scanStale">扫描异常任务</el-button>
            </div>
          </div>
        </section>

        <section class="bzp-panel">
          <div class="bzp-panel__header">
            <div class="bzp-panel__title">
              <el-icon><FolderOpened /></el-icon>
              存储占用
            </div>
          </div>
          <div class="bzp-panel__body">
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="总占用">
                {{ formatBytes(storage?.total_bytes) }}
              </el-descriptions-item>
              <el-descriptions-item label="剩余磁盘">
                {{ formatBytes(storage?.disk_free_bytes) }}
              </el-descriptions-item>
              <el-descriptions-item
                v-for="(size, name) in storage?.breakdown || {}"
                :key="name"
                :label="String(name)"
              >
                {{ formatBytes(size) }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </section>
      </div>
    </div>

    <!-- ============ 操作日志 ============ -->
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><Document /></el-icon>
          系统操作日志
        </div>
        <div class="settings__toolbar">
          <el-select v-model="logLevel" size="small" style="width: 130px" @change="loadLogs">
            <el-option v-for="item in levelOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-select v-model="logCategory" size="small" style="width: 130px" @change="loadLogs">
            <el-option
              v-for="item in categoryOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-button size="small" @click="loadLogs">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
          <el-button size="small" type="danger" plain @click="purgeLogs">清理 30 天前</el-button>
        </div>
      </div>

      <el-table v-loading="loadingLogs" :data="logs" size="small" stripe>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="级别" width="90">
          <template #default="{ row }">
            <el-tag :type="levelTagType(row.level)" size="small" effect="light">{{ row.level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="100" />
        <el-table-column prop="message" label="内容" min-width="320" show-overflow-tooltip />
        <el-table-column label="关联任务" width="100">
          <template #default="{ row }">
            <el-button
              v-if="row.task_id"
              link
              size="small"
              type="primary"
              @click="$router.push(`/jobs?task_id=${row.task_id}`)"
            >
              #{{ row.task_id }}
            </el-button>
            <span v-else class="bzp-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="settings__pagination">
        <el-pagination
          v-model:current-page="logPage"
          :page-size="30"
          :total="logTotal"
          layout="total, prev, pager, next"
          background
          @current-change="loadLogs"
        />
      </div>
    </section>
        </div>
      </el-tab-pane>

      <!-- ============================================================
           Tab 2：模型管理
           ============================================================ -->
      <el-tab-pane name="models">
        <template #label>
          <span class="settings__tab-label">
            <el-icon><Cpu /></el-icon>
            模型管理
            <el-badge
              v-if="modelStore.enabledModels.length"
              :value="modelStore.enabledModels.length"
              type="primary"
              class="settings__tab-badge"
            />
          </span>
        </template>

        <div class="settings__tab-body">
          <ModelManagerPanel />
        </div>
      </el-tab-pane>

      <!-- ============================================================
           Tab 3：用量统计（token 维度，不含任何计费概念）
           ============================================================ -->
      <el-tab-pane name="usage">
        <template #label>
          <span class="settings__tab-label">
            <el-icon><DataLine /></el-icon>
            用量统计
          </span>
        </template>

        <div class="settings__tab-body">
          <UsagePanel />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.settings__tabs {
  --el-tabs-header-height: 46px;
}

.settings__tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
}

.settings__tab-badge {
  margin-left: 4px;
}

.settings__tab-body {
  padding-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings__hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.6;
}

.settings__inline {
  display: flex;
  align-items: center;
  gap: 8px;
}

.settings__alert {
  margin-top: 10px;
}

.settings__health-alert {
  margin-bottom: 14px;
}

.settings__actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}

.settings__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.settings__pagination {
  display: flex;
  justify-content: flex-end;
  padding: 14px 18px;
  border-top: 1px solid var(--bzp-border);
}
</style>
