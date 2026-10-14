import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { chatApi, streamMessage, type StreamHandlers } from '@/api/chat'
import { modelApi } from '@/api/model'
import type {
  ChatAttachment,
  ChatConfig,
  ChatConversation,
  ChatMessage,
  ChatToolCallRecord,
  TaskStatusItem,
  ToolResultPayload,
} from '@/types/chat'
import type { ModelConfig } from '@/types/model'

/** 意图模式的中文标签（前端展示用） */
export const MODE_LABELS: Record<string, string> = {
  unset: '自动判断',
  job_hunting: '找岗位',
  interview_coach: '面试辅导',
  chat: '通用对话',
}

/** 可手动选择的模式（含 auto） */
export const MODE_OPTIONS: { value: string; label: string; description: string }[] = [
  { value: 'auto', label: '自动判断', description: '由 AI 根据你的话判断意图' },
  { value: 'job_hunting', label: '找岗位', description: '检索本地岗位库、发起抓取' },
  { value: 'interview_coach', label: '面试辅导', description: '简历与 JD 匹配分析、面试题' },
  { value: 'chat', label: '通用对话', description: '不限工具，普通问答' },
]

/** 前端本地的"正在生成中"消息（尚未落库，用负数 id 占位） */
export interface StreamingMessage {
  role: 'assistant'
  content: string
  reasoning: string
  toolCalls: ChatToolCallRecord[]
  /** 工具返回的结构化载荷，按出现顺序保存（岗位卡片等） */
  toolResults: ToolResultPayload[]
  modelLabel: string
  mode: string
  errorType: string | null
  errorMessage: string | null
  errorSuggestion: string | null
  /** 降级说明（如"模型不支持工具调用，本轮只做纯文本"） */
  notice: { message: string; suggestion?: string } | null
  active: boolean
  /** 是否已收到过正文（决定思考区默认折叠） */
  hasText: boolean
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<ChatConversation[]>([])
  const currentId = ref<number | null>(null)
  const messages = ref<ChatMessage[]>([])
  const attachments = ref<ChatAttachment[]>([])
  const config = ref<ChatConfig | null>(null)
  const models = ref<ModelConfig[]>([])
  const loading = ref(false)
  const sending = ref(false)
  const streaming = ref<StreamingMessage | null>(null)
  /** 当前选用的模型（对话页顶部下拉框） */
  const selectedModelId = ref<number | null>(null)

  /**
   * 本会话进行中/最近的任务状态（由 WebSocket 实时更新）。
   *
   * 用途：任务发起后用户会一直盯着对话看进度，这里让它无需刷新页面就能看到
   * 进度推进。key 是 task_id。
   */
  const liveTasks = ref<Record<number, TaskStatusItem>>({})
  /** WebSocket 是否已连接（连接失败时界面应能提示"进度不实时"） */
  const socketConnected = ref(false)
  /** 会话搜索关键词（空表示不过滤） */
  const searchQuery = ref('')

  let controller: AbortController | null = null

  const currentConversation = computed(
    () => conversations.value.find((item) => item.id === currentId.value) ?? null,
  )

  /** 附件 id → 附件对象，渲染消息附件用 */
  const attachmentMap = computed(() => {
    const map: Record<number, ChatAttachment> = {}
    for (const item of attachments.value) map[item.id] = item
    return map
  })

  /** 已启用的模型（下拉框只列这些） */
  const enabledModels = computed(() => models.value.filter((item) => item.is_enabled))

  const canSend = computed(
    () => !sending.value && (Boolean(currentId.value) || conversations.value.length >= 0),
  )

  // ------------------------------------------------------------------
  // 加载
  // ------------------------------------------------------------------
  async function initialize() {
    loading.value = true
    try {
      const [cfg, list, modelList] = await Promise.all([
        chatApi.config(),
        chatApi.listConversations(),
        modelApi.list({ enabled_only: true }),
      ])
      config.value = cfg
      conversations.value = list
      models.value = modelList

      const preferred =
        modelList.find((item) => item.is_default) ?? modelList[0] ?? null
      selectedModelId.value = preferred?.id ?? null

      if (list.length) {
        await selectConversation(list[0].id)
      }
    } finally {
      loading.value = false
    }
  }

  async function refreshConversations() {
    conversations.value = await chatApi.listConversations(50, searchQuery.value || undefined)
  }

