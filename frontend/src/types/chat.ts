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

export type ToolResultPayload =
  | JobCardsPayload
  | TaskCreatedPayload
  | TaskStatusPayload
  | Record<string, unknown>

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
