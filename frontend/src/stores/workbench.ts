import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { systemApi } from '@/api'
import type { DashboardSummary, QueueStatus, SystemSettings } from '@/types'

/**
 * 工作台全局状态。
 *
 * 顶部状态栏、左侧导航的角标、概览卡片都依赖这里的 `statusText` 与 `queue`，
 * 所以放在 store 里共享，避免每个页面各自请求一遍。
 */
export const useWorkbenchStore = defineStore('workbench', () => {
  const dashboard = ref<DashboardSummary | null>(null)
  const settings = ref<SystemSettings | null>(null)
  const queue = ref<QueueStatus | null>(null)
  const loading = ref(false)
  const lastRefreshedAt = ref<Date | null>(null)
  const backendOnline = ref(true)

  /** 顶部状态栏文案 */
  const statusText = computed(() => dashboard.value?.status_text || '正在检测服务状态…')
  const statusLevel = computed(() => dashboard.value?.status_level || 'ok')

  /** 概览 4 张卡片 */
  const cards = computed(
    () =>
      dashboard.value?.cards ?? {
        total_jobs: 0,
        today_new_jobs: 0,
        active_tasks: 0,
        auth_status: 'unknown' as const,
        auth_status_label: '未校验',
        auth_account: null,
        auth_verified_at: null,
        total_tasks: 0,
        succeeded_tasks: 0,
        failed_tasks: 0,
        today_tasks: 0,
        favorite_jobs: 0,
      },
  )

  const disclaimer = computed(() => dashboard.value?.disclaimer || '')

  async function loadDashboard(trendDays = 14, recentLimit = 8) {
    loading.value = true
    try {
      dashboard.value = await systemApi.dashboard(trendDays, recentLimit)
      backendOnline.value = true
      lastRefreshedAt.value = new Date()
    } catch {
      backendOnline.value = false
      throw new Error('dashboard load failed')
    } finally {
      loading.value = false
    }
  }

  async function loadQueue() {
    try {
      queue.value = await systemApi.queue()
    } catch {
      backendOnline.value = false
    }
  }

  async function loadSettings() {
    settings.value = await systemApi.settings()
    return settings.value
  }

  async function updateSettings(payload: Partial<SystemSettings>) {
    settings.value = await systemApi.updateSettings(payload)
    return settings.value
  }

  /** WebSocket 推送的队列变化直接合并，省掉一次请求 */
  function applyQueueEvent(payload: Partial<QueueStatus>) {
    if (!queue.value) {
      queue.value = payload as QueueStatus
      return
    }
    queue.value = { ...queue.value, ...payload }
  }

  return {
    dashboard,
    settings,
    queue,
    loading,
    backendOnline,
    lastRefreshedAt,
    statusText,
    statusLevel,
    cards,
    disclaimer,
    loadDashboard,
    loadQueue,
    loadSettings,
    updateSettings,
    applyQueueEvent,
  }
})