  /**
   * 搜索会话（按标题或消息内容）。
   *
   * 搜消息内容而不只搜标题是刻意的：用户往往记得"我问过 Docker 那个对话"，
   * 而标题是自动生成的首条消息前 30 字，未必包含他记得的那个词。
   */
  async function searchConversations(query: string) {
    searchQuery.value = query
    conversations.value = await chatApi.listConversations(50, query || undefined)
  }

  async function selectConversation(id: number) {
    if (sending.value) return
    currentId.value = id
    const detail = await chatApi.getConversation(id)
    messages.value = detail.messages
    attachments.value = detail.attachments
    if (detail.conversation.model_config_id) {
      selectedModelId.value = detail.conversation.model_config_id
    }
  }

  async function createConversation(title = '新对话') {
    if (sending.value) return null
    const created = await chatApi.createConversation(title, selectedModelId.value ?? undefined)
    conversations.value = [created, ...conversations.value]
    currentId.value = created.id
    messages.value = []
    attachments.value = []
    return created
  }

  async function removeConversation(id: number) {
    await chatApi.deleteConversation(id)
    conversations.value = conversations.value.filter((item) => item.id !== id)
    if (currentId.value === id) {
      currentId.value = null
      messages.value = []
      attachments.value = []
      if (conversations.value.length) {
        await selectConversation(conversations.value[0].id)
      }
    }
  }

  async function renameConversation(id: number, title: string) {
    const updated = await chatApi.updateConversation(id, { title })
    const index = conversations.value.findIndex((item) => item.id === id)
    if (index >= 0) conversations.value[index] = updated
  }

  async function switchModel(modelId: number) {
    selectedModelId.value = modelId
    if (currentId.value) {
      await chatApi.updateConversation(currentId.value, { model_config_id: modelId })
      const index = conversations.value.findIndex((item) => item.id === currentId.value)
      if (index >= 0) {
        conversations.value[index] = {
          ...conversations.value[index],
          model_config_id: modelId,
        }
      }
    }
  }

  /**
   * 手动切换意图模式。
   *
   * 这是对「惰性路由」的必要补充：路由只在首轮/未定模式时判断（为了省一次
   * 模型往返），所以话题跳转时不会自动重判。用户可以用这个入口直接纠正，
   * 也可以选「自动判断」清空模式让下一轮重新判断。
   */
  async function switchMode(mode: string) {
    if (!currentId.value) return
    const updated = await chatApi.updateConversation(currentId.value, { mode, reset_slots: true })
    const index = conversations.value.findIndex((item) => item.id === currentId.value)
    if (index >= 0) conversations.value[index] = updated
  }

  // ------------------------------------------------------------------
  // 上传
  // ------------------------------------------------------------------
  async function uploadAttachment(file: File): Promise<ChatAttachment> {
    if (!currentId.value) {
      const created = await createConversation()
      if (!created) throw new Error('无法创建会话')
    }
    const uploaded = await chatApi.uploadAttachment(currentId.value as number, file)
    attachments.value = [...attachments.value, uploaded]
    return uploaded
  }

