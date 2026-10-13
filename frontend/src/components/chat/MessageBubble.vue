<script setup lang="ts">
/**
 * 单条消息气泡。
 *
 * 按 role 分三种形态：用户（右侧浅色）、助手（左侧白底）、系统通知（居中细条）。
 * 助手消息里依次渲染：思考过程折叠区 → 工具调用提示 → markdown 正文 → 错误提示。
 */
import { computed } from 'vue'
import MarkdownBlock from './MarkdownBlock.vue'
import ReasoningBlock from './ReasoningBlock.vue'
import AttachmentTray from './AttachmentTray.vue'
import JobResultCards from './JobResultCards.vue'
import JobDetailCard from './JobDetailCard.vue'
import TaskCreatedCard from './TaskCreatedCard.vue'
import TaskStatusCard from './TaskStatusCard.vue'
import type {
  ChatAttachment,
  ChatMessage,
  JobCardsPayload,
  JobDetailPayload,
  TaskCreatedPayload,
  TaskStatusPayload,
} from '@/types/chat'
import type { StreamingMessage } from '@/stores/chat'

const props = defineProps<{
  /** 已落库的消息 */
  message?: ChatMessage
  /** 正在流式生成中的消息（与 message 二选一） */
  streaming?: StreamingMessage
  attachments: Record<number, ChatAttachment>
}>()

const isUser = computed(() => (props.message?.role ?? 'assistant') === 'user')
const isSystem = computed(() => props.message?.role === 'system')

const text = computed(() => props.streaming?.content ?? props.message?.content ?? '')
const reasoning = computed(() => props.streaming?.reasoning ?? props.message?.reasoning ?? '')
const active = computed(() => props.streaming?.active ?? false)
const hasText = computed(() => Boolean(props.streaming?.hasText ?? text.value))
const errorMessage = computed(
  () => props.streaming?.errorMessage ?? props.message?.error_message ?? null,
)
const errorSuggestion = computed(() => props.streaming?.errorSuggestion ?? null)
const modelLabel = computed(
  () => props.streaming?.modelLabel ?? props.message?.model_label ?? null,
)
const toolCalls = computed(
  () => props.streaming?.toolCalls ?? props.message?.tool_calls ?? [],
)

/** 该消息引用的附件对象列表 */
const messageAttachments = computed(() => {
  const ids = props.message?.attachments ?? []
  return ids.map((id) => props.attachments[id]).filter(Boolean)
})

/**
 * 工具返回的结构化载荷。
 *
 * 有两个来源，必须都处理：
 * * **流式中**：`streaming.toolResults` 是载荷数组（可能有多轮工具调用）
 * * **已落库**：`message.content_data` 是 `{payload_type, data}` 单个对象
 *   （后端只持久化最后一个结构化结果）
 *
 * 统一成一个带 kind 判别的列表，模板里按 kind 分发到对应卡片组件。
 */
interface TypedPayload {
  kind: 'job_cards' | 'job_detail' | 'task_created' | 'task_status'
  data: unknown
}

const payloads = computed<TypedPayload[]>(() => {
  const list: TypedPayload[] = []

  // ---- 流式来源 ----
  for (const item of props.streaming?.toolResults ?? []) {
    const raw = item as Record<string, unknown>
    if (Array.isArray(raw?.jobs)) {
      list.push({ kind: 'job_cards', data: raw })
    } else if (raw && typeof raw === 'object' && 'task_id' in raw) {
      list.push({ kind: 'task_created', data: raw })
    } else if (Array.isArray(raw?.tasks)) {
      list.push({ kind: 'task_status', data: raw })
    } else if (raw && typeof raw === 'object' && 'has_detail' in raw) {
      // job_detail 的判别字段：has_detail 只有它独有
      list.push({ kind: 'job_detail', data: raw })
    }
  }

  // ---- 已落库来源 ----
  const contentData = props.message?.content_data as
    | { payload_type?: string; data?: unknown }
    | null
    | undefined
  if (contentData?.payload_type && contentData.data && !list.length) {
    const kind = contentData.payload_type
    if (
      kind === 'job_cards' ||
      kind === 'job_detail' ||
      kind === 'task_created' ||
      kind === 'task_status'
    ) {
      list.push({ kind, data: contentData.data })
    }
  }

  return list
})

