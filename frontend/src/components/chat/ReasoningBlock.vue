<script setup lang="ts">
/**
 * 推理模型的思考过程展示。
 *
 * 为什么需要这个组件
 * ------------------
 * DeepSeek V4.1 Flash 是推理模型，会先输出 `reasoning_content` 再输出正文。
 * 实测一次回答约 68 个思考分片、9 个正文分片 —— 也就是说**推理阶段占了大部分
 * 时间**。如果界面上不显示，用户会面对一段十几秒的空屏，以为程序卡死。
 *
 * 交互约定（与 ChatGPT 一致）：
 * * 正在思考时：展开显示，让用户看到进度
 * * 正文开始后：自动折叠成「已深度思考」，可点击展开回看
 */
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  reasoning: string
  /** 是否仍在生成中 */
  active: boolean
  /** 是否已经出现过正文（决定是否自动折叠） */
  hasText: boolean
}>()

const expanded = ref(false)
/** 用户是否手动操作过折叠状态（手动优先于自动） */
const userToggled = ref(false)

watch(
  () => props.hasText,
  (hasText) => {
    if (userToggled.value) return
    // 正文开始时自动折叠
    expanded.value = !hasText
  },
  { immediate: true },
)

function toggle() {
  userToggled.value = true
  expanded.value = !expanded.value
}

const title = computed(() => {
  if (props.active && !props.hasText) return '正在深度思考…'
  if (props.active) return '已深度思考（生成中）'
  return '已深度思考'
})

/** 思考内容长度提示，让用户知道折叠区里有多少内容 */
const lengthHint = computed(() => {
  const size = props.reasoning.length
  if (size < 1000) return `${size} 字`
  return `${(size / 1000).toFixed(1)}k 字`
})
</script>

<template>
  <div v-if="reasoning" class="reasoning" :class="{ 'reasoning--active': active && !hasText }">
    <button type="button" class="reasoning__head" @click="toggle">
      <el-icon class="reasoning__icon" :class="{ 'is-spin': active && !hasText }">
        <Loading v-if="active && !hasText" />
        <Opportunity v-else />
      </el-icon>
      <span class="reasoning__title">{{ title }}</span>
      <span class="reasoning__hint">{{ lengthHint }}</span>
      <el-icon class="reasoning__arrow" :class="{ 'is-expanded': expanded }">
        <ArrowRight />
      </el-icon>
    </button>

    <div v-show="expanded" class="reasoning__body">
      <pre class="reasoning__text">{{ reasoning }}</pre>
    </div>
  </div>
</template>

<style scoped>
.reasoning {
  border: 1px solid var(--bzp-border);
  border-radius: 8px;
  background: #f8fafd;
  overflow: hidden;
  margin-bottom: 10px;
}

.reasoning--active {
  border-color: #cddffd;
  background: #f4f8ff;
}

.reasoning__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  background: transparent;
  border: none;
  cursor: pointer;
  font-family: inherit;
  font-size: 12.5px;
  color: #4a5b76;
  text-align: left;
}

.reasoning__head:hover {
  background: rgba(47, 107, 255, 0.05);
}

.reasoning__icon {
  font-size: 14px;
  color: var(--bzp-primary);
  flex-shrink: 0;
}

.reasoning__icon.is-spin {
  animation: reasoning-spin 1.2s linear infinite;
}

@keyframes reasoning-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.reasoning__title {
  flex: 1;
  font-weight: 500;
}

.reasoning__hint {
  font-size: 11px;
  color: var(--bzp-text-secondary);
}

.reasoning__arrow {
  font-size: 12px;
  color: #9aa8bd;
  transition: transform 0.18s ease;
}

.reasoning__arrow.is-expanded {
  transform: rotate(90deg);
}

.reasoning__body {
  border-top: 1px dashed var(--bzp-border);
  padding: 10px 12px;
  max-height: 320px;
  overflow-y: auto;
}

.reasoning__text {
  margin: 0;
  font-size: 12px;
  line-height: 1.85;
  color: #5a6b85;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}
</style>
