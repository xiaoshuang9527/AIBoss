<script setup lang="ts">
/**
 * 单个模型卡片。
 *
 * 展示：显示名称、供应商、模型 ID、能力标签、掩码密钥、上次测试结果、启用开关。
 * 操作：测试连接、编辑、克隆、设为默认、启用/禁用、删除。
 */
import { computed } from 'vue'
import type { ModelConfig } from '@/types/model'

const props = defineProps<{
  model: ModelConfig
  testing: boolean
  capabilityLabel: (value: string) => string
  formatTime: (value: string | null) => string
}>()

const emit = defineEmits<{
  (event: 'edit', model: ModelConfig): void
  (event: 'test', model: ModelConfig): void
  (event: 'clone', model: ModelConfig): void
  (event: 'remove', model: ModelConfig): void
  (event: 'set-default', model: ModelConfig): void
  (event: 'toggle-enabled', model: ModelConfig, enabled: boolean): void
}>()

const testTag = computed(() => {
  if (props.model.last_test_ok === true) {
    return { type: 'success' as const, text: `连通正常 ${props.model.last_test_latency_ms ?? 0}ms` }
  }
  if (props.model.last_test_ok === false) {
    return { type: 'danger' as const, text: '连接失败' }
  }
  return { type: 'info' as const, text: '尚未测试' }
})
</script>

<template>
  <article class="model-card bzp-panel" :class="{ 'model-card--disabled': !model.is_enabled }">
    <header class="model-card__head">
      <div class="model-card__title-wrap">
        <div class="model-card__title">
          <span class="model-card__name">{{ model.display_name }}</span>
          <el-tag v-if="model.is_default" type="primary" size="small" effect="dark" round>
            默认
          </el-tag>
        </div>
        <div class="model-card__provider">
          <el-tag size="small" effect="plain">{{ model.provider_label }}</el-tag>
          <span class="bzp-mono model-card__model-id">{{ model.model_id }}</span>
        </div>
      </div>

      <el-switch
        :model-value="model.is_enabled"
        size="small"
        @update:model-value="(value: boolean) => emit('toggle-enabled', model, value)"
      />
    </header>

    <div class="model-card__caps">
      <el-tag
        v-for="cap in model.capabilities"
        :key="cap"
        size="small"
        effect="plain"
        type="info"
      >
        {{ capabilityLabel(cap) }}
      </el-tag>
      <span v-if="!model.capabilities.length" class="bzp-muted model-card__no-cap">
        未配置能力标签，无法参与对话
      </span>
    </div>

    <dl class="model-card__meta">
      <div class="model-card__meta-row">
        <dt>API 地址</dt>
        <dd class="bzp-mono">{{ model.base_url || '（使用默认）' }}</dd>
      </div>
      <div class="model-card__meta-row">
        <dt>密钥</dt>
        <dd>
          <span v-if="model.has_api_key" class="bzp-mono bzp-muted">
            {{ model.api_key_masked }}
          </span>
          <el-tag v-else size="small" type="warning" effect="plain">未配置</el-tag>
        </dd>
      </div>
      <div class="model-card__meta-row">
        <dt>上次测试</dt>
        <dd>
          <el-tooltip
            :disabled="!model.last_test_message"
            :content="model.last_test_message || ''"
            placement="top"
          >
            <el-tag :type="testTag.type" size="small" effect="light">{{ testTag.text }}</el-tag>
          </el-tooltip>
          <span class="bzp-muted model-card__time">{{ formatTime(model.last_tested_at) }}</span>
        </dd>
      </div>
    </dl>

    <footer class="model-card__footer">
      <el-button size="small" type="primary" plain :loading="testing" @click="emit('test', model)">
        <el-icon><Link /></el-icon>
        测试连接
      </el-button>
      <el-button size="small" @click="emit('edit', model)">
        <el-icon><Edit /></el-icon>
        编辑
      </el-button>
      <el-button size="small" text @click="emit('clone', model)">克隆</el-button>
      <el-button
        v-if="!model.is_default"
        size="small"
        text
        type="primary"
        @click="emit('set-default', model)"
      >
        设为默认
      </el-button>
      <el-button size="small" text type="danger" @click="emit('remove', model)">删除</el-button>
    </footer>
  </article>
</template>

<style scoped>
.model-card {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: box-shadow 0.15s ease, transform 0.15s ease;
}

.model-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 26px rgba(16, 26, 44, 0.1);
}

.model-card--disabled {
  opacity: 0.62;
  background: #fafbfd;
}

.model-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.model-card__title-wrap {
  min-width: 0;
  flex: 1;
}

.model-card__title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.model-card__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--bzp-text);
}

.model-card__provider {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.model-card__model-id {
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
  word-break: break-all;
}

.model-card__caps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 24px;
}

.model-card__no-cap {
  font-size: 12px;
}

.model-card__meta {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12px;
}

.model-card__meta-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.model-card__meta-row dt {
  flex-shrink: 0;
  width: 62px;
  color: var(--bzp-text-secondary);
}

.model-card__meta-row dd {
  margin: 0;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  word-break: break-all;
}

.model-card__time {
  font-size: 11px;
}

.model-card__footer {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  border-top: 1px solid var(--bzp-border);
  padding-top: 12px;
  margin-top: auto;
}
</style>
