<script setup lang="ts">
/**
 * 《匹配度诊断报告》卡片（tool: analyze_match）。
 *
 * 为什么用卡片而不是让 AI 讲
 * --------------------------
 * 报告有固定结构：四个维度、风险点、5-8 道 STAR 面试题。用自由文本输出的话
 * 每轮格式都不一样，用户没法逐题准备，也没法一眼看出哪个维度是短板。
 *
 * 这个组件刻意做成"能当练习手册用"：
 * - 维度用颜色区分（匹配/部分/不匹配），短板一眼可见
 * - 面试题每道独立成块，带 STAR 四段结构提示，可以逐题对照准备
 * - 风险点给出「为什么会成为风险 + 建议怎么应对」，不只是罗列问题
 */
import { computed, ref } from 'vue'
import type { MatchReportPayload } from '@/types/chat'

const props = defineProps<{ payload: MatchReportPayload }>()

const report = computed(() => props.payload.report)

/** 只展开第一道题，其余折叠，避免一次性铺满整屏 */
const expandedQuestions = ref<Set<number>>(new Set([0]))

function toggleQuestion(index: number) {
  const next = new Set(expandedQuestions.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedQuestions.value = next
}

const LEVEL_META: Record<string, { label: string; tone: string; icon: string }> = {
  match: { label: '匹配', tone: 'match', icon: 'CircleCheckFilled' },
  partial: { label: '部分匹配', tone: 'partial', icon: 'WarningFilled' },
  mismatch: { label: '不匹配', tone: 'mismatch', icon: 'CircleCloseFilled' },
}

function levelMeta(level: string) {
  return LEVEL_META[level] ?? LEVEL_META.partial
}

/** 分数配色：低于 50 明确示警，不要给虚假的安慰 */
const scoreTone = computed(() => {
  const score = report.value.overall_score
  if (score === null || score === undefined) return 'unknown'
  if (score >= 75) return 'good'
  if (score >= 50) return 'medium'
  return 'poor'
})

/** 解析失败时不能显示 0 分 —— 0 是一个真实可能的评分（完全不匹配） */
const hasScore = computed(
  () => report.value.overall_score !== null && report.value.overall_score !== undefined,
)

const STAR_LABELS: { key: keyof MatchReportPayload['report']['questions'][0]['star']; label: string; hint: string }[] = [
  { key: 'situation', label: 'S 情境', hint: '什么样的背景' },
  { key: 'task', label: 'T 任务', hint: '你负责什么' },
  { key: 'action', label: 'A 行动', hint: '具体做了什么' },
  { key: 'result', label: 'R 结果', hint: '可量化的成果' },
]

function copyQuestion(index: number) {
  const item = report.value.questions[index]
  const star = item.star
  const text = [
    item.question,
    '',
    `S 情境：${star.situation}`,
    `T 任务：${star.task}`,
    `A 行动：${star.action}`,
    `R 结果：${star.result}`,
    item.tips ? `\n提示：${item.tips}` : '',
  ].join('\n')
  void navigator.clipboard?.writeText(text)
}
</script>

<template>
  <div class="match">
    <!-- 头部：结论 + 分数 -->
    <div class="match__head" :class="`match__head--${scoreTone}`">
      <div class="match__score">
        <template v-if="hasScore">
          <span class="match__score-num">{{ report.overall_score }}</span>
          <span class="match__score-unit">/100</span>
        </template>
        <template v-else>
          <span class="match__score-na">无评分</span>
        </template>
      </div>
      <div class="match__head-right">
        <div class="match__head-title">匹配度诊断报告</div>
        <div class="match__verdict">{{ report.verdict || '（无结论）' }}</div>
        <div v-if="payload.job" class="match__job">
          目标岗位：{{ payload.job.title }} · {{ payload.job.company }} ·
          {{ payload.job.salary }}
        </div>
        <div class="match__sources">
          <span>简历：{{ payload.resume_source || '未标明' }}</span>
          <span>岗位：{{ payload.jd_source || '未标明' }}</span>
        </div>
      </div>
    </div>

    <el-alert
      v-if="report.parse_error"
      type="warning"
      :closable="false"
      show-icon
      class="match__alert"
    >
      <template #title>报告结构化解析失败</template>
      <template #default>
        已把模型原始输出附在下方。格式异常通常是模型没有严格按 JSON 输出，
        可以重新生成一次。
      </template>
    </el-alert>

    <!-- 四个维度 -->
    <div v-if="report.dimensions.length" class="match__section">
      <div class="match__section-title">四维诊断</div>
      <div class="match__dims">
        <div
          v-for="dim in report.dimensions"
          :key="dim.name"
          class="dim"
          :class="`dim--${levelMeta(dim.level).tone}`"
        >
          <div class="dim__head">
            <el-icon class="dim__icon">
              <component :is="levelMeta(dim.level).icon" />
            </el-icon>
            <span class="dim__name">{{ dim.name }}</span>
            <el-tag
              size="small"
              effect="dark"
              :type="dim.level === 'match' ? 'success' : dim.level === 'mismatch' ? 'danger' : 'warning'"
              round
            >
              {{ levelMeta(dim.level).label }}
            </el-tag>
          </div>
          <div class="dim__summary">{{ dim.summary }}</div>
          <ul v-if="dim.evidence.length" class="dim__list">
            <li v-for="(item, i) in dim.evidence" :key="`e${i}`">{{ item }}</li>
          </ul>
          <div v-if="dim.gaps.length" class="dim__gaps">
            <span class="dim__gaps-label">缺口</span>
            <ul class="dim__list dim__list--gap">
              <li v-for="(item, i) in dim.gaps" :key="`g${i}`">{{ item }}</li>
            </ul>
          </div>
        </div>
      </div>
    </div>

    <!-- 亮点 -->
    <div v-if="report.strengths.length" class="match__section">
      <div class="match__section-title">
        <el-icon><Star /></el-icon>
        值得强调的亮点
      </div>
      <ul class="match__list">
        <li v-for="(item, i) in report.strengths" :key="i">{{ item }}</li>
      </ul>
    </div>

    <!-- 风险点 -->
    <div v-if="report.risks.length" class="match__section">
      <div class="match__section-title">
        <el-icon><Warning /></el-icon>
        风险点（面试官可能追问）
      </div>
      <div class="risk-list">
        <div v-for="(risk, i) in report.risks" :key="i" class="risk">
          <div class="risk__title">{{ risk.risk }}</div>
          <div v-if="risk.why" class="risk__why">原因：{{ risk.why }}</div>
          <div v-if="risk.how_to_handle" class="risk__how">
            <el-icon><MagicStick /></el-icon>
            {{ risk.how_to_handle }}
          </div>
        </div>
      </div>
    </div>

    <!-- STAR 面试题 -->
    <div v-if="report.questions.length" class="match__section">
      <div class="match__section-title">
        <el-icon><Notebook /></el-icon>
        STAR 面试题预测（{{ report.questions.length }} 道）
      </div>

      <div class="q-list">
        <div v-for="(item, index) in report.questions" :key="index" class="q">
          <button type="button" class="q__head" @click="toggleQuestion(index)">
            <span class="q__index">{{ index + 1 }}</span>
            <span class="q__text">{{ item.question }}</span>
            <el-tag v-if="item.category" size="small" effect="plain" type="info">
              {{ item.category }}
            </el-tag>
            <el-icon class="q__arrow" :class="{ 'is-open': expandedQuestions.has(index) }">
              <ArrowRight />
            </el-icon>
          </button>

          <div v-show="expandedQuestions.has(index)" class="q__body">
            <div v-if="item.focus" class="q__focus">
              <span class="q__focus-label">考察点</span>{{ item.focus }}
            </div>

            <div class="star">
              <div v-for="seg in STAR_LABELS" :key="seg.key" class="star__row">
                <span class="star__label">{{ seg.label }}</span>
                <span class="star__value">
                  {{ item.star[seg.key] || `（${seg.hint}）` }}
                </span>
              </div>
            </div>

            <div v-if="item.tips" class="q__tips">
              <el-icon><Pointer /></el-icon>
              {{ item.tips }}
            </div>

            <div class="q__actions">
              <el-button link size="small" type="primary" @click="copyQuestion(index)">
                <el-icon><DocumentCopy /></el-icon>
                复制这道题
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 后续建议 -->
    <div v-if="report.actions.length" class="match__section">
      <div class="match__section-title">
        <el-icon><List /></el-icon>
        接下来建议做
      </div>
      <ol class="match__list match__list--ordered">
        <li v-for="(item, i) in report.actions" :key="i">{{ item }}</li>
      </ol>
    </div>
  </div>
</template>

<style scoped>
.match {
  border: 1px solid var(--bzp-border);
  border-radius: 10px;
  background: #fff;
  margin: 4px 0 10px;
  overflow: hidden;
}

/* ---------- 头部 ---------- */
.match__head {
  display: flex;
  gap: 16px;
  padding: 14px 16px;
  align-items: flex-start;
  border-bottom: 1px solid var(--bzp-border);
}

.match__head--good {
  background: linear-gradient(135deg, #f0fbf4, #f7fdfa);
}
.match__head--medium {
  background: linear-gradient(135deg, #fffaf0, #fffdf8);
}
.match__head--poor {
  background: linear-gradient(135deg, #fff5f5, #fffafa);
}

.match__head--unknown {
  background: #f8fafd;
}

.match__score {
  display: flex;
  align-items: baseline;
  gap: 2px;
  flex-shrink: 0;
  padding-top: 2px;
}

.match__score-num {
  font-size: 34px;
  font-weight: 700;
  line-height: 1;
}

.match__head--good .match__score-num {
  color: var(--bzp-success);
}
.match__head--medium .match__score-num {
  color: #d99400;
}
.match__head--poor .match__score-num {
  color: var(--bzp-danger);
}

.match__score-unit {
  font-size: 13px;
  color: var(--bzp-text-secondary);
}

.match__score-na {
  font-size: 15px;
  font-weight: 600;
  color: var(--bzp-text-secondary);
}

.match__head-right {
  min-width: 0;
  flex: 1;
}

.match__head-title {
  font-size: 13.5px;
  font-weight: 600;
}

.match__verdict {
  margin-top: 4px;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--bzp-text);
}

.match__job {
  margin-top: 5px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}

.match__sources {
  margin-top: 5px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 11px;
  color: #9aa8bd;
}

.match__alert {
  border-radius: 0;
}

/* ---------- 通用区块 ---------- */
.match__section {
  padding: 12px 16px;
  border-bottom: 1px dashed var(--bzp-border);
}

.match__section:last-child {
  border-bottom: none;
}

.match__section-title {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--bzp-text-secondary);
  margin-bottom: 9px;
}

.match__list {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.85;
}

.match__list--ordered {
  padding-left: 20px;
}

/* ---------- 维度 ---------- */
.match__dims {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}

.dim {
  border: 1px solid var(--bzp-border);
  border-left-width: 3px;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fcfdfe;
}

.dim--match {
  border-left-color: var(--bzp-success);
}
.dim--partial {
  border-left-color: var(--bzp-warning);
}
.dim--mismatch {
  border-left-color: var(--bzp-danger);
}

.dim__head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dim__icon {
  font-size: 14px;
}

.dim--match .dim__icon {
  color: var(--bzp-success);
}
.dim--partial .dim__icon {
  color: var(--bzp-warning);
}
.dim--mismatch .dim__icon {
  color: var(--bzp-danger);
}

.dim__name {
  flex: 1;
  font-size: 12.5px;
  font-weight: 600;
}

.dim__summary {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--bzp-text);
}

.dim__list {
  margin: 6px 0 0;
  padding-left: 16px;
  font-size: 11.5px;
  line-height: 1.75;
  color: #5a6b85;
}

.dim__gaps {
  margin-top: 7px;
  padding-top: 6px;
  border-top: 1px dashed var(--bzp-border);
}

.dim__gaps-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--bzp-danger);
}

