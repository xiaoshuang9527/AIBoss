import { del, download, get, post, put } from './http'
import type {
  AuthProfile,
  AuthSession,
  AuthStatusPayload,
  AuthVerifyResult,
  DashboardSummary,
  Job,
  JobFacets,
  PageData,
  QueueStatus,
  ScrapeTask,
  ScraperHealth,
  StorageUsage,
  SystemLog,
  SystemSettings,
  TaskCreatePayload,
  TaskLog,
  TaskSummary,
  TopSkill,
} from '@/types'

/* ==================== 系统 / 概览 ==================== */
export const systemApi = {
  /** 工作台首页聚合数据 */
  dashboard: (trendDays = 14, recentLimit = 8) =>
    get<DashboardSummary>('/system/dashboard', { trend_days: trendDays, recent_limit: recentLimit }),

  health: () => get<{ status: string; version: string }>('/system/health'),

  scraperHealth: () => get<ScraperHealth>('/system/scraper-health'),

  settings: () => get<SystemSettings>('/system/settings'),

  updateSettings: (payload: Partial<SystemSettings>) => put<SystemSettings>('/system/settings', payload),

  logs: (params: { page?: number; page_size?: number; level?: string; category?: string }) =>
    get<PageData<SystemLog>>('/system/logs', params),

  purgeLogs: (days = 30) => del<{ deleted: number }>(`/system/logs?days=${days}`),

  queue: () => get<QueueStatus>('/system/queue'),

  storage: () => get<StorageUsage>('/system/storage'),

  scanStale: () => post<{ fixed: number }>('/system/maintenance/scan-stale'),
}

/* ==================== 账号授权 ==================== */
export const authApi = {
  profiles: () => get<AuthProfile[]>('/auth/profiles'),

  status: () => get<AuthStatusPayload>('/auth/status'),

  verify: () => post<AuthVerifyResult>('/auth/verify'),

  startSession: (payload: {
    browser?: 'chrome' | 'edge'
    copy_login_state?: boolean
    reset_profile?: boolean
    name?: string
    external_browser?: boolean
  }) => post<AuthSession>('/auth/sessions', payload),

  currentSession: () => get<AuthSession | null>('/auth/sessions/current'),

  cancelSession: () => del<{ cancelled: boolean }>('/auth/sessions/current'),

  logout: () => post<{ status: string }>('/auth/logout'),
}

/* ==================== 抓取任务 ==================== */
export const taskApi = {
  create: (payload: TaskCreatePayload) =>
    post<{ task: ScrapeTask; queue: QueueStatus }>('/tasks', payload),

  list: (params: {
    page?: number
    page_size?: number
    status?: string
    keyword?: string
    city?: string
  }) => get<PageData<ScrapeTask>>('/tasks', params),

  detail: (id: number) => get<ScrapeTask>(`/tasks/${id}`),

  logs: (id: number, limit = 300) => get<TaskLog>(`/tasks/${id}/logs`, { limit }),

  summary: (id: number) => get<TaskSummary>(`/tasks/${id}/summary`),

  cancel: (id: number) => post<{ cancelled: boolean }>(`/tasks/${id}/cancel`),

  retry: (id: number) => post<{ task: ScrapeTask; queue: QueueStatus }>(`/tasks/${id}/retry`),

  remove: (id: number) => del<{ deleted: boolean }>(`/tasks/${id}`),

  queue: () => get<QueueStatus>('/tasks/queue'),
}

/* ==================== 岗位结果 ==================== */
export interface JobQuery {
  page?: number
  page_size?: number
  order_by?: string
  keyword?: string
  city?: string
  task_id?: number
  experience?: string
  degree?: string
  company_scale?: string
  salary_min?: number
  salary_max?: number
  skill?: string
  only_new?: boolean
  only_favorite?: boolean
  only_with_jd?: boolean
}

export const jobApi = {
  list: (params: JobQuery) => get<PageData<Job>>('/jobs', { ...params }),

  detail: (id: number) => get<Job>(`/jobs/${id}`),

  facets: () => get<JobFacets>('/jobs/facets'),

  topSkills: (limit = 15) => get<TopSkill[]>('/jobs/top-skills', { limit }),

  toggleFavorite: (id: number, favorite?: boolean) =>
    post<{ id: number; is_favorite: boolean }>(`/jobs/${id}/favorite`, { favorite }),

  remove: (ids: number[]) => del<{ deleted: number }>('/jobs', { ids }),
}

/* ==================== 导出 ==================== */
export const exportApi = {
  jobs: (params: {
    fmt: 'csv' | 'json'
    task_id?: number
    keyword?: string
    city?: string
    include_jd?: boolean
  }) => download('/export/jobs', params),

  /** 导出前预览：后端为 POST，这里用查询参数传筛选条件 */
  preview: (params: { task_id?: number; keyword?: string; city?: string }) => {
    const query = new URLSearchParams()
    if (params.task_id) query.set('task_id', String(params.task_id))
    if (params.keyword) query.set('keyword', params.keyword)
    if (params.city) query.set('city', params.city)
    return post<{ total: number; preview: { title: string; company: string; salary: string | null }[] }>(
      `/export/preview?${query.toString()}`,
    )
  },
}
