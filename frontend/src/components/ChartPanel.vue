<script setup lang="ts">
/**
 * ECharts 通用封装：负责实例生命周期、尺寸自适应与数据更新。
 */
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

interface Props {
  option: echarts.EChartsOption
  height?: string
  loading?: boolean
  empty?: boolean
  emptyText?: string
}

const props = withDefaults(defineProps<Props>(), {
  height: '260px',
  loading: false,
  empty: false,
  emptyText: '暂无数据',
})

const container = ref<HTMLDivElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)
let observer: ResizeObserver | null = null

function render() {
  if (!chart.value || props.empty) return
  chart.value.setOption(props.option, true)
}

onMounted(() => {
  if (!container.value) return
  chart.value = echarts.init(container.value)
  render()
  observer = new ResizeObserver(() => chart.value?.resize())
  observer.observe(container.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  chart.value?.dispose()
  chart.value = null
})

watch(() => props.option, render, { deep: true })
watch(
  () => props.empty,
  (value) => {
    if (!value) {
      // 从空态切回来时需要等 DOM 渲染完再 setOption
      requestAnimationFrame(render)
    }
  },
)
</script>

<template>
  <div class="chart">
    <el-empty v-if="empty" :description="emptyText" :image-size="72" />
    <div v-else v-loading="loading" ref="container" :style="{ height, width: '100%' }" />
  </div>
</template>

<style scoped>
.chart {
  width: 100%;
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
