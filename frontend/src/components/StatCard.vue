<script setup lang="ts">
/**
 * 概览统计卡片。
 * 刻意只展示统计口径（累计/今日/进行中/授权状态），
 * 不含任何额度、套餐、计费类指标。
 */
import { computed } from 'vue'

interface Props {
  title: string
  value: number | string
  suffix?: string
  icon?: string
  tone?: 'primary' | 'success' | 'warning' | 'danger'
  hint?: string
  hintType?: 'success' | 'warning' | 'danger' | 'info'
  loading?: boolean
  clickable?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  suffix: '',
  icon: 'DataLine',
  tone: 'primary',
  hint: '',
  hintType: 'info',
  loading: false,
  clickable: false,
})

const emit = defineEmits<{ (event: 'click'): void }>()

const displayValue = computed(() => {
  if (typeof props.value === 'number') return props.value.toLocaleString('zh-CN')
  return props.value
})
</script>

<template>
  <div
    class="stat-card bzp-panel"
    :class="[`stat-card--${tone}`, { 'stat-card--clickable': clickable }]"
    @click="clickable && emit('click')"
  >
    <div class="stat-card__head">
      <span class="stat-card__title">{{ title }}</span>
      <div class="stat-card__icon">
        <el-icon><component :is="icon" /></el-icon>
      </div>
    </div>

    <div v-loading="loading" class="stat-card__value">
      <span class="stat-card__number">{{ displayValue }}</span>
      <span v-if="suffix" class="stat-card__suffix">{{ suffix }}</span>
    </div>

    <div class="stat-card__foot">
      <el-tag v-if="hint" :type="hintType" size="small" effect="light" round>
        {{ hint }}
      </el-tag>
      <span v-else class="bzp-muted">&nbsp;</span>
    </div>
  </div>
</template>

<style scoped>
.stat-card {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  overflow: hidden;
  transition: transform 0.16s ease, box-shadow 0.16s ease;
}

.stat-card--clickable {
  cursor: pointer;
}

.stat-card--clickable:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 22px rgba(16, 26, 44, 0.1);
}

.stat-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  border-radius: 3px 0 0 3px;
}

.stat-card--primary::before {
  background: var(--bzp-primary);
}
.stat-card--success::before {
  background: var(--bzp-success);
}
.stat-card--warning::before {
  background: var(--bzp-warning);
}
.stat-card--danger::before {
  background: var(--bzp-danger);
}

.stat-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.stat-card__title {
  font-size: 13px;
  color: var(--bzp-text-secondary);
}

.stat-card__icon {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  background: #f1f5fd;
  color: var(--bzp-primary);
}

.stat-card--success .stat-card__icon {
  background: #e9f9f2;
  color: var(--bzp-success);
}
.stat-card--warning .stat-card__icon {
  background: #fff5e5;
  color: var(--bzp-warning);
}
.stat-card--danger .stat-card__icon {
  background: #fdedef;
  color: var(--bzp-danger);
}

.stat-card__value {
  display: flex;
  align-items: baseline;
  gap: 5px;
  min-height: 42px;
}

.stat-card__number {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--bzp-text);
  line-height: 1.15;
}

.stat-card__suffix {
  font-size: 13px;
  color: var(--bzp-text-secondary);
}

.stat-card__foot {
  display: flex;
  align-items: center;
  min-height: 24px;
}
</style>
