import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiEnvelope } from '@/types'

/**
 * 统一 HTTP 客户端。
 *
 * 后端所有接口都返回 `{ code, message, data }` 信封，这里自动拆封：
 * 业务代码直接拿到 `data`，不必到处写 `.data.data`。
 */
const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 120_000,
  headers: { 'Content-Type': 'application/json' },
})

/** 是否已经在提示错误，避免并发请求刷屏 */
let errorToastAt = 0

function toastError(message: string) {
  const now = Date.now()
  if (now - errorToastAt < 1200) return
  errorToastAt = now
  ElMessage.error(message)
}

http.interceptors.response.use(
  (response) => {
    // 文件下载（CSV / JSON）直接返回原始响应
    if (response.config.responseType === 'blob') return response
    const payload = response.data as ApiEnvelope<unknown>
    if (payload && typeof payload === 'object' && 'code' in payload) {
      if (payload.code !== 0) {
        toastError(payload.message || '请求失败')
        return Promise.reject(new Error(payload.message || '请求失败'))
      }
    }
    return response
  },
  (error) => {
    const status = error?.response?.status
    const detail =
      error?.response?.data?.message ||
      error?.response?.data?.detail ||
      error?.message ||
      '网络请求失败'

    if (status === 422) {
      toastError(typeof detail === 'string' ? detail : '参数校验失败')
    } else if (status === 404) {
      toastError('资源不存在')
    } else if (status && status >= 500) {
      toastError('服务端错误，请查看后端日志')
    } else if (!error?.response) {
      toastError('无法连接后端服务，请确认后端已启动')
    } else {
      toastError(String(detail))
    }
    return Promise.reject(error)
  },
)

/** GET，返回拆封后的 data */
export async function get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const response = await http.get<ApiEnvelope<T>>(url, { params })
  return response.data.data
}

/** POST */
export async function post<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const response = await http.post<ApiEnvelope<T>>(url, body, config)
  return response.data.data
}

/** PUT */
export async function put<T>(url: string, body?: unknown): Promise<T> {
  const response = await http.put<ApiEnvelope<T>>(url, body)
  return response.data.data
}

/** PATCH —— 用于局部更新（如启用/禁用、设为默认） */
export async function patch<T>(
  url: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  const response = await http.patch<ApiEnvelope<T>>(url, body, config)
  return response.data.data
}

/** DELETE */
export async function del<T>(url: string, body?: unknown): Promise<T> {
  const response = await http.delete<ApiEnvelope<T>>(url, { data: body })
  return response.data.data
}

/** 下载文件：命中浏览器下载行为 */
export async function download(url: string, params?: Record<string, unknown>): Promise<void> {
  const response = await http.get(url, { params, responseType: 'blob' })
  const disposition = String(response.headers['content-disposition'] || '')
  let filename = 'export.csv'

  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (utf8Match) {
    filename = decodeURIComponent(utf8Match[1])
  } else {
    const plainMatch = /filename="?([^";]+)"?/i.exec(disposition)
    if (plainMatch) filename = plainMatch[1]
  }

  const blobUrl = URL.createObjectURL(new Blob([response.data]))
  const anchor = document.createElement('a')
  anchor.href = blobUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(blobUrl)
}

/** WebSocket 基地址（自动跟随当前站点协议） */
export function wsUrl(path: string): string {
  const base = import.meta.env.VITE_API_BASE
  if (base && /^https?:\/\//.test(base)) {
    return base.replace(/^http/, 'ws') + path
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api${path}`
}

export default http
