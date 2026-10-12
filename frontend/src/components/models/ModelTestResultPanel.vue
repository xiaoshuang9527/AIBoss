<script setup lang="ts">
/**
 * 测试连接结果展示。
 *
 * 刻意把「错误分类 + 修复建议」放在最显眼的位置：用户点测试就是为了知道
 * 到底哪里配错了，只给一句"连接失败"没有价值。
 */
import type { ModelTestResult } from '@/types/model'

defineProps<{
  result: ModelTestResult | null
  loading?: boolean
}>()

/** 错误类型 → 展示样式与图标 */
const ERROR_STYLE: Record<string, { type: 'error' | 'warning' | 'info'; label: string }> = {
  auth_failed: { type: 'error', label: '认证失败' },
  model_not_found: { type: 'error', label: '模型不存在' },
  rate_limited: { type: 'warning', label: '请求被限流' },
  unsupported_capability: { type: 'warning', label: '能力不匹配' },
  invalid_messages: { type: 'warning', label: '消息格式不支持' },
  network_error: { type: 'error', label: '网络连接失败' },
  bad_response: { type: 'error', label: '响应格式异常' },
  not_configured: { type: 'warning', label: '配置不完整' },
  config_error: { type: 'error', label: '配置有误' },
  unknown: { type: 'info', label: '未知错误' },
}

function styleOf(errorType: string | null) {
  if (!errorType) return { type: 'info' as const, label: '错误' }
  return ERROR_STYLE[errorType] ?? { type: 'info' as const, label: errorType }
}
</script>

<template>
  <div v-loading="loading" class="test-result">
    <template v-if="result">
      <!-- 成功 -->
      <el-result
        v-if="result.ok"
        icon="success"
        title="连接正常"
        :sub-title="`耗时 ${result.latency_ms} ms`"
      >
        <template #extra>
          <el-descriptions :column="1" size="small" border class="test-result__desc">
            <el-descriptions-item label="模型回显">
              <span class="bzp-mono">{{ result.model_echo || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="响应延迟">
              {{ result.latency_ms }} ms
            </el-descriptions-item>
          </el-descriptions>
        </template>
      </el-result>

      <!-- 失败 -->
      <template v-else>
        <el-alert
          :type="styleOf(result.error_type).type === 'error' ? 'error' : 'warning'"
          :closable="false"
          show-icon
          class="test-result__alert"
        >
          <template #title>
            {{ styleOf(result.error_type).label }}
            <span class="test-result__msg">{{ result.message }}</span>
          </template>
          <template #default>
            <div v-if="result.suggestion" class="test-result__suggestion">
              <el-icon><MagicStick /></el-icon>
              {{ result.suggestion }}
            </div>
          </template>
        </el-alert>

        <el-descriptions :column="1" size="small" border class="test-result__desc">
          <el-descriptions-item v-if="result.error_type" label="错误类型">
            <span class="bzp-mono">{{ result.error_type }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="result.latency_ms" label="耗时">
            {{ result.latency_ms }} ms
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="result.detail" class="test-result__detail">
          <div class="test-result__detail-title">原始信息</div>
          <pre class="bzp-terminal test-result__pre">{{ result.detail }}</pre>
        </div>
      </template>
    </template>

    <el-empty v-else-if="!loading" description="点击「测试连接」开始探测" :image-size="70" />
  </div>
</template>

<style scoped>
.test-result {
  min-height: 120px;
}

.test-result__alert {
  margin-bottom: 14px;
}

.test-result__msg {
  margin-left: 6px;
  font-weight: 400;
}

.test-result__suggestion {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.7;
}

.test-result__desc {
  margin-bottom: 14px;
}

.test-result__detail-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--bzp-text-secondary);
  margin-bottom: 6px;
}

.test-result__pre {
  max-height: 200px;
  margin: 0;
  font-size: 11.5px;
}
</style>
