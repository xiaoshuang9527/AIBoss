<script setup lang="ts">
/**
 * 岗位卡片列表（工具结果的结构化渲染）。
 *
 * 这是「模式一」的核心交互：AI 检索到岗位后，界面以可点击的卡片展示，
 * 每条都能一键跳到 BOSS 直聘原页面。
 *
 * 为什么不用 markdown 渲染岗位列表
 * --------------------------------
 * 纯文本无法承载可点击的跳转按钮，而且模型复述字段容易出错（编造链接、漏字段）。
 * 结构化卡片的数据直接来自数据库，链接是爬虫抓到的真实 URL。
 */
import type { JobCard } from '@/types/chat'

const props = defineProps<{
  jobs: JobCard[]
  total?: number
  query?: Record<string, unknown>
}>()

function goBoss(job: JobCard) {
  if (!job.job_link) return
  window.open(job.job_link, '_blank', 'noopener,noreferrer')
}

function salaryTone(job: JobCard): 'danger' | 'warning' | 'info' {
  const avg = job.salary_avg
  if (avg === null || avg === undefined) return 'info'
  if (avg >= 30) return 'danger'
  if (avg >= 15) return 'warning'
  return 'info'
}

/** 结果多于返回条数时提示用户 */
function truncated(): boolean {
  return Boolean(props.total && props.jobs.length && props.total > props.jobs.length)
}
</script>

<template>
  <div class="job-cards">
    <div class="job-cards__head">
      <el-icon><Briefcase /></el-icon>
      <span class="job-cards__title">
        检索到 {{ total ?? jobs.length }} 条岗位
        <template v-if="jobs.length && total && total > jobs.length">
          （展示前 {{ jobs.length }} 条）
        </template>
      </span>
    </div>

    <div v-if="jobs.length" class="job-cards__list">
      <article v-for="job in jobs" :key="job.id" class="job-card">
        <div class="job-card__main">
          <div class="job-card__left">
            <div class="job-card__title-row">
              <span class="job-card__title" :title="job.title">{{ job.title }}</span>
              <el-tag v-if="job.is_favorite" type="warning" size="small" effect="light" round>
                已收藏
              </el-tag>
            </div>
            <div class="job-card__company">{{ job.company }}</div>
            <div class="job-card__meta">
              <span v-if="job.city"><el-icon><Location /></el-icon>{{ job.location || job.city }}</span>
              <span v-if="job.experience"><el-icon><Suitcase /></el-icon>{{ job.experience }}</span>
              <span v-if="job.degree"><el-icon><Reading /></el-icon>{{ job.degree }}</span>
              <span v-if="job.boss_active_status" class="job-card__active">
                {{ job.boss_active_status }}
              </span>
            </div>
            <div v-if="job.skills?.length" class="job-card__skills">
              <el-tag
                v-for="skill in job.skills.slice(0, 6)"
                :key="skill"
                size="small"
                effect="plain"
              >
                {{ skill }}
              </el-tag>
            </div>
          </div>

          <div class="job-card__right">
            <div class="job-card__salary" :class="`job-card__salary--${salaryTone(job)}`">
              {{ job.salary }}
            </div>
            <el-button type="primary" size="small" :disabled="!job.job_link" @click="goBoss(job)">
              <el-icon><ChatDotRound /></el-icon>
              去 BOSS 沟通
            </el-button>
          </div>
        </div>

        <div v-if="job.jd_preview" class="job-card__jd">{{ job.jd_preview }}…</div>
      </article>
    </div>

    <el-empty v-else description="本地岗位库没有匹配结果" :image-size="60">
      <div class="job-cards__hint">
        可以让 AI 换关键词再查，或者发起一次 BOSS 抓取任务获取新岗位。
      </div>
    </el-empty>

    <div v-if="truncated()" class="job-cards__more">
      还有 {{ (total ?? 0) - jobs.length }} 条未展示，可以缩小筛选范围或让我换关键词再查。
    </div>
  </div>
</template>

<style scoped>
.job-cards {
  margin: 4px 0 10px;
}

.job-cards__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
  margin-bottom: 10px;
}

.job-cards__title {
  font-weight: 500;
}

.job-cards__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.job-card {
  border: 1px solid var(--bzp-border);
  border-radius: 9px;
  padding: 12px 14px;
  background: #fff;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.job-card:hover {
  border-color: #c9d9ff;
  box-shadow: 0 4px 14px rgba(47, 107, 255, 0.08);
}

.job-card__main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.job-card__left {
  min-width: 0;
  flex: 1;
}

.job-card__title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.job-card__title {
  font-size: 13.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-card__company {
  margin-top: 3px;
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
}

.job-card__meta {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
}

.job-card__meta span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.job-card__active {
  color: var(--bzp-success);
}

.job-card__skills {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.job-card__right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  flex-shrink: 0;
}

.job-card__salary {
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
}

.job-card__salary--danger {
  color: var(--bzp-danger);
}
.job-card__salary--warning {
  color: #e08b00;
}
.job-card__salary--info {
  color: var(--bzp-text-secondary);
}

.job-card__jd {
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px dashed var(--bzp-border);
  font-size: 12px;
  line-height: 1.7;
  color: #5a6b85;
}

.job-cards__hint,
.job-cards__more {
  font-size: 12px;
  color: var(--bzp-text-secondary);
  line-height: 1.7;
}

.job-cards__more {
  margin-top: 10px;
  padding: 7px 10px;
  border-radius: 7px;
  background: #f5f8fd;
}
</style>
