<script setup lang="ts">
/**
 * 附件列表（预览 / 删除）。
 *
 * 图片显示缩略图（走 /raw 接口），文档显示类型图标 + 文件名 + 抽取状态。
 * `readonly` 用于消息里回显历史附件，此时不显示删除按钮。
 */
import { computed } from 'vue'
import { chatApi } from '@/api/chat'
import type { ChatAttachment } from '@/types/chat'

const props = withDefaults(
  defineProps<{
    attachments: ChatAttachment[]
    readonly?: boolean
  }>(),
  { readonly: false },
)

const emit = defineEmits<{ (event: 'remove', id: number): void }>()

/** 抽取状态 → 提示文案与样式 */
const STATUS_META: Record<string, { text: string; type: 'success' | 'warning' | 'info' | 'danger' }> =
  {
    done: { text: '已解析', type: 'success' },
    pending: { text: '解析中', type: 'info' },
    not_applicable: { text: '图片', type: 'info' },
    failed: { text: '解析失败', type: 'danger' },
  }

function statusOf(item: ChatAttachment) {
  if (item.extract_status === 'failed') return STATUS_META.failed
  return STATUS_META[item.extract_status] ?? STATUS_META.pending
}

const KIND_ICON: Record<string, string> = {
  pdf: 'Document',
  docx: 'Document',
  text: 'Document',
  image: 'Picture',
}

function iconOf(item: ChatAttachment): string {
  return KIND_ICON[item.kind] ?? 'Document'
}

function rawUrl(id: number): string {
  return chatApi.attachmentRawUrl(id)
}

const hasAny = computed(() => props.attachments.length > 0)
</script>

<template>
  <div v-if="hasAny" class="tray" :class="{ 'tray--readonly': readonly }">
    <div v-for="item in attachments" :key="item.id" class="tray__item">
      <img
        v-if="item.kind === 'image'"
        :src="rawUrl(item.id)"
        :alt="item.filename"
        class="tray__thumb"
      />
      <div v-else class="tray__icon">
        <el-icon><component :is="iconOf(item)" /></el-icon>
      </div>

      <div class="tray__info">
        <div class="tray__name" :title="item.filename">{{ item.filename }}</div>
        <div class="tray__meta">
          <span>{{ item.size_label }}</span>
          <el-tag :type="statusOf(item).type" size="small" effect="plain">
            {{ statusOf(item).text }}
          </el-tag>
        </div>
        <el-tooltip v-if="item.extract_error" :content="item.extract_error" placement="top">
          <div class="tray__error">解析出错，点击查看</div>
        </el-tooltip>
      </div>

      <el-button
        v-if="!readonly"
        class="tray__remove"
        link
        size="small"
        @click="emit('remove', item.id)"
      >
        <el-icon><Close /></el-icon>
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.tray {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.tray--readonly {
  margin-bottom: 12px;
}

.tray__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--bzp-border);
  border-radius: 8px;
  background: #fbfcfe;
  max-width: 260px;
  position: relative;
}

.tray__thumb {
  width: 42px;
  height: 42px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--bzp-border);
  flex-shrink: 0;
  background: #f4f6fa;
}

.tray__icon {
  width: 42px;
  height: 42px;
  border-radius: 6px;
  background: #eef3ff;
  color: var(--bzp-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  flex-shrink: 0;
}

.tray__info {
  min-width: 0;
  flex: 1;
}

.tray__name {
  font-size: 12.5px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tray__meta {
  margin-top: 3px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--bzp-text-secondary);
}

.tray__error {
  margin-top: 2px;
  font-size: 11px;
  color: var(--bzp-danger);
  cursor: help;
}

.tray__remove {
  position: absolute;
  top: 2px;
  right: 2px;
  color: #9aa8bd;
}

.tray__remove:hover {
  color: var(--bzp-danger);
}
</style>
