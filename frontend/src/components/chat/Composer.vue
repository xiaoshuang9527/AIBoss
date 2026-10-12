<script setup lang="ts">
/**
 * 输入区：多行文本 + 粘贴截图 + 拖拽上传 + 附件预览。
 *
 * 三种上传方式都要支持，因为用户的习惯差异很大：
 * 1. 粘贴 —— 截图后直接 Ctrl+V，这是最常用的（简历截图场景）
 * 2. 拖拽 —— 从文件夹拖进来
 * 3. 点击选择 —— 传统方式
 *
 * Enter 发送、Shift+Enter 换行（与主流对话产品一致）。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AttachmentTray from './AttachmentTray.vue'
import type { ChatAttachment } from '@/types/chat'

const props = defineProps<{
  pending: ChatAttachment[]
  sending: boolean
  disabled: boolean
  maxAttachments: number
  accepted: string
  maxImageLabel: string
  maxDocLabel: string
}>()

const emit = defineEmits<{
  (event: 'send', content: string): void
  (event: 'upload', files: File[]): void
  (event: 'remove', id: number): void
  (event: 'stop'): void
}>()

const draft = ref('')
const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const canSend = computed(
  () => !props.sending && !props.disabled && (draft.value.trim().length > 0 || props.pending.length > 0),
)

const canAttach = computed(() => props.pending.length < props.maxAttachments)

function submit() {
  if (!canSend.value) return
  emit('send', draft.value.trim())
  draft.value = ''
}

function onKeydown(event: KeyboardEvent) {
  // Enter 发送；Shift+Enter 换行；输入法组合中不触发
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    submit()
  }
}

function pickFiles() {
  fileInput.value?.click()
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) {
    emit('upload', Array.from(input.files))
  }
  // 清空以便重复选择同一个文件
  input.value = ''
}

/** 粘贴：从剪贴板提取图片或文件 */
function onPaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return

  const files: File[] = []
  for (const item of Array.from(items)) {
    if (item.kind !== 'file') continue
    const file = item.getAsFile()
    if (!file) continue
    // 截图通常没有文件名，补一个便于识别
    if (!file.name || file.name === 'image.png') {
      const suffix = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')
      files.push(
        new File([file], `粘贴的截图-${new Date().toISOString().slice(11, 19)}.${suffix}`, {
          type: file.type,
        }),
      )
    } else {
      files.push(file)
    }
  }

  if (files.length) {
    event.preventDefault()
    emit('upload', files)
  }
}

/** 拖拽 */
function onDragOver(event: DragEvent) {
  event.preventDefault()
  if (!canAttach.value) return
  dragging.value = true
}

function onDragLeave() {
  dragging.value = false
}

function onDrop(event: DragEvent) {
  event.preventDefault()
  dragging.value = false
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (!files.length) return
  emit('upload', files)
}

function onUploadClick() {
  if (!canAttach.value) {
    ElMessage.warning(`单条消息最多 ${props.maxAttachments} 个附件`)
    return
  }
  pickFiles()
}
</script>

<template>
  <div
    class="composer"
    :class="{ 'composer--dragging': dragging }"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <AttachmentTray :attachments="pending" @remove="(id: number) => emit('remove', id)" />

    <div class="composer__box">
      <textarea
        v-model="draft"
        class="composer__input"
        rows="1"
        :placeholder="
          disabled
            ? '请先创建一个会话'
            : '描述你的需求，或直接粘贴简历截图。Enter 发送，Shift+Enter 换行'
        "
        :disabled="disabled"
        @keydown="onKeydown"
        @paste="onPaste"
      />

      <div class="composer__actions">
        <el-tooltip :content="`支持 ${accepted}`" placement="top">
          <el-button
            link
            :disabled="disabled || !canAttach"
            class="composer__attach"
            @click="onUploadClick"
          >
            <el-icon><Paperclip /></el-icon>
          </el-button>
        </el-tooltip>

        <span class="composer__hint bzp-muted">
          图片 ≤ {{ maxImageLabel }} · 文档 ≤ {{ maxDocLabel }}
        </span>

        <el-button
          v-if="sending"
          size="small"
          type="warning"
          plain
          @click="emit('stop')"
        >
          <el-icon><VideoPause /></el-icon>
          停止
        </el-button>
        <el-button v-else type="primary" size="small" :disabled="!canSend" @click="submit">
          <el-icon><Promotion /></el-icon>
          发送
        </el-button>
      </div>
    </div>

    <input
      ref="fileInput"
      type="file"
      class="composer__file"
      multiple
      :accept="accepted"
      @change="onFileChange"
    />

    <div v-if="dragging" class="composer__overlay">
      <el-icon><UploadFilled /></el-icon>
      松开即可上传
    </div>
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid var(--bzp-border);
  padding: 14px 18px 16px;
  background: var(--bzp-panel);
  position: relative;
}

.composer--dragging {
  background: #f4f8ff;
}

.composer__box {
  border: 1px solid var(--bzp-border);
  border-radius: 10px;
  background: #fff;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  overflow: hidden;
}

.composer__box:focus-within {
  border-color: #b9cdfb;
  box-shadow: 0 0 0 3px rgba(47, 107, 255, 0.09);
}

.composer__input {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  padding: 12px 14px 4px;
  font-family: inherit;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--bzp-text);
  background: transparent;
  min-height: 46px;
  max-height: 200px;
  overflow-y: auto;
}

.composer__input::placeholder {
  color: #a8b3c6;
}

.composer__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px 8px;
}

.composer__attach {
  font-size: 17px;
  color: #7c8aa0;
}

.composer__attach:hover {
  color: var(--bzp-primary);
}

.composer__hint {
  flex: 1;
  font-size: 11px;
}

.composer__file {
  display: none;
}

.composer__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(244, 248, 255, 0.94);
  border: 2px dashed var(--bzp-primary);
  border-radius: 10px;
  color: var(--bzp-primary);
  font-size: 13px;
  pointer-events: none;
}

.composer__overlay .el-icon {
  font-size: 28px;
}
</style>
