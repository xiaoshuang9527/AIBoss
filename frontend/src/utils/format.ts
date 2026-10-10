import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

/**
 * 时间格式化。
 *
 * ⚠️ 关键约定：后端存的是 **naive UTC**（SQLite 不保存时区），
 * 序列化出来形如 `2026-10-10T11:09:03`（不带 Z）。
 * dayjs 遇到这种串会**按本地时间解释**，于是东八区下会平白少 8 小时，
 * 再经过相对时间格式化就显示成「8 小时前」。
 * 所以这里统一：没有时区标记的串一律补 Z，按 UTC 解释后再转本地时间。
 */
function normalize(value: string): dayjs.Dayjs {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  return dayjs(hasTimezone ? value : `${value}Z`)
}

/** 统一的时间格式化：后端返回的可能是不带时区的 ISO 串 */
export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const parsed = normalize(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : '—'
}

export function formatDate(value?: string | null): string {
  if (!value) return '—'
  const parsed = normalize(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD') : '—'
}

export function fromNow(value?: string | null): string {
  if (!value) return '—'
  const parsed = normalize(value)
  return parsed.isValid() ? parsed.fromNow() : '—'
}

/** 字节数 -> 人类可读 */
export function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

/** 秒 -> 1分23秒 */
export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return '—'
  const total = Math.round(seconds)
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分`
}

/** 任务状态 -> Element Plus tag 类型 */
export function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  switch (status) {
    case 'succeeded':
      return 'success'
    case 'running':
      return 'primary'
    case 'pending':
      return 'info'
    case 'needs_attention':
      return 'warning'
    case 'failed':
      return 'danger'
    case 'cancelled':
      return 'info'
    default:
      return 'info'
  }
}

/** 授权状态 -> tag 类型 */
export function authTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'active':
      return 'success'
    case 'authorizing':
      return 'warning'
    case 'expired':
      return 'danger'
    default:
      return 'info'
  }
}
