import { onBeforeUnmount, onMounted, ref } from 'vue'
import { wsUrl } from '@/api/http'
import type { WsEvent } from '@/types'

type Handler = (event: WsEvent) => void

/**
 * 任务 WebSocket 通道。
 *
 * 设计要点：
 * * 断线自动重连（指数退避，最长 15s）；
 * * 页面卸载时销毁，避免热更新泄漏连接；
 * * 暴露 `connected` 让页面在断线时降级为 HTTP 轮询。
 */
export function useTaskSocket(onEvent: Handler) {
  const connected = ref(false)
  const reconnecting = ref(false)

  let socket: WebSocket | null = null
  let retry = 0
  let timer: number | null = null
  let disposed = false

  const connect = () => {
    if (disposed) return
    try {
      socket = new WebSocket(wsUrl('/ws/tasks'))
    } catch {
      scheduleReconnect()
      return
    }

    socket.onopen = () => {
      connected.value = true
      reconnecting.value = false
      retry = 0
    }

    socket.onmessage = (message) => {
      try {
        const parsed = JSON.parse(message.data) as WsEvent
        onEvent(parsed)
      } catch {
        // 忽略无法解析的消息
      }
    }

    socket.onclose = () => {
      connected.value = false
      scheduleReconnect()
    }

    socket.onerror = () => {
      connected.value = false
    }
  }

  const scheduleReconnect = () => {
    if (disposed || timer !== null) return
    reconnecting.value = true
    retry += 1
    const delay = Math.min(15_000, 800 * 2 ** Math.min(retry, 5))
    timer = window.setTimeout(() => {
      timer = null
      connect()
    }, delay)
  }

  const close = () => {
    disposed = true
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }
  }

  onMounted(connect)
  onBeforeUnmount(close)

  return { connected, reconnecting, close }
}
