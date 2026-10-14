<script setup lang="ts">
/**
 * 对话页顶部的模型选择下拉框。
 *
 * 为什么要显示能力与延迟
 * ----------------------
 * 实测两个模型的延迟差了 8 倍（DeepSeek 1.5s vs Agnes 12.4s）。
 * 如果下拉框只显示名字，用户切到慢模型会以为程序卡死。
 * 所以这里显式展示：能力标签 + 最近一次测试延迟 + 默认标记。
 */
import { computed } from 'vue'
import type { ModelConfig } from '@/types/model'

const props = defineProps<{
  models: ModelConfig[]
  modelValue: number | null
  disabled?: boolean
  /** 当前是否有待发送的图片附件（决定要不要提示"不支持看图"） */
  hasPendingImages?: boolean
}>()

const emit = defineEmits<{ (event: 'update:modelValue', value: number): void }>()

const CAP_LABELS: Record<string, string> = {
  text: '文生文',
  vision: '看图',
  tool_calls: '工具',
  reasoning: '推理',
}

const selected = computed(() => props.models.find((item) => item.id === props.modelValue) ?? null)

function onChange(value: number) {
  emit('update:modelValue', value)
}

/** 延迟标签：超过 3 秒标黄，提示用户会比较慢 */
function latencyInfo(item: ModelConfig): { text: string; slow: boolean } | null {
  const value = item.last_test_latency_ms
  if (!value) return null
  return {
    text: value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`,
    slow: value >= 3000,
  }
}

function capsOf(item: ModelConfig): string[] {
  return item.capabilities.map((cap) => CAP_LABELS[cap] ?? cap)
}
</script>

<template>
  <div class="switcher">
    <el-select
      :model-value="modelValue ?? undefined"
      :disabled="disabled"
      placeholder="选择模型"
      size="default"
      class="switcher__select"
      popper-class="model-switcher-popper"
      :fit-input-width="false"
      @update:model-value="onChange"
    >
      <el-option v-for="item in models" :key="item.id" :label="item.display_name" :value="item.id">
        <div class="switcher__option">
          <div class="switcher__row">
            <span class="switcher__option-name" :title="item.display_name">
              {{ item.display_name }}
            </span>
            <el-tag
              v-if="item.is_default"
              type="primary"
              size="small"
              effect="dark"
              round
              class="switcher__badge"
            >
              默认
            </el-tag>
            <span
              v-if="latencyInfo(item)"
              class="switcher__latency"
              :class="{ 'switcher__latency--slow': latencyInfo(item)?.slow }"
            >
              {{ latencyInfo(item)?.text }}
            </span>
          </div>
          <div class="switcher__row switcher__row--sub">
            <el-tag
              v-for="cap in capsOf(item)"
              :key="cap"
              size="small"
              effect="plain"
              type="info"
              class="switcher__cap"
            >
              {{ cap }}
            </el-tag>
            <span class="switcher__option-id">{{ item.model_id }}</span>
          </div>
        </div>
      </el-option>

      <template #empty>
        <div class="switcher__empty">
          还没有启用任何模型
          <div class="bzp-muted">请到「系统设置 → 模型管理」添加</div>
        </div>
      </template>
    </el-select>

    <!-- 当前模型的摘要，让用户不展开也知道自己在用什么 -->
    <div v-if="selected" class="switcher__current">
      <el-tag
        v-for="cap in capsOf(selected)"
        :key="cap"
        size="small"
        effect="plain"
        type="info"
      >
        {{ cap }}
      </el-tag>
      <!--
        能力提示只在**真的会挡住用户**时显示。
        早先是"只要模型不支持看图就常驻警告"，结果问「n8n 是什么」这种
        纯文本问题时也挂着一句橙色「无法解析简历截图」—— 看起来像在报错，
        实际完全无关。现在仅在对应场景出现：
        * 有待发送的图片附件时，才提示不支持看图
      -->
      <span v-if="hasPendingImages && !selected.capabilities.includes('vision')" class="switcher__warn">
        <el-icon><WarningFilled /></el-icon>
        将发送的图片无法被该模型识别，请换用支持「看图」的模型
      </span>
    </div>
  </div>
</template>

<style scoped>
.switcher {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-width: 0;
}

.switcher__select {
  width: 248px;
}

.switcher__option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 0;
  line-height: 1.35;
  min-width: 0;
}

.switcher__row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.switcher__row--sub {
  gap: 4px;
  flex-wrap: wrap;
}

.switcher__option-name {
  font-weight: 500;
  /* 长模型名截断，而不是把右侧的延迟标签挤出去 */
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switcher__badge,
.switcher__latency {
  flex-shrink: 0;
}

.switcher__cap {
  flex-shrink: 0;
}

.switcher__option-id {
  font-size: 11px;
  color: var(--bzp-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.switcher__latency {
  font-size: 11px;
  color: var(--bzp-success);
}

.switcher__latency--slow {
  color: var(--bzp-warning);
  font-weight: 600;
}

.switcher__empty {
  padding: 12px;
  text-align: center;
  font-size: 12.5px;
  line-height: 1.8;
}

.switcher__current {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
}

.switcher__warn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--bzp-warning);
}
</style>

<!--
  下拉面板样式必须**不加 scoped**。

  Element Plus 的 select 下拉是 teleport 到 body 的，scoped 样式带的
  data-v 属性根本作用不到那里，所以 .switcher__option 之前完全没生效，
  选项被默认行高裁剪成一行 —— 这正是"能力标签显示不出来"的原因。

  另一个关键点：Element Plus 给 .el-select-dropdown__item 设了
  height: 34px，多行内容会被裁掉，必须显式覆盖成 auto。
-->
<style>
.model-switcher-popper .el-select-dropdown__list {
  padding: 4px;
}

/* 下拉开得比输入框宽一些：两行内容（名称 + 能力标签 + 模型 ID）
   挤在 248px 里会频繁换行，反而更难读。 */
.model-switcher-popper {
  min-width: 320px !important;
}

.model-switcher-popper .el-select-dropdown__item {
  /* 让选项能放下两行（名称行 + 能力行） */
  height: auto;
  line-height: 1.35;
  padding: 8px 10px;
  border-radius: 7px;
  margin-bottom: 2px;
}

.model-switcher-popper .el-select-dropdown__item:last-child {
  margin-bottom: 0;
}

.model-switcher-popper .el-select-dropdown__item.is-selected {
  font-weight: 500;
}
</style>
