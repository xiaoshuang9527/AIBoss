<script setup lang="ts">
/**
 * AI 对话页。
 *
 * 布局：左侧会话列表 + 右侧消息流与输入区。
 *
 * 任务进度通过 `/api/ws/chat/{id}` 实时推送：AI 发起抓取后用户不必刷新页面，
 * 进度条会自己往前走。这条通道只推**本会话**发起的任务（见后端 chat_events.py）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import Composer from '@/components/chat/Composer.vue'
import ModelSwitcher from '@/components/chat/ModelSwitcher.vue'
import TaskStatusCard from '@/components/chat/TaskStatusCard.vue'
import { useChatSocket } from '@/composables/useChatSocket'
import { useChatStore, MODE_OPTIONS } from '@/stores/chat'
import type { ChatAttachment } from '@/types/chat'

const store = useChatStore()

// 对话级任务进度通道
const socket = useChatSocket({
  onTaskProgress: (payload) => store.applyTaskProgress(payload.task),
})
watch(socket.connected, (value) => {
  store.socketConnected = value
})
// 切换会话时重连到对应的通道
watch(
  () => store.currentId,
  (id) => socket.connect(id),
)

/** 进行中的任务（实时进度面板用） */
const liveTaskList = computed(() =>
  Object.values(store.liveTasks).sort((a, b) => b.task_id - a.task_id),
)
const hasLiveTasks = computed(() => liveTaskList.value.length > 0)

/** 搜索：防抖 300ms，避免每敲一个字就打一次接口 */
const searchInput = ref('')
let searchTimer: number | null = null

function onSearchInput(value: string) {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    void store.searchConversations(value)
  }, 300)
}

/** 待发送里是否有图片 —— 决定模型选择器要不要提示"不支持看图" */
const pendingImages = computed(() =>
  pendingAttachments.value.some((item) => item.kind === 'image'),
)

const scrollArea = ref<HTMLDivElement | null>(null)
const pendingAttachments = ref<ChatAttachment[]>([])

const config = computed(() => store.config)
const disabled = computed(() => store.loading)
const currentTitle = computed(() => store.currentConversation?.title ?? '新对话')

/** 假模型模式提示 */
const fakeMode = computed(() => store.config?.fake_llm === true)

/** 当前模式（unset 显示为「自动判断」） */
const currentMode = computed(() => store.currentConversation?.mode ?? 'unset')

const modeLabel = computed(() => {
  const found = MODE_OPTIONS.find((item) => item.value === currentMode.value)
  if (found) return found.label
  // 已路由成具体模式时，currentMode 是 job_hunting 等，去 MODE_OPTIONS 里也找得到
  return currentMode.value
})

const modeTone = computed(() => {
  switch (currentMode.value) {
    case 'job_hunting':
      return 'primary'
    case 'interview_coach':
      return 'warning'
    default:
      return 'info'
  }
})

/** 消息列表 + 流式中的临时消息合并渲染 */
const renderedMessages = computed(() => store.messages)