const jobCardBlocks = computed(() =>
  payloads.value
    .filter((item) => item.kind === 'job_cards')
    .map((item) => item.data as JobCardsPayload),
)

const jobDetailBlocks = computed(() =>
  payloads.value
    .filter((item) => item.kind === 'job_detail')
    .map((item) => item.data as JobDetailPayload),
)

const taskCreatedBlocks = computed(() =>
  payloads.value
    .filter((item) => item.kind === 'task_created')
    .map((item) => item.data as TaskCreatedPayload),
)

const taskStatusBlocks = computed(() =>
  payloads.value
    .filter((item) => item.kind === 'task_status')
    .map((item) => item.data as TaskStatusPayload),
)

const latencyLabel = computed(() => {
  const value = props.message?.latency_ms
  if (!value) return null
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`
})

const tokenLabel = computed(() => {
  const item = props.message
  if (!item) return null
  const total = (item.prompt_tokens || 0) + (item.completion_tokens || 0)
  if (!total) return null
  const cached = item.cached_tokens ? `（缓存 ${item.cached_tokens}）` : ''
  return `${total} tokens${cached}`
})

/** 工具名称 → 用户能看懂的中文描述 */
const TOOL_LABELS: Record<string, string> = {
  search_existing_jobs: '检索本地岗位库',
  trigger_boss_scraper: '发起 BOSS 抓取任务',
  get_job_detail: '读取岗位详情',
  parse_resume: '解析简历',
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

function toolArgs(call: { arguments: Record<string, unknown> }): string {
  const entries = Object.entries(call.arguments || {})
  if (!entries.length) return ''
  return entries.map(([key, value]) => `${key}=${String(value)}`).join('，')
}
</script>

<template>
  <!-- 系统通知：居中细条 -->
  <div v-if="isSystem" class="msg msg--system">
    <el-icon><InfoFilled /></el-icon>
    <span>{{ message?.content }}</span>
  </div>

  <div v-else class="msg" :class="isUser ? 'msg--user' : 'msg--assistant'">
    <div class="msg__avatar" :class="isUser ? 'msg__avatar--user' : 'msg__avatar--ai'">
      <el-icon>
        <component :is="isUser ? 'User' : 'MagicStick'" />
      </el-icon>
    </div>

    <div class="msg__body">
      <div class="msg__bubble">
        <!-- 思考过程（推理模型） -->
        <ReasoningBlock
          v-if="reasoning"
          :reasoning="reasoning"
          :active="active"
          :has-text="hasText"
        />

        <!-- 用户消息的附件预览 -->
        <AttachmentTray
          v-if="messageAttachments.length"
          :attachments="messageAttachments"
          readonly
        />

        <!-- 工具调用提示 -->
        <div v-if="toolCalls.length" class="msg__tools">
          <div v-for="call in toolCalls" :key="call.id" class="msg__tool">
            <el-icon class="msg__tool-icon"><Connection /></el-icon>
            <span class="msg__tool-name">{{ toolLabel(call.name) }}</span>
            <span v-if="toolArgs(call)" class="msg__tool-args bzp-mono">
              {{ toolArgs(call) }}
            </span>
          </div>
        </div>

        <!-- 岗位卡片（工具的结构化结果） -->
        <JobResultCards
          v-for="(block, index) in jobCardBlocks"
          :key="`jobs-${index}`"
          :jobs="block.jobs"
          :total="block.total"
          :query="block.query"
        />

        <!-- 单个岗位的完整 JD -->
        <JobDetailCard
          v-for="(block, index) in jobDetailBlocks"
          :key="`detail-${index}`"
          :job="block"
        />

        <!-- 抓取任务已创建 -->
        <TaskCreatedCard
          v-for="(block, index) in taskCreatedBlocks"
          :key="`task-${index}`"
          :task="block"
        />

        <!-- 抓取任务进度 -->
        <TaskStatusCard
          v-for="(block, index) in taskStatusBlocks"
          :key="`status-${index}`"
          :tasks="block.tasks"
        />

        <!-- 正文 -->
        <MarkdownBlock v-if="text" :source="text" />
        <div v-else-if="active && !reasoning" class="msg__pending">
          <el-icon class="is-spin"><Loading /></el-icon>
          正在生成…
        </div>

        <!-- 光标：表示还在流式输出 -->
        <span v-if="active && text" class="msg__cursor" />

        <!-- 错误提示 -->
        <el-alert
          v-if="errorMessage"
          type="error"
          :closable="false"
          show-icon
          class="msg__error"
        >
          <template #title>{{ errorMessage }}</template>
          <template v-if="errorSuggestion" #default>
            <div class="msg__error-suggestion">
              <el-icon><MagicStick /></el-icon>
              {{ errorSuggestion }}
            </div>
          </template>
        </el-alert>
      </div>

      <!-- 元信息 -->
      <div v-if="!isUser" class="msg__meta">
        <span v-if="modelLabel" class="msg__meta-item">
          <el-icon><Cpu /></el-icon>
          {{ modelLabel }}
        </span>
        <span v-if="latencyLabel" class="msg__meta-item">{{ latencyLabel }}</span>
        <span v-if="tokenLabel" class="msg__meta-item">{{ tokenLabel }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  align-items: flex-start;
}

.msg--user {
  flex-direction: row-reverse;
}

.msg--system {
  justify-content: center;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--bzp-text-secondary);
  background: #f4f6fa;
  border-radius: 999px;
  padding: 5px 14px;
  margin: 12px auto;
  width: fit-content;
}

.msg__avatar {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.msg__avatar--ai {
  background: linear-gradient(135deg, #3b7bff, #6aa4ff);
  color: #fff;
}

.msg__avatar--user {
  background: #eef1f6;
  color: #64748b;
}

.msg__body {
  min-width: 0;
  max-width: min(820px, 78%);
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.msg--user .msg__body {
  align-items: flex-end;
}

.msg__bubble {
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--bzp-panel);
  border: 1px solid var(--bzp-border);
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--bzp-text);
  word-break: break-word;
  position: relative;
  max-width: 100%;
}

.msg--user .msg__bubble {
  background: #eef4ff;
  border-color: #d8e4fd;
}

.msg__tools {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}

.msg__tool {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 6px 10px;
  border-radius: 7px;
  background: #f4f8ff;
  border: 1px solid #dbe6ff;
  font-size: 12px;
  color: #2b4a80;
}

.msg__tool-icon {
  color: var(--bzp-primary);
}

.msg__tool-name {
  font-weight: 500;
}

.msg__tool-args {
  font-size: 11px;
  color: #5a6b85;
}

.msg__pending {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--bzp-text-secondary);
}

.msg__pending .is-spin {
  animation: msg-spin 1.2s linear infinite;
}

@keyframes msg-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.msg__cursor {
  display: inline-block;
  width: 2px;
  height: 14px;
  margin-left: 2px;
  background: var(--bzp-primary);
  vertical-align: text-bottom;
  animation: msg-blink 1s step-end infinite;
}

@keyframes msg-blink {
  50% {
    opacity: 0;
  }
}

.msg__error {
  margin-top: 10px;
}

.msg__error-suggestion {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  line-height: 1.7;
}

.msg__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--bzp-text-secondary);
  padding-left: 4px;
}

.msg__meta-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
</style>
