<script setup lang="ts">
/**
 * 整体布局：左侧导航 + 顶部状态栏 + 主内容区。
 * 同时负责首次加载概览数据，并在全局展示免责声明条。
 */
import { onMounted, ref } from 'vue'
import AppSidebar from './AppSidebar.vue'
import AppHeader from './AppHeader.vue'
import { useWorkbenchStore } from '@/stores/workbench'

const store = useWorkbenchStore()
const disclaimerDismissed = ref(false)

onMounted(async () => {
  disclaimerDismissed.value = localStorage.getItem('bzp:disclaimer-dismissed') === '1'
  try {
    await Promise.all([store.loadDashboard(), store.loadQueue()])
  } catch {
    // 顶部状态栏会显示后端不可用，这里不额外打扰用户
  }
})

function dismissDisclaimer() {
  disclaimerDismissed.value = true
  localStorage.setItem('bzp:disclaimer-dismissed', '1')
}
</script>

<template>
  <div class="layout">
    <AppSidebar />
    <div class="layout__main">
      <AppHeader />

      <div v-if="!disclaimerDismissed && store.disclaimer" class="layout__disclaimer">
        <el-icon class="layout__disclaimer-icon"><Warning /></el-icon>
        <span class="layout__disclaimer-text">{{ store.disclaimer }}</span>
        <el-button link size="small" @click="dismissDisclaimer">不再提示</el-button>
      </div>

      <main class="layout__content">
        <RouterView v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </RouterView>
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  min-height: 100vh;
  background: var(--bzp-bg);
}

.layout__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.layout__disclaimer {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 24px 0;
  padding: 10px 16px;
  border-radius: 8px;
  background: #fff8e6;
  border: 1px solid #ffe2a8;
  color: #8a6100;
  font-size: 12.5px;
  line-height: 1.6;
}

.layout__disclaimer-icon {
  color: var(--bzp-warning);
  flex-shrink: 0;
}

.layout__disclaimer-text {
  flex: 1;
}

.layout__content {
  flex: 1;
  padding: 20px 24px 32px;
  min-width: 0;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.16s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