  // ------------------------------------------------------------------
  // 发送
  // ------------------------------------------------------------------
  async function send(content: string, attachmentIds: number[]) {
    if (!currentId.value || sending.value) return

    // 乐观插入用户消息，避免等待网络才看到自己的输入
    const optimistic: ChatMessage = {
      id: -Date.now(),
      conversation_id: currentId.value,
      role: 'user',
      content,
      content_type: 'text',
      content_data: null,
      reasoning: null,
      attachments: attachmentIds,
      tool_calls: [],
      prompt_tokens: 0,
      completion_tokens: 0,
      cached_tokens: 0,
      latency_ms: null,
      error_type: null,
      error_message: null,
      model_config_id: selectedModelId.value,
      model_label: null,
      created_at: new Date().toISOString(),
    }
    messages.value = [...messages.value, optimistic]

    sending.value = true
    streaming.value = {
      role: 'assistant',
      content: '',
      reasoning: '',
      toolCalls: [],
      toolResults: [],
      modelLabel: '',
      mode: currentConversation.value?.mode ?? 'unset',
      errorType: null,
      errorMessage: null,
      errorSuggestion: null,
      notice: null,
      active: true,
      hasText: false,
    }

    const handlers: StreamHandlers = {
      onMeta: (data) => {
        if (!streaming.value) return
        streaming.value.modelLabel = data.model_label
        streaming.value.mode = data.mode
        // 模式可能被路由改变，同步到会话对象以便页头显示
        const index = conversations.value.findIndex((item) => item.id === currentId.value)
        if (index >= 0) {
          conversations.value[index] = { ...conversations.value[index], mode: data.mode }
        }
      },
      onReasoning: (delta) => {
        if (streaming.value) streaming.value.reasoning += delta
      },
      onText: (delta) => {
        if (streaming.value) {
          streaming.value.content += delta
          streaming.value.hasText = true
        }
      },
      onToolCall: (data) => {
        streaming.value?.toolCalls.push({
          id: data.id,
          name: data.name,
          arguments: data.arguments,
        })
      },
      onToolResult: (data) => {
        // 只保留有结构化数据的载荷 —— 纯文本结果（如工具报错）不值得单独渲染
        if (data.data && data.payload_type !== 'text') {
          streaming.value?.toolResults.push(data.data)
        }
      },
      onNotice: (data) => {
        if (streaming.value) {
          streaming.value.notice = {
            message: data.message,
            suggestion: data.suggestion,
          }
        }
      },
      onError: (data) => {
        if (streaming.value) {
          streaming.value.errorType = data.error_type
          streaming.value.errorMessage = data.title
            ? `${data.title}：${data.message}`
            : data.message
          streaming.value.errorSuggestion = data.suggestion ?? null
        }
      },
      onDone: () => {
        void finalizeStream()
      },
    }

    controller = streamMessage(
      currentId.value,
      { content, attachment_ids: attachmentIds, model_config_id: selectedModelId.value ?? undefined },
      handlers,
    )
  }

  /** 流结束后把临时消息换成数据库里的正式记录 */
  async function finalizeStream() {
    if (!currentId.value) return
    try {
      const detail = await chatApi.getConversation(currentId.value)
      messages.value = detail.messages
      attachments.value = detail.attachments
      const index = conversations.value.findIndex((item) => item.id === currentId.value)
      if (index >= 0) {
        conversations.value[index] = detail.conversation
        // 标题可能被后端自动改了，重排一下列表让最新会话浮到前面
        conversations.value = [
          detail.conversation,
          ...conversations.value.filter((item) => item.id !== detail.conversation.id),
        ]
      }
    } finally {
      streaming.value = null
      sending.value = false
      controller = null
    }
  }

  function stopStreaming() {
    controller?.abort()
    controller = null
    sending.value = false
    if (streaming.value) {
      streaming.value.active = false
      // 保留已生成的部分，标记为已中断
      if (streaming.value.content) {
        messages.value = [
          ...messages.value,
          {
            id: -Date.now(),
            conversation_id: currentId.value as number,
            role: 'assistant',
            content: streaming.value.content,
            content_type: 'text',
            content_data: null,
            reasoning: streaming.value.reasoning || null,
            attachments: [],
            tool_calls: streaming.value.toolCalls,
            prompt_tokens: 0,
            completion_tokens: 0,
            cached_tokens: 0,
            latency_ms: null,
            error_type: null,
            error_message: '（已手动中断）',
            model_config_id: selectedModelId.value,
            model_label: streaming.value.modelLabel,
            created_at: new Date().toISOString(),
          },
        ]
      }
    }
    streaming.value = null
  }

  /**
   * 接收 WebSocket 推来的任务进度。
   *
   * 刻意**覆盖式**更新（而不是增量）：服务端推的是任务的最新快照，
   * 中间被节流丢弃的事件不需要补 —— 前端只关心"现在是什么状态"。
   */
  function applyTaskProgress(task: TaskStatusItem) {
    if (!task?.task_id) return
    liveTasks.value = { ...liveTasks.value, [task.task_id]: task }
  }

  /** 清掉已结束的实时任务卡片（用户手动关掉时用） */
  function dismissLiveTask(taskId: number) {
    const next = { ...liveTasks.value }
    delete next[taskId]
    liveTasks.value = next
  }

  return {
    conversations,
    currentId,
    messages,
    attachments,
    config,
    models,
    loading,
    sending,
    streaming,
    selectedModelId,
    liveTasks,
    socketConnected,
    searchQuery,
    currentConversation,
    attachmentMap,
    enabledModels,
    canSend,
    initialize,
    refreshConversations,
    searchConversations,
    selectConversation,
    createConversation,
    removeConversation,
    renameConversation,
    switchModel,
    switchMode,
    uploadAttachment,
    send,
    stopStreaming,
    applyTaskProgress,
    dismissLiveTask,
  }
})
