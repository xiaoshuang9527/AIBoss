<script setup lang="ts">
/**
 * 单个岗位的详情卡（tool: get_job_detail）。
 *
 * 与 `JobResultCards`（列表）的区别：列表卡片只显示摘要，这里展示 JD 全文。
 * 为什么需要单独一张：
 * 用户问「这个岗位具体要什么」「帮我分析匹不匹配」时，需要看到职责与要求原文，
 * 而不是 60 字摘要。摘要常驻上下文（省 token），全文按需拉取。
 */
import { computed, ref } from 'vue'
import type { JobDetailPayload } from '@/types/chat'

const props = defineProps<{ job: JobDetailPayload }>()

const expanded = ref(false)

const hasDetail = computed(
  () => Boolean(props.job.job_duty || props.job.job_requirement || props.job.jd),
)

/** 拼出完整 JD 文本：优先用结构化字段，缺失时回退到 jd */
const fullText = computed(() => {
  const parts: string[] = []
  if (props.job.job_duty) parts.push(`【岗位职责】\n${props.job.job_duty}`)
  if (props.job.job_requirement) parts.push(`【任职要求】\n${props.job.job_requirement}`)
  if (!parts.length && props.job.jd) parts.push(props.job.jd)
  return parts.join('\n\n')
})

/** 折叠状态下只显示前若干行，避免一条消息撑满整屏 */
const PREVIEW_LINES = 8
const preview = computed(() => {
  const lines = fullText.value.split('\n')
  if (lines.length <= PREVIEW_LINES) return fullText.value
  return `${lines.slice(0, PREVIEW_LINES).join('\n')}\n…`
})

const canCollapse = computed(() => fullText.value.split('\n').length > PREVIEW_LINES)

function goBoss() {
  if (props.job.job_link) {
    window.open(props.job.job_link, '_blank', 'noopener,noreferrer')
  }
}
</script>

<template>
  <div class="detail">
    <div class="detail__head">
      <div class="detail__title-row">
        <span class="detail__title">{{ job.title }}</span>
        <span class="detail__salary">{{ job.salary }}</span>
      </div>
      <el-button
        type="primary"
        size="small"
        :disabled="!job.job_link"
        @click="goBoss"
      >
        <el-icon><ChatDotRound /></el-icon>
        去 BOSS 沟通
      </el-button>
    </div>

    <div class="detail__company">
      {{ job.company }}
      <span v-if="job.company_scale || job.company_stage" class="detail__muted">
        · {{ [job.company_scale, job.company_stage].filter(Boolean).join(' · ') }}
      </span>
    </div>

    <div class="detail__meta">
      <span v-if="job.city"><el-icon><Location /></el-icon>{{ job.location || job.city }}</span>
      <span v-if="job.experience"><el-icon><Suitcase /></el-icon>{{ job.experience }}</span>
      <span v-if="job.degree"><el-icon><Reading /></el-icon>{{ job.degree }}</span>
      <span v-if="job.boss_active_status" class="detail__active">
        {{ job.boss_active_status }}
      </span>
    </div>

    <div v-if="job.welfare" class="detail__welfare">福利：{{ job.welfare }}</div>

    <!-- JD 正文 -->
    <div v-if="hasDetail" class="detail__jd">
      <pre class="detail__jd-text">{{ expanded ? fullText : preview }}</pre>
      <el-button
        v-if="canCollapse"
        link
        size="small"
        type="primary"
        class="detail__toggle"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : '展开完整 JD' }}
        <el-icon><component :is="expanded ? 'ArrowUp' : 'ArrowDown'" /></el-icon>
      </el-button>
      <div v-if="job.truncated" class="detail__truncated">
        ⚠️ JD 过长已截断，完整版本请到「岗位结果」页查看。
      </div>
    </div>

    <el-alert v-else type="info" :closable="false" show-icon class="detail__missing">
      <template #title>该岗位还没有 JD 详情</template>
      <template #default>
        抓取时可能关闭了「抓取详情」，或详情抓取失败。可以重新发起一次开启详情的抓取。
      </template>
    </el-alert>
  </div>
</template>

<style scoped>
.detail {
  border: 1px solid var(--bzp-border);
  border-radius: 9px;
  padding: 12px 14px;
  background: #fff;
  margin: 4px 0 10px;
}

.detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.detail__title-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}

.detail__title {
  font-size: 14px;
  font-weight: 600;
}

.detail__salary {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--bzp-danger);
  white-space: nowrap;
}

.detail__company {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
}

.detail__muted {
  color: #9aa8bd;
}

.detail__meta {
  margin-top: 7px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}

.detail__meta span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.detail__active {
  color: var(--bzp-success);
}

.detail__welfare {
  margin-top: 7px;
  font-size: 12px;
  color: #5a6b85;
}

.detail__jd {
  margin-top: 11px;
  padding-top: 10px;
  border-top: 1px dashed var(--bzp-border);
}

.detail__jd-text {
  margin: 0;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.85;
  color: var(--bzp-text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow-y: auto;
}

.detail__toggle {
  margin-top: 6px;
}

.detail__truncated {
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--bzp-warning);
}

.detail__missing {
  margin-top: 10px;
}
</style>