async function scrollToBottom(smooth = false) {
  await nextTick()
  const element = scrollArea.value
  if (!element) return
  element.scrollTo({ top: element.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

// 新消息或流式内容变化时自动滚到底
watch(
  () => [store.messages.length, store.streaming?.content, store.streaming?.reasoning],
  () => void scrollToBottom(),
)

onMounted(async () => {
  try {
    await store.initialize()
    // initialize 可能已经选中了一个会话，此时 watch 不会触发（值没变），
    // 所以这里显式连一次
    socket.connect(store.currentId)
    await scrollToBottom()
  } catch {
    ElMessage.error('加载对话模块失败，请确认后端服务正常')
  }
})

async function onNewConversation() {
  try {
    await store.createConversation()
    pendingAttachments.value = []
  } catch {
    ElMessage.error('创建会话失败')
  }
}

async function onSelect(id: number) {
  if (id === store.currentId) return
  try {
    await store.selectConversation(id)
    pendingAttachments.value = []
    await scrollToBottom()
  } catch {
    ElMessage.error('加载会话失败')
  }
}

async function onDelete(id: number) {
  try {
    await ElMessageBox.confirm(
      '删除会话会同时清理已上传的附件文件（简历等），且不可恢复。确认删除？',
      '删除会话',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await store.removeConversation(id)
  ElMessage.success('已删除')
}

async function onRename(id: number, current: string) {
  try {
    const result = await ElMessageBox.prompt('输入新的会话名称', '重命名', {
      inputValue: current,
      inputValidator: (value: string) => (value.trim() ? true : '名称不能为空'),
    })
    await store.renameConversation(id, result.value.trim())
    ElMessage.success('已重命名')
  } catch {
    /* 用户取消 */
  }
}

// ---------------- 附件 ----------------
async function onUpload(files: File[]) {
  const limit = config.value?.max_attachments ?? 5
  const remaining = limit - pendingAttachments.value.length
  if (remaining <= 0) {
    ElMessage.warning(`单条消息最多 ${limit} 个附件`)
    return
  }

  const accepted = files.slice(0, remaining)
  if (files.length > remaining) {
    ElMessage.warning(`超出数量上限，只会上传前 ${remaining} 个`)
  }

  for (const file of accepted) {
    try {
      const uploaded = await store.uploadAttachment(file)
      pendingAttachments.value = [...pendingAttachments.value, uploaded]
    } catch (error) {
      // http 拦截器已经提示了具体原因（类型不支持 / 过大），这里不再重复
      void error
    }
  }
}

function onRemovePending(id: number) {
  pendingAttachments.value = pendingAttachments.value.filter((item) => item.id !== id)
}

// ---------------- 发送 ----------------
async function onSend(content: string) {
  if (!store.currentId) {
    const created = await store.createConversation()
    if (!created) return
  }
  const ids = pendingAttachments.value.map((item) => item.id)
  pendingAttachments.value = []
  await store.send(content, ids)
}

function timeLabel(value: string | null): string {
  if (!value) return ''
  const date = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  return sameDay
    ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
</script>

<template>
  <div class="chat">
    <!-- ============ 左侧：会话列表 ============ -->
    <aside class="chat__aside">
      <div class="chat__aside-head">
        <el-button type="primary" size="small" class="chat__new" @click="onNewConversation">
          <el-icon><Plus /></el-icon>
          新建对话
        </el-button>
        <!-- 搜索：同时匹配标题与消息内容（见后端 list_conversations） -->
        <el-input
          v-model="searchInput"
          size="small"
          placeholder="搜索对话或消息内容"
          clearable
          class="chat__search"
          @input="onSearchInput"
          @clear="onSearchInput('')"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
      </div>

      <div v-loading="store.loading" class="chat__list">
        <div
          v-for="item in store.conversations"
          :key="item.id"
          class="chat__item"
          :class="{ 'chat__item--active': item.id === store.currentId }"
          @click="onSelect(item.id)"
        >
          <div class="chat__item-main">
            <div class="chat__item-title">{{ item.title }}</div>
            <div class="chat__item-sub">
              <span>{{ timeLabel(item.last_message_at || item.updated_at) }}</span>
              <span>·</span>
              <span>{{ item.message_count }} 条</span>
            </div>
          </div>

          <el-dropdown trigger="click" @click.stop>
            <el-button link size="small" class="chat__item-more" @click.stop>
              <el-icon><MoreFilled /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="onRename(item.id, item.title)">重命名</el-dropdown-item>
                <el-dropdown-item divided @click="onDelete(item.id)">删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <el-empty
          v-if="!store.conversations.length && !store.loading"
          description="还没有对话"
          :image-size="60"
        >
          <el-button size="small" type="primary" @click="onNewConversation">开始第一个对话</el-button>
        </el-empty>
      </div>
    </aside>

    <!-- ============ 右侧：对话区 ============ -->
    <section class="chat__main">
      <header class="chat__header">
        <div class="chat__header-left">
          <div class="chat__title">{{ currentTitle }}</div>

          <!-- 模式指示 + 手动切换：惰性路由在话题跳转时不会重判，
               所以必须给用户一个纠正入口 -->
          <el-dropdown
            v-if="store.currentId"
            trigger="click"
            @command="(value: string) => store.switchMode(value)"
          >
            <el-tag :type="modeTone" size="small" effect="light" round class="chat__mode-tag">
              <el-icon><component :is="currentMode === 'interview_coach' ? 'Notebook' : 'Compass'" /></el-icon>
              {{ modeLabel }}
              <el-icon class="chat__mode-arrow"><ArrowDown /></el-icon>
            </el-tag>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-for="option in MODE_OPTIONS"
                  :key="option.value"
                  :command="option.value"
                >
                  <div class="chat__mode-option">
                    <span class="chat__mode-option-label">{{ option.label }}</span>
                    <span class="chat__mode-option-desc">{{ option.description }}</span>
                  </div>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-tag v-if="fakeMode" type="warning" size="small" effect="light" round>
            假模型 echo
          </el-tag>
        </div>

        <ModelSwitcher
          v-model="store.selectedModelId"
          :models="store.enabledModels"
          :disabled="store.sending"
          :has-pending-images="pendingImages"
          @update:model-value="store.switchModel"
        />
      </header>

      <el-alert
        v-if="fakeMode"
        type="warning"
        :closable="false"
        show-icon
        class="chat__banner"
      >
        <template #title>当前回复由假模型生成，不消耗 API 额度</template>
        <template #default>
          这一阶段用于验证「上传 → 发送 → 流式输出 → 界面渲染」整条链路。
          后端把 <code>BZP_FAKE_LLM</code> 设为 <code>false</code> 后即接入真实模型。
        </template>
      </el-alert>

      <!-- 消息流 -->
      <div ref="scrollArea" class="chat__scroll">
        <!-- 实时任务进度：置顶显示，任务跑完前一直可见 -->
        <div v-if="hasLiveTasks" class="chat__live">
          <TaskStatusCard :tasks="liveTaskList" />
        </div>

        <div v-if="!renderedMessages.length && !store.streaming" class="chat__empty">
          <el-icon class="chat__empty-icon"><ChatDotRound /></el-icon>
          <h3>开始一段对话</h3>
          <p class="bzp-muted">可以问岗位、聊面试，或者直接粘贴一张简历截图。</p>
          <div class="chat__samples">
            <div class="chat__sample" @click="onSend('帮我在深圳找 Python 后端岗位，薪资 15k-25k')">
              帮我在深圳找 Python 后端岗位
            </div>
            <div class="chat__sample" @click="onSend('解释一下 STAR 法则怎么用')">
              解释一下 STAR 法则怎么用
            </div>
          </div>
        </div>

        <MessageBubble
          v-for="item in renderedMessages"
          :key="item.id"
          :message="item"
          :attachments="store.attachmentMap"
        />

        <MessageBubble
          v-if="store.streaming"
          :streaming="store.streaming"
          :attachments="store.attachmentMap"
        />
      </div>

      <!-- 输入区 -->
      <Composer
        :pending="pendingAttachments"
        :sending="store.sending"
        :disabled="disabled"
        :max-attachments="config?.max_attachments ?? 5"
        :accepted="config?.accepted ?? ''"
        :max-image-label="config?.max_image_label ?? '8MB'"
        :max-doc-label="config?.max_doc_label ?? '20MB'"
        @send="onSend"
        @upload="onUpload"
        @remove="onRemovePending"
        @stop="store.stopStreaming"
      />
    </section>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  gap: 16px;
  height: calc(100vh - 168px);
  min-height: 520px;
}

/* ---------- 左侧 ---------- */
.chat__aside {
  width: 236px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bzp-panel);
  border: 1px solid var(--bzp-border);
  border-radius: var(--bzp-radius);
  overflow: hidden;
}

.chat__aside-head {
  padding: 12px;
  border-bottom: 1px solid var(--bzp-border);
}

.chat__new {
  width: 100%;
}

.chat__search {
  margin-top: 8px;
}

.chat__list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.chat__item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.chat__item:hover {
  background: #f4f6fa;
}

.chat__item--active {
  background: #eef4ff;
}

.chat__item-main {
  flex: 1;
  min-width: 0;
}

.chat__item-title {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__item-sub {
  margin-top: 2px;
  display: flex;
  gap: 4px;
  font-size: 11px;
  color: var(--bzp-text-secondary);
}

.chat__item-more {
  opacity: 0;
  color: #9aa8bd;
}

.chat__item:hover .chat__item-more {
  opacity: 1;
}

/* ---------- 右侧 ---------- */
.chat__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bzp-panel);
  border: 1px solid var(--bzp-border);
  border-radius: var(--bzp-radius);
  overflow: hidden;
}

.chat__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--bzp-border);
  flex-wrap: wrap;
}

.chat__header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.chat__title {
  font-size: 14.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 320px;
}

.chat__mode-tag {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.chat__mode-arrow {
  font-size: 10px;
  opacity: 0.7;
}

.chat__mode-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.4;
}

.chat__mode-option-label {
  font-weight: 500;
}

.chat__mode-option-desc {
  font-size: 11px;
  color: var(--bzp-text-secondary);
}

.chat__banner {
  border-radius: 0;
  border-left: none;
  border-right: none;
}

.chat__banner code {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}

.chat__scroll {
  flex: 1;
  overflow-y: auto;
  padding: 20px 22px 8px;
  background: #fbfcfe;
}

.chat__live {
  margin-bottom: 4px;
}

/* ---------- 空态 ---------- */
.chat__empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  text-align: center;
}

.chat__empty-icon {
  font-size: 42px;
  color: #cdd8e8;
  margin-bottom: 6px;
}

.chat__empty h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.chat__empty p {
  margin: 0;
  font-size: 13px;
}

.chat__samples {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(460px, 100%);
}

.chat__sample {
  padding: 9px 14px;
  border: 1px solid var(--bzp-border);
  border-radius: 8px;
  font-size: 12.5px;
  color: #4a5b76;
  cursor: pointer;
  transition: all 0.15s ease;
}

.chat__sample:hover {
  border-color: #b9cdfb;
  background: #f4f8ff;
  color: var(--bzp-primary);
}

@media (max-width: 1000px) {
  .chat__aside {
    display: none;
  }
}
</style>