.dim__list--gap {
  color: #b04a4a;
}

/* ---------- 风险 ---------- */
.risk-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.risk {
  border: 1px solid #ffe0e0;
  background: #fffafa;
  border-radius: 8px;
  padding: 9px 11px;
}

.risk__title {
  font-size: 12.5px;
  font-weight: 600;
  color: #b04a4a;
}

.risk__why {
  margin-top: 4px;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--bzp-text-secondary);
}

.risk__how {
  margin-top: 5px;
  display: flex;
  gap: 5px;
  font-size: 11.5px;
  line-height: 1.7;
  color: #2b6b45;
}

/* ---------- 面试题 ---------- */
.q-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.q {
  border: 1px solid var(--bzp-border);
  border-radius: 8px;
  overflow: hidden;
}

.q__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 11px;
  background: #fbfcfe;
  border: none;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
}

.q__head:hover {
  background: #f4f7fc;
}

.q__index {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--bzp-primary);
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.q__text {
  flex: 1;
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1.6;
  color: var(--bzp-text);
}

.q__arrow {
  font-size: 12px;
  color: #9aa8bd;
  transition: transform 0.18s ease;
  flex-shrink: 0;
}

.q__arrow.is-open {
  transform: rotate(90deg);
}

.q__body {
  padding: 10px 12px;
  border-top: 1px dashed var(--bzp-border);
}

.q__focus {
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--bzp-text-secondary);
  margin-bottom: 8px;
}

.q__focus-label {
  display: inline-block;
  margin-right: 5px;
  padding: 0 5px;
  border-radius: 3px;
  background: #eef3ff;
  color: var(--bzp-primary);
  font-weight: 600;
}

.star {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.star__row {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.7;
}

.star__label {
  width: 56px;
  flex-shrink: 0;
  font-weight: 600;
  color: var(--bzp-primary);
}

.star__value {
  color: var(--bzp-text);
  min-width: 0;
}

.q__tips {
  margin-top: 8px;
  padding: 7px 9px;
  border-radius: 6px;
  background: #f6f9ff;
  font-size: 11.5px;
  line-height: 1.7;
  color: #2b4a80;
  display: flex;
  gap: 5px;
}

.q__actions {
  margin-top: 6px;
  text-align: right;
}
</style>
