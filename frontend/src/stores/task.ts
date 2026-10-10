import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { taskApi } from '@/api'
import type { QueueStatus, ScrapeTask, TaskCreatePayload } from '@/types'

/** 任务列表与队列状态。 */
export const useTaskStore = defineStore('task', () => {
  const tasks = ref<ScrapeTask[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)
  const statusFilter = ref<string>('')

  /** 正在执行/排队的任务，用于顶部与概览提示 */
  const activeTasks = computed(() =>
    tasks.value.filter((task) => task.status === 'running' || task.status === 'pending'),
  )

  async function loadTasks(overrides: { page?: number; page_size?: number; status?: string } = {}) {
    loading.value = true
    try {
      const response = await taskApi.list({
        page: overrides.page ?? page.value,
        page_size: overrides.page_size ?? pageSize.value,
        status: overrides.status ?? (statusFilter.value || undefined),
      })
      tasks.value = response.items
      total.value = response.meta.total
      page.value = response.meta.page
      pageSize.value = response.meta.page_size
    } finally {
      loading.value = false
    }
  }

  /** 拉取最近任务（工作台底部表格用，不分页） */
  async function loadRecent(limit = 8) {
    const response = await taskApi.list({ page: 1, page_size: limit })
    return response.items
  }

  async function createTask(payload: TaskCreatePayload) {
    const result = await taskApi.create(payload)
    return result
  }

  async function cancelTask(id: number) {
    return taskApi.cancel(id)
  }

  async function retryTask(id: number) {
    const result = await taskApi.retry(id)
    // 本地同步状态，UI 立刻响应
    const index = tasks.value.findIndex((item) => item.id === id)
    if (index >= 0) tasks.value[index] = result.task
    return result
  }

  async function removeTask(id: number) {
    await taskApi.remove(id)
    tasks.value = tasks.value.filter((item) => item.id !== id)
    total.value = Math.max(0, total.value - 1)
  }

  /** WebSocket 事件：就地更新单条任务，避免整表刷新 */
  function patchTask(id: number, patch: Partial<ScrapeTask>) {
    const index = tasks.value.findIndex((item) => item.id === id)
    if (index >= 0) {
      tasks.value[index] = { ...tasks.value[index], ...patch }
    }
  }

  async function refreshQueue(): Promise<QueueStatus> {
    return taskApi.queue()
  }

  return {
    tasks,
    total,
    page,
    pageSize,
    loading,
    statusFilter,
    activeTasks,
    loadTasks,
    loadRecent,
    createTask,
    cancelTask,
    retryTask,
    removeTask,
    patchTask,
    refreshQueue,
  }
})
