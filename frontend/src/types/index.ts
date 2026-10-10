/**
 * 与后端约定的数据结构定义。
 * 字段与 backend/bzp_workbench/schemas.py 一一对应。
 */

export type TaskStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'needs_attention'

export type AuthStatusValue = 'unknown' | 'active' | 'expired' | 'authorizing' | 'invalid'

export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

export interface PageMeta {
  total: number
  page: number
  page_size: number
  pages: number
}

export interface PageData<T> {
  items: T[]
  meta: PageMeta
}

/* ---------------- 岗位 ---------------- */
export interface Job {
  id: number
  job_id: string
  title: string
  company: string
  salary: string | null
  salary_min: number | null
  salary_max: number | null
  salary_avg: number | null
  city: string | null
  location: string | null
  experience: string | null
  degree: string | null
  company_scale: string | null
  company_stage: string | null
  company_industry: string | null
  boss_name: string | null
  boss_title: string | null
  boss_active_status: string | null
  tags: string | null
  welfare: string | null
  skill_tags: string[]
  skill_keywords: string[]
  jd: string | null
  job_duty: string | null
  job_requirement: string | null
  job_link: string | null
  company_link: string | null
  is_new: boolean
  is_favorite: boolean
  detail_fetched: boolean
  first_task_id: number | null
  scraped_at: string | null
  created_at: string
}

export interface JobFacet {
  value: string
  count: number
}

export interface JobFacets {
  cities: JobFacet[]
  experiences: JobFacet[]
  degrees: JobFacet[]
  skills: JobFacet[]
  salary_min: number | null
  salary_max: number | null
}

/* ---------------- 任务 ---------------- */
export interface ScrapeTask {
  id: number
  keyword: string
  city: string
  salary: string | null
  experience: string | null
  degree: string | null
  company_scale: string | null
  company_stage: string | null
  industry: string | null
  max_pages: number
  with_detail: boolean
  note: string | null
  status: TaskStatus
  status_label: string
  progress: number
  stage_text: string | null
  total_found: number
  new_count: number
  detail_count: number
  attempts: number
  error_message: string | null
  duration_seconds: number | null
  auth_profile_id: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  queue_running?: boolean
}

export interface TaskCreatePayload {
  keyword: string
  city: string
  salary?: string | null
  experience?: string | null
  degree?: string | null
  company_scale?: string | null
  company_stage?: string | null
  industry?: string | null
  max_pages: number
  with_detail: boolean
  note?: string | null
}

export interface TaskLog {
  task_id: number
  status: TaskStatus
  progress: number
  stage_text: string | null
  lines: string[]
  total_lines: number
}

export interface TaskSummary {
  task_id: number
  keyword: string
  city: string
  total_jobs: number
  salary_distribution: { bucket: string; count: number }[]
  top_companies: JobFacet[]
  top_skills: { skill: string; count: number }[]
  max_pages: number
  with_detail: boolean
  recommended_delay: [number, number]
}

export interface QueueStatus {
  running: number
  pending: number
  max_concurrent: number
  slots_free: number
  worker_alive: boolean
  running_task_ids: number[]
}

/* ---------------- 授权 ---------------- */
export interface AuthProfile {
  id: number
  name: string
  browser: string
  cdp_port: number
  status: AuthStatusValue
  status_label: string
  account_label: string | null
  last_verified_at: string | null
  last_error: string | null
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface AuthStatusPayload {
  profile: AuthProfile
  authorized: boolean
  authorizing: boolean
  cdp_reachable: boolean
}

export type AuthPhase =
  | 'starting'
  | 'awaiting_login'
  | 'verifying'
  | 'success'
  | 'failed'
  | 'cancelled'

export interface AuthSession {
  session_id: string
  profile_id: number
  browser: string
  cdp_port: number
  phase: AuthPhase
  phase_label: string
  message: string
  login_url: string | null
  qr_hint: string | null
  started_at: string
  finished_at: string | null
  log_tail: string[]
  external_browser?: boolean
}

export interface AuthVerifyResult {
  ok: boolean
  status: AuthStatusValue
  status_label: string
  message: string
  detail: string | null
}

/* ---------------- 概览 / 图表 ---------------- */
export interface OverviewCards {
  total_jobs: number
  today_new_jobs: number
  active_tasks: number
  auth_status: AuthStatusValue
  auth_status_label: string
  auth_account: string | null
  auth_verified_at: string | null
  total_tasks: number
  succeeded_tasks: number
  failed_tasks: number
  today_tasks: number
  favorite_jobs: number
}

export interface ChartData {
  categories: string[]
  series: { name: string; data: (number | string)[] }[]
}

export interface TopSkill {
  skill: string
  count: number
}

export interface DashboardSummary {
  cards: OverviewCards
  status_text: string
  status_level: 'ok' | 'warning' | 'error'
  salary_distribution: ChartData
  experience_distribution: ChartData
  city_distribution: ChartData
  daily_trend: ChartData
  top_skills: TopSkill[]
  top_companies: JobFacet[]
  recent_tasks: ScrapeTask[]
  disclaimer: string
}

/* ---------------- 系统 ---------------- */
export interface ScraperHealth {
  scraper_dir: string
  entry_exists: boolean
  python_executable: string
  version: string | null
  cdp_port: number
  cdp_reachable: boolean
  profile_dir: string | null
  profile_exists: boolean
  /** CDP 端口通 ≠ 已登录，这里单独给出登录态 */
  authorized: boolean
  message: string
}

export interface SystemSettings {
  app_name: string
  app_version: string
  max_concurrent_tasks: number
  task_delay_min: number
  task_delay_max: number
  max_retries: number
  max_pages_per_task: number
  task_timeout_seconds: number
  auth_timeout_seconds: number
  request_budget: number
  cdp_port: number
  database_dialect: string
  storage_root: string
  disclaimer: string
}

export interface SystemLog {
  id: number
  level: string
  category: string
  message: string
  detail: string | null
  task_id: number | null
  created_at: string
}

export interface StorageUsage {
  root: string
  total_bytes: number
  breakdown: Record<string, number>
  disk_free_bytes: number
}

/* ---------------- WebSocket 事件 ---------------- */
export interface WsEvent<T = Record<string, unknown>> {
  type: string
  ts?: string
  data: T
}
