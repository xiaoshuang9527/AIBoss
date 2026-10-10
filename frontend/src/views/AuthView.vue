<script setup lang="ts">
/**
 * 账号授权页。
 *
 * 流程：点击「开始授权」→ 后端启动专用浏览器并打开 BOSS 登录页 →
 * 用户在浏览器窗口扫码 → 后端轮询登录态并加密保存 → 页面实时反馈结果。
 *
 * 注意：这一步必须在**有界面**的机器上完成（本机运行 / X11 转发 / VNC）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { authApi } from '@/api'
import { useWorkbenchStore } from '@/stores/workbench'
import { useAuthSocket } from '@/composables/useAuthSocket'
import { formatDateTime, fromNow, authTagType } from '@/utils/format'
import type { AuthPhase, AuthProfile, AuthSession, AuthStatusPayload } from '@/types'

const store = useWorkbenchStore()

const status = ref<AuthStatusPayload | null>(null)
const profiles = ref<AuthProfile[]>([])
const session = ref<AuthSession | null>(null)
const loading = ref(false)
const verifying = ref(false)
const starting = ref(false)

const browser = ref<'chrome' | 'edge'>('chrome')
const copyLoginState = ref(false)
const resetProfile = ref(false)
/** 服务端拉不起浏览器时，改为接管手动启动的实例 */
const externalBrowser = ref(false)

/** 手动启动专用浏览器的命令（一键复制） */
const launchCommand = computed(() => {
  const profileDir = '<项目根目录>\\backend\\storage\\chrome-profiles\\.boss-zhipin-scraper\\chrome-profile'
  const port = profile.value?.cdp_port ?? 9222
  const exe = browser.value === 'edge' ? 'msedge.exe' : 'chrome.exe'
  return (
    `& "${exe}" --remote-debugging-port=${port} ` +
    `--user-data-dir="${profileDir}" ` +
    `--no-first-run --no-default-browser-check --remote-allow-origins=*`
  )
})

async function copyLaunchCommand() {
  try {
    await navigator.clipboard.writeText(launchCommand.value)
    ElMessage.success('启动命令已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选中命令文本复制')
  }
}

const phaseSteps: AuthPhase[] = ['starting', 'awaiting_login', 'verifying', 'success']
const activeStep = computed(() => {
  const phase = session.value?.phase
  if (!phase) return -1
  if (phase === 'failed' || phase === 'cancelled') return -1
  return phaseSteps.indexOf(phase)
})

/** 是否有一个仍在进行中的授权会话（进行中时禁止重复发起） */
const sessionBusy = computed(() => {
  const phase = session.value?.phase
  if (!phase) return false
  return ['starting', 'awaiting_login', 'verifying'].includes(phase)
})

const profile = computed(() => status.value?.profile ?? null)

const statusTone = computed(() => {
  const value = profile.value?.status
  if (value === 'active') return 'success'
  if (value === 'authorizing') return 'warning'
  return 'danger'
})

const statusTitle = computed(() => {
  const value = profile.value?.status
  if (value === 'active') return '授权有效'
  if (value === 'authorizing') return '等待扫码'
  if (value === 'expired') return '登录态已失效'
  return '尚未授权'
})

const statusDescription = computed(() => {
  const profileValue = profile.value
  if (!profileValue) return '正在读取授权信息…'
  if (profileValue.status === 'active') {
    return `登录态有效，最近校验：${formatDateTime(profileValue.last_verified_at)}（${fromNow(
      profileValue.last_verified_at,
    )}）`
  }
  if (profileValue.status === 'authorizing') {
    return '专用浏览器已启动，请在浏览器窗口内扫码登录。'
  }
  return profileValue.last_error || '请点击下方按钮启动扫码授权。'
})

async function loadStatus() {
  loading.value = true
  try {
    const [statusPayload, profileList, current] = await Promise.all([
      authApi.status(),
      authApi.profiles(),
      authApi.currentSession(),
    ])
    status.value = statusPayload
    profiles.value = profileList
    session.value = current
    if (current && ['starting', 'awaiting_login', 'verifying'].includes(current.phase)) {
      startPolling()
    }
  } finally {
    loading.value = false
  }
}

