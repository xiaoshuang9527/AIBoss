import { onBeforeUnmount, onMounted, ref } from 'vue'
import { wsUrl } from '@/api/http'
import type { WsEvent } from '@/types'

type Handler = (event: WsEvent) => void

/**
 * 扫码授权专用 WebSocket 通道（``/api/ws/auth``）。
 *
 * 与 :func:`useTaskSocket` 逻辑一致，只是连的是另一个通道；
 * 断线期间页面依靠 HTTP 轮询兜底，所以重连失败不影响可用性。
 */
export function useAuthSocket(onEvent: Handler) {
  const connected = ref(false)
  const reconnecting = ref(false)

  let socket: WebSocket | null = null
  let retry = 0
  let timer: number | null = null
  let disposed = false

  const scheduleReconnect = () => {
    if (disposed || timer !== null) return
    reconnecting.value = true
    retry += 1
    const delay = Math.min(15_000, 1000 * 2 ** Math.min(retry, 5))
    timer = window.setTimeout(() => {
      timer = null
      connect()
    }, delay)
  }

  const connect = () => {
    if (disposed) return
    try {
      socket = new WebSocket(wsUrl('/ws/auth'))
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
        onEvent(JSON.parse(message.data) as WsEvent)
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
