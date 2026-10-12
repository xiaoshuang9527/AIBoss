import { del, get, patch, post } from './http'
import { wsUrl } from './http'
import type {
  ChatAttachment,
  ChatConfig,
  ChatConversation,
  ChatMessage,
  ConversationDetail,
} from '@/types/chat'
import type { SSEMetaData, SSEErrorData, SSEUsageData, SSEToolCallData, SSEToolResultData } from '@/types/chat'

const BASE = import.meta.env.VITE_API_BASE || '/api'

function form(body: Record<string, string | number | boolean | undefined>): FormData {
  const data = new FormData()
  for (const [key, value] of Object.entries(body)) {
    if (value !== undefined && value !== null && value !== '') {
      data.append(key, String(value))
    }
  }
  return data
}

/** 对话模块接口。 */
export const chatApi = {
  config: () => get<ChatConfig>('/chat/config'),

  listConversations: (limit = 50) => get<ChatConversation[]>('/chat/conversations', { limit }),

  createConversation: (title = '新对话', modelConfigId?: number) =>
    post<ChatConversation>(
      '/chat/conversations',
      form({ title, model_config_id: modelConfigId }),
      { headers: { 'Content-Type': 'multipart/form-data' } },
    ),

  getConversation: (id: number) => get<ConversationDetail>(`/chat/conversations/${id}`),

  updateConversation: (
    id: number,
    payload: { title?: string; model_config_id?: number; mode?: string; reset_slots?: boolean },
  ) =>
    patch<ChatConversation>(
      `/chat/conversations/${id}`,
      form(payload as Record<string, string | number | boolean | undefined>),
      { headers: { 'Content-Type': 'multipart/form-data' } },
    ),

  deleteConversation: (id: number) =>
    del<{ deleted: boolean; removed_files: number }>(`/chat/conversations/${id}`),

  uploadAttachment: (conversationId: number, file: File) => {
    const data = new FormData()
    data.append('file', file)
    return post<ChatAttachment>(`/chat/conversations/${conversationId}/attachments`, data, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  getAttachment: (id: number) => get<ChatAttachment>(`/chat/attachments/${id}`),

  /** 附件原始内容 URL（图片缩略图直接用 <img :src>） */
  attachmentRawUrl: (id: number) => `${BASE}/chat/attachments/${id}/raw`,

  messagesUrl: (conversationId: number) =>
    `${BASE}/chat/conversations/${conversationId}/messages`,
}

/**
 * 用 fetch 流式发送消息并解析 SSE。
 *
 * 为什么不用 EventSource：EventSource 只支持 GET，无法携带 JSON body，
 * 也不方便传附件 id 列表。所以用 fetch + ReadableStream 手工解析。
 *
 * @returns 可用于中断的 AbortController
 */
export interface StreamHandlers {
  onMeta?: (data: SSEMetaData) => void
  onReasoning?: (delta: string) => void
  onText?: (delta: string) => void
  onToolCall?: (data: SSEToolCallData) => void
  onToolResult?: (data: SSEToolResultData) => void
  onUsage?: (data: SSEUsageData) => void
  onDone?: (finishReason: string) => void
  onError?: (data: SSEErrorData) => void
}

export function streamMessage(
  conversationId: number,
  payload: {
    content: string
    attachment_ids?: number[]
    model_config_id?: number
    /** 显式指定模式（跳过自动路由），传 auto 表示重新自动判断 */
    mode?: string
  },
  handlers: StreamHandlers,
): AbortController {
  const controller = new AbortController()

  void (async () => {
    let response: Response
    try {
      response = await fetch(chatApi.messagesUrl(conversationId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: payload.content,
          attachment_ids: payload.attachment_ids ?? [],
          model_config_id: payload.model_config_id,
          mode: payload.mode,
        }),
        signal: controller.signal,
      })
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        handlers.onError?.({
          error_type: 'network_error',
          title: '无法连接后端',
          message: '请确认后端服务正在运行。',
          suggestion: '检查后端进程与端口配置。',
        })
        handlers.onDone?.('error')
      }
      return
    }

    if (!response.ok) {
      // 前置校验失败（404/422）走普通 JSON，不是 SSE
      let detail = `HTTP ${response.status}`
      try {
        const body = await response.json()
        detail = body.detail || body.message || detail
      } catch {
        /* 保持默认文案 */
      }
      handlers.onError?.({
        error_type: 'bad_response',
        title: '请求被拒绝',
        message: String(detail),
        suggestion: '请检查会话是否存在、内容是否为空。',
      })
      handlers.onDone?.('error')
      return
    }

    if (!response.body) {
      handlers.onError?.({
        error_type: 'bad_response',
        title: '浏览器不支持流式读取',
        message: '当前浏览器无法读取响应流。',
      })
      handlers.onDone?.('error')
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE 以空行分隔事件块
        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          dispatch(block, handlers)
          boundary = buffer.indexOf('\n\n')
        }
      }
      // 处理最后一块（某些情况下末尾没有空行）
      if (buffer.trim()) dispatch(buffer, handlers)
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        handlers.onError?.({
          error_type: 'network_error',
          title: '流式读取中断',
          message: String(error),
          suggestion: '请重试；若持续失败请检查网络与代理设置。',
        })
        handlers.onDone?.('error')
      }
    }
  })()

  return controller
}

function dispatch(block: string, handlers: StreamHandlers): void {
  if (!block.trim() || block.startsWith(':')) return // 注释行是心跳

  let event = ''
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7).trim()
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
  }
  if (!event || !dataLines.length) return

  let data: Record<string, unknown>
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    return
  }

  switch (event) {
    case 'meta':
      handlers.onMeta?.(data as unknown as SSEMetaData)
      break
    case 'reasoning':
      handlers.onReasoning?.(String(data.delta ?? ''))
      break
    case 'text':
      handlers.onText?.(String(data.delta ?? ''))
      break
    case 'tool_call':
      handlers.onToolCall?.(data as unknown as SSEToolCallData)
      break
    case 'tool_result':
      handlers.onToolResult?.(data as unknown as SSEToolResultData)
      break
    case 'usage':
      handlers.onUsage?.(data as unknown as SSEUsageData)
      break
    case 'done':
      handlers.onDone?.(String(data.finish_reason ?? 'stop'))
      break
    case 'error':
      handlers.onError?.(data as unknown as SSEErrorData)
      break
  }
}

export { wsUrl }
export type { ChatMessage }
