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