async function startAuth() {
  starting.value = true
  try {
    const created = await authApi.startSession({
      browser: browser.value,
      copy_login_state: copyLoginState.value,
      reset_profile: resetProfile.value,
      external_browser: externalBrowser.value,
    })
    session.value = created
    ElMessage.success('已启动专用浏览器，请在弹出的窗口内扫码登录')
    startPolling()
  } catch (error) {
    const message =
      (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '启动授权失败，请确认爬虫目录与浏览器是否可用'
    ElMessage.error(message)
  } finally {
    starting.value = false
  }
}

async function cancelAuth() {
  await authApi.cancelSession()
  session.value = null
  stopPolling()
  ElMessage.info('已取消授权')
  await loadStatus()
}

async function verifyNow() {
  verifying.value = true
  try {
    const result = await authApi.verify()
    ElMessage[result.ok ? 'success' : 'warning'](result.detail || result.message)
    await loadStatus()
    await store.loadDashboard().catch(() => undefined)
  } finally {
    verifying.value = false
  }
}

async function logout() {
  try {
    await ElMessageBox.confirm(
      '停用后需要重新扫码授权才能抓取。确认停用当前登录态？',
      '停用登录态',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await authApi.logout()
  ElMessage.success('已停用登录态')
  await loadStatus()
  await store.loadDashboard().catch(() => undefined)
}

/* ---------------- 轮询兜底 ---------------- */
let pollTimer: number | null = null

function startPolling() {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(async () => {
    try {
      const current = await authApi.currentSession()
      session.value = current
      if (!current || ['success', 'failed', 'cancelled'].includes(current.phase)) {
        stopPolling()
        await loadStatus()
        await store.loadDashboard().catch(() => undefined)
        if (current?.phase === 'success') {
          ElMessage.success('授权成功，登录态已加密保存')
        } else if (current?.phase === 'failed') {
          ElMessage.error(current.message || '授权失败')
        }
      }
    } catch {
      stopPolling()
    }
  }, 2500)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

/**
 * 订阅授权专用 WebSocket 通道（/api/ws/auth）。
 * 断线时由上面的轮询兜底，用户不会看不到进度。
 */
const { connected, close: closeAuthSocket } = useAuthSocket((event) => {
  if (event.type !== 'auth.progress') return
  const payload = event.data as unknown as AuthSession
  session.value = payload
  if (['success', 'failed', 'cancelled'].includes(payload.phase)) {
    stopPolling()
    loadStatus().catch(() => undefined)
    store.loadDashboard().catch(() => undefined)
    if (payload.phase === 'success') {
      ElMessage.success('授权成功，登录态已加密保存')
    } else if (payload.phase === 'failed') {
      ElMessage.error(payload.message || '授权失败')
    }
  }
})

onMounted(loadStatus)
onBeforeUnmount(() => {
  stopPolling()
  closeAuthSocket()
})
</script>

<template>
  <div class="bzp-page">
    <!-- ============ 当前状态 ============ -->
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><Key /></el-icon>
          账号授权状态
        </div>
        <div class="auth__actions">
          <el-button size="small" :loading="loading" @click="loadStatus">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
          <el-button size="small" type="primary" :loading="verifying" @click="verifyNow">
            <el-icon><CircleCheck /></el-icon>
            校验登录态
          </el-button>
        </div>
      </div>

      <div class="bzp-panel__body">
        <div class="auth__status" :class="`auth__status--${statusTone}`">
          <div class="auth__status-icon">
            <el-icon>
              <component :is="profile?.status === 'active' ? 'CircleCheckFilled' : 'WarningFilled'" />
            </el-icon>
          </div>
          <div class="auth__status-body">
            <div class="auth__status-title">
              {{ statusTitle }}
              <el-tag :type="authTagType(profile?.status || 'unknown')" size="small" effect="light" round>
                {{ profile?.status_label || '未校验' }}
              </el-tag>
            </div>
            <div class="auth__status-desc">{{ statusDescription }}</div>
            <div class="auth__status-meta">
              <span>浏览器：{{ profile?.browser || '—' }}</span>
              <span>CDP 端口：{{ profile?.cdp_port ?? '—' }}</span>
              <span>
                CDP 状态：
                <el-tag
                  :type="status?.cdp_reachable ? 'success' : 'info'"
                  size="small"
                  effect="plain"
                >
                  {{ status?.cdp_reachable ? '已监听' : '未启动' }}
                </el-tag>
              </span>
            </div>
          </div>
          <el-button
            v-if="profile?.status === 'active'"
            size="small"
            type="danger"
            plain
            @click="logout"
          >
            停用登录态
          </el-button>
        </div>

        <el-alert
          title="登录态如何保存？"
          type="info"
          :closable="false"
          class="auth__note"
        >
          <template #default>
            专用浏览器使用工作台自有的隔离 profile 目录（<span class="bzp-mono">storage/chrome-profiles/</span>），
            不会读取或修改你日常使用的 Chrome 数据。登录成功后，Cookie 相关文件会被加密打包保存，
            数据库里只记录路径与指纹，不含明文凭据。
          </template>
        </el-alert>
      </div>
    </section>

    <div class="bzp-grid bzp-grid--2">
      <!-- ============ 发起授权 ============ -->
      <section class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><Iphone /></el-icon>
            扫码授权
          </div>
        </div>

        <div class="bzp-panel__body">
          <el-form label-width="118px" label-position="right">
            <el-form-item label="浏览器">
              <el-radio-group v-model="browser" :disabled="Boolean(session)">
                <el-radio-button value="chrome">Google Chrome</el-radio-button>
                <el-radio-button value="edge">Microsoft Edge</el-radio-button>
              </el-radio-group>
            </el-form-item>

            <el-form-item label="接管现有浏览器">
              <el-switch v-model="externalBrowser" :disabled="Boolean(session)" />
              <div class="auth__hint">
                开启后工作台<strong>不会尝试启动浏览器</strong>，只负责接管一个已经开着
                CDP 调试端口的实例。适用于服务端无法拉起 Chrome 的情况
                （无图形界面、容器、受限运行环境）。
              </div>
            </el-form-item>

            <el-alert
              v-if="externalBrowser"
              type="info"
              :closable="false"
              class="auth__launch"
            >
              <template #title>请先用「隔离 profile」手动启动浏览器</template>
              <template #default>
                <div class="auth__launch-hint">
                  在<strong>普通终端</strong>里执行下面的命令，在弹出的窗口登录 zhipin.com，
                  然后回到本页点「开始授权」接管它：
                </div>
                <pre class="auth__cmd">{{ launchCommand }}</pre>
                <el-button size="small" type="primary" plain @click="copyLaunchCommand">
                  <el-icon><CopyDocument /></el-icon>
                  复制启动命令
                </el-button>
                <div class="auth__launch-hint">
                  必须带 <code>--remote-debugging-port</code> 和 <code>--user-data-dir</code>
                  指向隔离 profile，否则登录态不会落到工作台可读取的位置。
                </div>
              </template>
            </el-alert>

            <el-form-item label="导入本机 Cookie">
              <el-switch v-model="copyLoginState" :disabled="Boolean(session)" />
              <div class="auth__hint">
                开启后会从本机主浏览器复制 BOSS 登录态到隔离 profile（覆盖式）。
                仅在你不方便扫码、且明确知道风险时使用。
              </div>
            </el-form-item>

            <el-form-item label="重建 Profile">
              <el-switch v-model="resetProfile" :disabled="Boolean(session)" />
              <div class="auth__hint">
                开启后会清空隔离浏览器内的登录态，相当于彻底重新登录。
              </div>
            </el-form-item>

            <el-form-item>
              <el-button
                type="primary"
                :loading="starting"
                :disabled="sessionBusy"
                @click="startAuth"
              >
                <el-icon><VideoPlay /></el-icon>
                开始授权（启动专用浏览器）
              </el-button>
              <el-button
                v-if="sessionBusy"
                type="warning"
                plain
                @click="cancelAuth"
              >
                取消授权
              </el-button>
            </el-form-item>
          </el-form>

          <el-alert type="warning" :closable="false" show-icon>
            <template #title>需要在有图形界面的环境执行</template>
            <template #default>
              扫码登录依赖真实浏览器窗口，纯无头服务器请通过 X11 转发 / VNC 打开桌面，
              或在本机运行后端后再访问本工作台。
            </template>
          </el-alert>
        </div>
      </section>

      <!-- ============ 授权进度 ============ -->
      <section class="bzp-panel">
        <div class="bzp-panel__header">
          <div class="bzp-panel__title">
            <el-icon><Loading /></el-icon>
            授权进度
          </div>
          <el-tag :type="connected ? 'success' : 'info'" size="small" effect="plain" round>
            {{ connected ? '实时推送' : '轮询模式' }}
          </el-tag>
        </div>

        <div class="bzp-panel__body">
          <template v-if="session">
            <el-steps :active="activeStep" finish-status="success" align-center class="auth__steps">
              <el-step title="启动浏览器" />
              <el-step title="扫码登录" />
              <el-step title="校验登录态" />
              <el-step title="保存完成" />
            </el-steps>

            <el-alert
              :type="
                session.phase === 'success'
                  ? 'success'
                  : session.phase === 'failed'
                    ? 'error'
                    : 'info'
              "
              :title="session.phase_label"
              :description="session.message"
              :closable="false"
              show-icon
              class="auth__alert"
            />

            <div class="auth__url">
              <span class="bzp-muted">登录页地址：</span>
              <a :href="session.login_url || '#'" target="_blank" rel="noopener noreferrer">
                {{ session.login_url || '—' }}
              </a>
            </div>

            <div v-if="session.log_tail?.length" class="bzp-terminal auth__terminal">
              {{ session.log_tail.slice(-40).join('\n') }}
            </div>
          </template>

          <el-empty v-else description="尚未发起授权，点击左侧「开始授权」" :image-size="72" />
        </div>
      </section>
    </div>

    <!-- ============ 授权档案列表 ============ -->
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><Files /></el-icon>
          授权档案
        </div>
        <span class="bzp-panel__subtitle">每个档案对应一个独立的隔离浏览器 profile</span>
      </div>

      <el-table :data="profiles" size="default" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="browser" label="浏览器" width="110" />
        <el-table-column prop="cdp_port" label="CDP 端口" width="100" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="authTagType(row.status)" size="small" effect="light">
              {{ row.status_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近校验" min-width="170">
          <template #default="{ row }">{{ formatDateTime(row.last_verified_at) }}</template>
        </el-table-column>
        <el-table-column label="备注" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.last_error" class="auth__error">{{ row.last_error }}</span>
            <span v-else class="bzp-muted">{{ row.account_label || '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.auth__actions {
  display: flex;
  gap: 8px;
}

.auth__status {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 18px;
  border-radius: 10px;
  background: #f8fafd;
  border: 1px solid var(--bzp-border);
}

.auth__status--success {
  background: #f2fbf7;
  border-color: #c9ecd9;
}

.auth__status--warning {
  background: #fffaf0;
  border-color: #ffe2a8;
}

.auth__status--danger {
  background: #fdf4f5;
  border-color: #f7cdd3;
}

.auth__status-icon {
  width: 42px;
  height: 42px;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  background: #fff;
  color: var(--bzp-success);
  flex-shrink: 0;
  border: 1px solid var(--bzp-border);
}

.auth__status--danger .auth__status-icon {
  color: var(--bzp-danger);
}
.auth__status--warning .auth__status-icon {
  color: var(--bzp-warning);
}

.auth__status-body {
  flex: 1;
  min-width: 0;
}

.auth__status-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
}

.auth__status-desc {
  margin-top: 5px;
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.7;
}

.auth__status-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 12px;
  color: var(--bzp-text-secondary);
  align-items: center;
}

.auth__note {
  margin-top: 16px;
}

.auth__hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.6;
}

.auth__launch {
  margin-bottom: 18px;
}

.auth__launch-hint {
  font-size: 12px;
  line-height: 1.8;
  margin-bottom: 8px;
}

.auth__cmd {
  background: #0d1421;
  color: #cbd6e6;
  border-radius: 6px;
  padding: 10px 12px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0 0 10px;
}

.auth__cmd + .el-button {
  margin-bottom: 10px;
}

.auth__launch code {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 5px;
  border-radius: 4px;
}

.auth__steps {
  margin-bottom: 18px;
}

.auth__alert {
  margin-bottom: 14px;
}

.auth__url {
  font-size: 12px;
  margin-bottom: 12px;
  word-break: break-all;
}

.auth__terminal {
  max-height: 240px;
}

.auth__error {
  color: var(--bzp-danger);
  font-size: 12px;
}
</style>
