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
      @update:model-value="onChange"
    >
      <el-option v-for="item in models" :key="item.id" :label="item.display_name" :value="item.id">
        <div class="switcher__option">
          <div class="switcher__option-main">
            <span class="switcher__option-name">{{ item.display_name }}</span>
            <el-tag v-if="item.is_default" type="primary" size="small" effect="dark" round>
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
          <div class="switcher__option-sub">
            <el-tag
              v-for="cap in capsOf(item)"
              :key="cap"
              size="small"
              effect="plain"
              type="info"
            >
              {{ cap }}
            </el-tag>
            <span class="bzp-muted switcher__option-id">{{ item.model_id }}</span>
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
      <span v-if="!selected.capabilities.includes('vision')" class="switcher__warn">
        <el-icon><WarningFilled /></el-icon>
        不支持看图，无法解析简历截图
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
  gap: 3px;
  padding: 3px 0;
  line-height: 1.4;
}

.switcher__option-main {
  display: flex;
  align-items: center;
  gap: 6px;
}

.switcher__option-name {
  font-weight: 500;
}

.switcher__option-sub {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.switcher__option-id {
  font-size: 11px;
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
