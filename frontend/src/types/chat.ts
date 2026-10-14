/** AI 对话模块类型，与后端 schemas.py 对应。 */

export type ChatRole = 'system' | 'user' | 'assistant' | 'tool'

/** 结构化载荷类型：前端按此决定用什么组件渲染 */
export type ChatContentType =
  | 'text'
  | 'job_cards'
  | 'match_report'
  | 'interview_set'
  | 'system_notice'

export interface ChatConversation {
  id: number
  title: string
  mode: string
  model_config_id: number | null
  message_count: number
  total_tokens: number
  created_at: string
  updated_at: string
  last_message_at: string | null
}

export interface ChatMessage {
  id: number
  conversation_id: number
  role: ChatRole
  content: string | null
  content_type: ChatContentType
  /** 结构化载荷（岗位卡片 / 诊断报告等），类型由 content_type 决定 */
  content_data: unknown | null
  /** 推理模型的思考过程 */
  reasoning: string | null
  attachments: number[]
  tool_calls: ChatToolCallRecord[]
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  latency_ms: number | null
  error_type: string | null
  error_message: string | null
  model_config_id: number | null
  model_label: string | null
  created_at: string
}

export interface ChatToolCallRecord {
  id: string
  name: string
  arguments: Record<string, unknown>
}

/** 岗位卡片（工具 search_existing_jobs 的结构化结果） */
export interface JobCard {
  id: number
  job_id: string
  title: string
  company: string
  salary: string
  salary_avg: number | null
  salary_bucket: string
  city: string | null
  location: string | null
  experience: string | null
  degree: string | null
  company_scale: string | null
  company_stage: string | null
  company_industry: string | null
  boss_name: string | null
  boss_active_status: string | null
  skills: string[]
  welfare: string | null
  jd_preview: string | null
  has_jd: boolean
  /** 「去 BOSS 沟通」的目标地址 */
  job_link: string | null
  company_link: string | null
  is_favorite: boolean
  scraped_at: string | null
}

/** 工具结果的载荷（按 payload_type 区分） */
export interface JobCardsPayload {
  jobs: JobCard[]
  total: number
  returned?: number
  query?: Record<string, unknown>
}

/** 薪资档位解析结果（抓取任务用；BOSS 只有粗档位，没有精确区间） */
export interface SalaryResolutionPayload {
  code: string
  label: string
  raw: string
  wanted: [number, number | null] | null
  /** 档位与用户期望不一致时的说明；null 表示基本一致 */
  note: string | null
}

/** 抓取任务已创建（tool: trigger_boss_scraper） */
export interface TaskCreatedPayload {
  task_id: number
  status: string
  status_label: string
  keyword: string
  city: string
  salary: SalaryResolutionPayload | null
  experience?: string | null
  degree?: string | null
  max_pages: number
  with_detail: boolean
  estimated_minutes: number
  /** true 表示命中了去重，并未真的新建任务 */
  duplicate?: boolean
}

/** 单个抓取任务的进度 */
export interface TaskStatusItem {
  task_id: number
  keyword: string
  city: string
  status: string
  status_label: string
  is_active: boolean
  progress: number
  stage_text: string | null
  total_found: number
  new_count: number
  detail_count: number
  error_message: string | null
  duration_seconds: number | null
  created_at: string | null
  finished_at: string | null
}

export interface TaskStatusPayload {
  tasks: TaskStatusItem[]
}

/** 单个岗位的完整 JD（tool: get_job_detail） */
export interface JobDetailPayload {
  id: number
  job_id: string
  title: string
  company: string
  salary: string
  city: string | null
  location: string | null
  experience: string | null
  degree: string | null
  company_scale: string | null
  company_stage: string | null
  company_industry: string | null
  boss_name: string | null
  boss_active_status: string | null
  welfare: string | null
  job_duty: string | null
  job_requirement: string | null
  jd: string | null
  job_link: string | null
  has_detail: boolean
  truncated: boolean
}

/** 匹配度诊断报告里的一个维度 */
export interface MatchDimension {
  name: string
  level: 'match' | 'partial' | 'mismatch'
  summary: string
  evidence: string[]
  gaps: string[]
}

export interface MatchRisk {
  risk: string
  why: string
  how_to_handle: string
}

export interface StarFramework {
  situation: string
  task: string
  action: string
  result: string
}

