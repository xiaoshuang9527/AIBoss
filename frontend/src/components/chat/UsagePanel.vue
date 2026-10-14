<script setup lang="ts">
/**
 * 用量统计面板（token 维度）。
 *
 * 定位说明
 * --------
 * 这是**纯个人求职辅助工具**，本面板只回答"我用了多少 token、花在哪"，
 * **不做金额换算、不设预算、不出现额度/套餐/计费字眼**。
 *
 * 为什么值得有这个面板：实测推理模型的思考过程占了输出的绝大部分
 * （一次回答 68 个思考分片 vs 9 个正文分片），而两个模型的延迟差 8 倍。
 * 不把"花在哪"摊开看，就只能凭感觉猜。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { chatApi } from '@/api/chat'
import type { UsageReport } from '@/types/chat'

const loading = ref(false)
const report = ref<UsageReport | null>(null)
const days = ref(14)

async function load() {
  loading.value = true
  try {
    report.value = await chatApi.usage(days.value)
  } catch {
    ElMessage.error('加载用量统计失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

const totals = computed(() => report.value?.totals)

/** 千分位，长数字可读性差 */
function num(value: number | undefined): string {
  if (value === undefined || value === null) return '—'
  return value.toLocaleString('zh-CN')
}

function pct(value: number | undefined): string {
  if (!value) return '0%'
  return `${(value * 100).toFixed(1)}%`
}

/**
 * 用量趋势：手写 SVG 折线，不引入图表库。
 *
 * 理由：这个面板只需要一条折线，而 ECharts 会让这个懒加载 chunk 多几百 KB。
 * 已有的 ChartPanel 是给工作台的大图表用的，这里不必复用。
 */
const CHART_W = 640
const CHART_H = 140
const PAD = 8

const chart = computed(() => {
  const points = report.value?.daily ?? []
  if (points.length === 0) return null

  const max = Math.max(...points.map((p) => p.total_tokens), 1)
  const stepX =
    points.length > 1 ? (CHART_W - PAD * 2) / (points.length - 1) : 0

  const coords = points.map((point, index) => {
    const x = PAD + index * stepX
    const y = CHART_H - PAD - (point.total_tokens / max) * (CHART_H - PAD * 2)
    return { x, y, point }
  })

  const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
  const area =
    `${PAD},${CHART_H - PAD} ` +
    line +
    ` ${(PAD + (points.length - 1) * stepX).toFixed(1)},${CHART_H - PAD}`

  return { line, area, coords, max, width: CHART_W, height: CHART_H }
})

/** 模型用量占比条 */
const modelMax = computed(() =>
  Math.max(...(report.value?.by_model ?? []).map((m) => m.total_tokens), 1),
)

