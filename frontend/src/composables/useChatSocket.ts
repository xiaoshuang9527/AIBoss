/**
 * 对话级任务进度通道。
 *
 * 为什么用 WebSocket 而不是继续轮询
 * --------------------------------
 * 抓取任务要跑 10-20 分钟，用户会一直盯着对话看进度。轮询（哪怕是 5 秒一次）
 * 在这段时间里会产生几百次请求，而且进度更新最多延迟一个轮询周期。
 * WebSocket 让进度变化**立刻**到达，且没有无效请求。
 *
 * 断线处理：指数退避重连（最多 15s）。如果浏览器不支持或连接始终失败，
 * 界面不会卡住 —— 进度卡片仍可手动点「查看详情」跳到历史页。
 */
import { onUnmounted, ref, type Ref } from 'vue'
import type { TaskStatusItem } from '@/types/chat'

/** 服务端推来的消息信封 */
interface Envelope {
  type: string
  ts?: string
  data?: Record<string, unknown>
}

export interface ChatTaskProgressPayload {
  event: string
  conversation_id: number
  task: TaskStatusItem
}

export interface UseChatSocketOptions {
  /** 收到任务进度时回调 */
  onTaskProgress: (payload: ChatTaskProgressPayload) => void
}

export interface UseChatSocketReturn {
  connected: Ref<boolean>
  /** 切换到另一个会话；传 null 表示断开 */
  connect: (conversationId: number | null) => void
  close: () => void
}

const MAX_BACKOFF_MS = 15_000
const BASE_BACKOFF_MS = 800

export function useChatSocket(options: UseChatSocketOptions): UseChatSocketReturn {
  const connected = ref(false)

  let socket: WebSocket | null = null
  let currentId: number | null = null
  let retryTimer: number | null = null
  let attempt = 0
  // 主动关闭时不再重连（区分于异常断开）
  let intentionalClose = false

  function clearRetry() {
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer)
      retryTimer = null
    }
  }

  function baseUrl(): string {
    const base = import.meta.env.VITE_API_BASE || '/api'
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    // base 可能是绝对地址（生产独立域名），此时需要解析出 host
    if (base.startsWith('http')) {
      return `${base.replace(/^http/, 'ws')}/ws/chat`
    }
    return `${protocol}//${window.location.host}${base}/ws/chat`
  }

  function scheduleReconnect() {
    if (intentionalClose || currentId === null) return
    clearRetry()
    attempt += 1
    const delay = Math.min(BASE_BACKOFF_MS * 2 ** (attempt - 1), MAX_BACKOFF_MS)
    retryTimer = window.setTimeout(() => {
      if (currentId !== null) open(currentId)
    }, delay)
  }

  function open(conversationId: number) {
    closeSocketOnly()
    intentionalClose = false

    let url: string
    try {
      url = `${baseUrl()}/${conversationId}`
    } catch {
      return
    }

    try {
      socket = new WebSocket(url)
    } catch {
      scheduleReconnect()
      return
    }

    socket.onopen = () => {
      connected.value = true
      attempt = 0
    }

    socket.onmessage = (event: MessageEvent<string>) => {
      let payload: Envelope
      try {
        payload = JSON.parse(event.data) as Envelope
      } catch {
        return
      }
      if (payload.type === 'connected') {
        connected.value = true
        return
      }
      if (payload.type === 'chat.task_progress' && payload.data) {
        const data = payload.data as unknown as ChatTaskProgressPayload
        if (data?.task) options.onTaskProgress(data)
      }
    }

    socket.onerror = () => {
      // onerror 之后一定会触发 onclose，重连逻辑放在那里统一处理
    }

    socket.onclose = () => {
      connected.value = false
      socket = null
      scheduleReconnect()
    }
  }

  function closeSocketOnly() {
    if (socket) {
      // 先摘掉回调，避免关闭时触发重连
      socket.onopen = null
      socket.onmessage = null
      socket.onerror = null
      socket.onclose = null
      try {
        socket.close()
      } catch {
        /* 已经断开 */
      }
      socket = null
    }
    connected.value = false
  }

  function connect(conversationId: number | null) {
    if (conversationId === currentId && socket) return
    currentId = conversationId
    clearRetry()
    attempt = 0
    if (conversationId === null) {
      intentionalClose = true
      closeSocketOnly()
      return
    }
    open(conversationId)
  }

  function close() {
    intentionalClose = true
    currentId = null
    clearRetry()
    closeSocketOnly()
  }

  onUnmounted(close)

  return { connected, connect, close }
}