export interface MatchQuestion {
  question: string
  category: string
  focus: string
  star: StarFramework
  tips: string
}

export interface MatchReport {
  verdict: string
  /** null 表示解析失败、没有可信评分（不要当成 0 分显示） */
  overall_score: number | null
  dimensions: MatchDimension[]
  strengths: string[]
  risks: MatchRisk[]
  questions: MatchQuestion[]
  actions: string[]
  /** 模型输出不是合法 JSON 时为 true，此时界面应提示格式异常 */
  parse_error?: boolean
  raw_text?: string
}

/** 《匹配度诊断报告》（tool: analyze_match） */
export interface MatchReportPayload {
  report: MatchReport
  job: {
    id: number
    title: string
    company: string
    salary: string
    city: string | null
    job_link: string | null
  } | null
  resume_source: string
  jd_source: string
  question_count: number
}

export type ToolResultPayload =
  | JobCardsPayload
  | JobDetailPayload
  | TaskCreatedPayload
  | TaskStatusPayload
  | MatchReportPayload
  | Record<string, unknown>

/* ---------------- 用量统计 ---------------- */

/**
 * 用量统计（token 维度）。
 *
 * 注意：这是一个个人求职辅助工具，**没有计费/额度/套餐概念**。
 * 这些字段只用于回答"我用了多少 token、花在哪"。
 */
export interface UsageTotals {
  conversations: number
  assistant_messages: number
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  /** 输出里属于思考过程的部分（推理模型才有） */
  reasoning_tokens: number
  total_tokens: number
  /** 缓存命中率：命中部分单价更低，越高越省 */
  cache_hit_rate: number
  /** 思考占输出的比例：推理模型里这一项经常很高 */
  reasoning_share: number
}

export interface UsageDailyPoint {
  date: string
  messages: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  total_tokens: number
}

export interface UsageByModel {
  model_label: string
  messages: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  total_tokens: number
  avg_latency_ms: number
}

export interface UsageTopConversation {
  conversation_id: number
  title: string
  mode: string
  messages: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface UsageToolStat {
  name: string
  calls: number
}

export interface UsageReport {
  days: number
  generated_at: string
  totals: UsageTotals
  daily: UsageDailyPoint[]
  by_model: UsageByModel[]
  top_conversations: UsageTopConversation[]
  tools: UsageToolStat[]
}

export type AttachmentKind = 'image' | 'pdf' | 'docx' | 'text' | 'unsupported'

export type ExtractStatus = 'pending' | 'done' | 'failed' | 'not_applicable'

export interface ChatAttachment {
  id: number
  conversation_id: number
  message_id: number | null
  kind: AttachmentKind
  filename: string
  mime_type: string | null
  size_bytes: number
  size_label: string
  extract_status: ExtractStatus
  extract_error: string | null
  /** 仅在单独取详情时返回（可能几万字符） */
  extracted_text: string | null
  created_at: string
}

export interface ChatConfig {
  max_image_bytes: number
  max_doc_bytes: number
  max_attachments: number
  fake_llm: boolean
  max_image_label: string
  max_doc_label: string
  accepted: string
}

export interface ConversationDetail {
  conversation: ChatConversation
  messages: ChatMessage[]
  attachments: ChatAttachment[]
}

/* ---------------- SSE 事件 ---------------- */

export type SSEEventName =
  | 'meta'
  | 'reasoning'
  | 'text'
  | 'tool_call'
  | 'tool_result'
  | 'usage'
  | 'done'
  | 'error'

export interface SSEMetaData {
  conversation_id: number
  model_config_id: number
  model_label: string
  mode: string
  fake: boolean
  /** 路由理由，便于用户理解为什么走了某个模式 */
  mode_reason?: string
  mode_confidence?: number
}

export interface SSEErrorData {
  error_type: string
  title: string
  message: string
  suggestion?: string | null
  detail?: string | null
}

export interface SSEUsageData {
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  latency_ms: number
}

export interface SSEToolCallData {
  id: string
  name: string
  arguments: Record<string, unknown>
}

export interface SSEToolResultData {
  id: string
  name: string
  payload_type: string
  data: ToolResultPayload | null
  latency_ms?: number
}

/** 降级说明：不是失败，但用户需要知道某项能力被放弃 */
export interface SSENoticeData {
  kind: string
  model_label: string
  dropped_tools?: string[]
  message: string
  suggestion?: string
}