const TOOL_LABELS: Record<string, string> = {
  search_existing_jobs: '检索岗位库',
  get_job_detail: '读岗位 JD',
  trigger_boss_scraper: '发起抓取',
  check_task_status: '查任务进度',
  analyze_match: '匹配度诊断',
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

const MODE_LABELS: Record<string, string> = {
  job_hunting: '找岗位',
  interview_coach: '面试辅导',
  chat: '通用对话',
  unset: '自动判断',
}

function latency(ms: number): string {
  if (!ms) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}
</script>

<template>
  <div v-loading="loading" class="usage">
    <el-alert type="info" :closable="false" show-icon class="usage__note">
      <template #title>这里只统计 token 用量，不涉及任何计费</template>
      <template #default>
        本工具是纯个人求职辅助，没有额度、套餐或消费功能。这一页用来观察
        "token 花在哪里"，帮助你判断该换模型还是该精简提问。
      </template>
    </el-alert>

    <!-- 概览 -->
    <div v-if="totals" class="usage__cards">
      <div class="ucard">
        <div class="ucard__label">总 token</div>
        <div class="ucard__value">{{ num(totals.total_tokens) }}</div>
        <div class="ucard__sub">
          输入 {{ num(totals.prompt_tokens) }} · 输出 {{ num(totals.completion_tokens) }}
        </div>
      </div>
      <div class="ucard">
        <div class="ucard__label">对话 / 轮次</div>
        <div class="ucard__value">{{ num(totals.conversations) }}</div>
        <div class="ucard__sub">{{ num(totals.assistant_messages) }} 轮助手回复</div>
      </div>
      <div class="ucard">
        <div class="ucard__label">缓存命中率</div>
        <div class="ucard__value">{{ pct(totals.cache_hit_rate) }}</div>
        <div class="ucard__sub">{{ num(totals.cached_tokens) }} token 命中缓存</div>
      </div>
      <div class="ucard">
        <div class="ucard__label">思考占输出</div>
        <div class="ucard__value">{{ pct(totals.reasoning_share) }}</div>
        <div class="ucard__sub">{{ num(totals.reasoning_tokens) }} token 用于推理</div>
      </div>
    </div>

    <!-- 趋势 -->
    <div class="usage__section">
      <div class="usage__head">
        <span class="usage__title">每日用量</span>
        <el-radio-group v-model="days" size="small" @change="load">
          <el-radio-button :value="7">7 天</el-radio-button>
          <el-radio-button :value="14">14 天</el-radio-button>
          <el-radio-button :value="30">30 天</el-radio-button>
        </el-radio-group>
      </div>

      <div v-if="chart" class="chart">
        <svg
          :viewBox="`0 0 ${chart.width} ${chart.height}`"
          preserveAspectRatio="none"
          class="chart__svg"
        >
          <polygon :points="chart.area" class="chart__area" />
          <polyline :points="chart.line" class="chart__line" />
          <circle
            v-for="(c, i) in chart.coords"
            :key="i"
            :cx="c.x"
            :cy="c.y"
            r="2.5"
            class="chart__dot"
          />
        </svg>
        <div class="chart__axis">
          <span>{{ report?.daily[0]?.date }}</span>
          <span>峰值 {{ num(chart.max) }} token</span>
          <span>{{ report?.daily[report.daily.length - 1]?.date }}</span>
        </div>
      </div>
      <el-empty v-else description="还没有用量数据" :image-size="60" />
    </div>

    <!-- 按模型 -->
    <div class="usage__section">
      <div class="usage__title">按模型</div>
      <div v-if="report?.by_model.length" class="models">
        <div v-for="item in report.by_model" :key="item.model_label" class="model">
          <div class="model__row">
            <span class="model__name">{{ item.model_label }}</span>
            <span class="model__tokens">{{ num(item.total_tokens) }} token</span>
            <span class="model__meta">
              {{ item.messages }} 轮 · 平均 {{ latency(item.avg_latency_ms) }}
            </span>
          </div>
          <div class="model__bar">
            <div
              class="model__bar-fill"
              :style="{ width: `${(item.total_tokens / modelMax) * 100}%` }"
            />
          </div>
        </div>
      </div>
      <el-empty v-else description="暂无数据" :image-size="50" />
    </div>

    <!-- 工具调用 -->
    <div class="usage__section">
      <div class="usage__title">工具调用次数</div>
      <div v-if="report?.tools.length" class="tools">
        <el-tag
          v-for="item in report.tools"
          :key="item.name"
          size="default"
          effect="plain"
          class="tools__tag"
        >
          {{ toolLabel(item.name) }}
          <b>{{ item.calls }}</b>
        </el-tag>
      </div>
      <el-empty v-else description="暂无工具调用" :image-size="50" />
    </div>

    <!-- 用量最高的对话 -->
    <div class="usage__section">
      <div class="usage__title">用量最高的对话</div>
      <el-table
        v-if="report?.top_conversations.length"
        :data="report.top_conversations"
        size="small"
        class="usage__table"
      >
        <el-table-column prop="title" label="对话" min-width="200" show-overflow-tooltip />
        <el-table-column label="模式" width="100">
          <template #default="{ row }">
            {{ MODE_LABELS[row.mode] ?? row.mode }}
          </template>
        </el-table-column>
        <el-table-column prop="messages" label="轮次" width="70" align="right" />
        <el-table-column label="输入 token" width="110" align="right">
          <template #default="{ row }">{{ num(row.prompt_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出 token" width="110" align="right">
          <template #default="{ row }">{{ num(row.completion_tokens) }}</template>
        </el-table-column>
        <el-table-column label="合计" width="110" align="right">
          <template #default="{ row }">
            <b>{{ num(row.total_tokens) }}</b>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无数据" :image-size="50" />
    </div>
  </div>
</template>

<style scoped>
.usage {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.usage__note {
  border-radius: 8px;
}

/* ---------- 概览卡 ---------- */
.usage__cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.ucard {
  border: 1px solid var(--bzp-border);
  border-radius: 9px;
  padding: 12px 14px;
  background: var(--bzp-panel);
}

.ucard__label {
  font-size: 12px;
  color: var(--bzp-text-secondary);
}

.ucard__value {
  margin-top: 5px;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--bzp-text);
}

.ucard__sub {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}

/* ---------- 区块 ---------- */
.usage__section {
  border: 1px solid var(--bzp-border);
  border-radius: 9px;
  padding: 14px 16px;
  background: var(--bzp-panel);
}

.usage__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.usage__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--bzp-text);
}

.usage__head .usage__title {
  margin-bottom: 0;
}

.usage__section > .usage__title {
  margin-bottom: 10px;
}

/* ---------- 折线图 ---------- */
.chart {
  width: 100%;
}

.chart__svg {
  width: 100%;
  height: 140px;
  display: block;
}

.chart__area {
  fill: rgba(47, 107, 255, 0.1);
  stroke: none;
}

.chart__line {
  fill: none;
  stroke: var(--bzp-primary);
  stroke-width: 1.8;
  vector-effect: non-scaling-stroke;
}

.chart__dot {
  fill: var(--bzp-primary);
}

.chart__axis {
  margin-top: 6px;
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--bzp-text-secondary);
}

/* ---------- 模型 ---------- */
.models {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.model__row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12.5px;
}

.model__name {
  font-weight: 600;
}

.model__tokens {
  color: var(--bzp-primary);
  font-weight: 600;
}

.model__meta {
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}

.model__bar {
  margin-top: 5px;
  height: 6px;
  border-radius: 3px;
  background: #eef2f8;
  overflow: hidden;
}

.model__bar-fill {
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, #4a8bff, #7fb0ff);
}

/* ---------- 工具 ---------- */
.tools {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tools__tag b {
  margin-left: 5px;
  color: var(--bzp-primary);
}

.usage__table {
  width: 100%;
}
</style>
