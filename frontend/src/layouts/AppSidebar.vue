<script setup lang="ts">
/**
 * 左侧导航栏。
 * 导航项直接由路由表推导，新增页面只要加一条路由即可。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWorkbenchStore } from '@/stores/workbench'

const route = useRoute()
const router = useRouter()
const store = useWorkbenchStore()

const navItems = computed(() =>
  router
    .getRoutes()
    .filter((item) => item.meta?.title && !item.meta?.hidden)
    .map((item) => ({
      path: item.path,
      title: item.meta?.title as string,
      icon: (item.meta?.icon as string) || 'Menu',
    })),
)

const activeTasks = computed(() => store.cards.active_tasks)

const activePath = computed(() => route.path)
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar__brand">
      <div class="sidebar__logo">求</div>
      <div class="sidebar__brand-text">
        <div class="sidebar__brand-title">求职辅助工作台</div>
        <div class="sidebar__brand-sub">Job Scraper Console</div>
      </div>
    </div>

    <nav class="sidebar__nav">
      <RouterLink
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="sidebar__item"
        :class="{ 'sidebar__item--active': activePath === item.path }"
      >
        <el-icon class="sidebar__icon"><component :is="item.icon" /></el-icon>
        <span class="sidebar__label">{{ item.title }}</span>
        <el-badge
          v-if="item.path === '/history' && activeTasks > 0"
          :value="activeTasks"
          type="warning"
          class="sidebar__badge"
        />
      </RouterLink>
    </nav>

    <div class="sidebar__footer">
      <RouterLink to="/disclaimer" class="sidebar__footer-link">
        <el-icon><Warning /></el-icon>
        <span>免责声明与合规</span>
      </RouterLink>
      <div class="sidebar__footer-note">
        仅供个人学习与求职分析，非商业工具，无任何计费功能。
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 232px;
  min-width: 232px;
  height: 100vh;
  background: var(--bzp-sidebar);
  color: #cbd6e6;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
}

.sidebar__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 18px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}

.sidebar__logo {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: linear-gradient(135deg, #3b7bff, #6aa4ff);
  color: #fff;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.sidebar__brand-title {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  line-height: 1.3;
}

.sidebar__brand-sub {
  font-size: 11px;
  color: #7d8ca6;
  letter-spacing: 0.4px;
}

.sidebar__nav {
  flex: 1;
  padding: 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
}

.sidebar__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: #b6c2d6;
  text-decoration: none;
  font-size: 13.5px;
  transition: background 0.15s ease, color 0.15s ease;
}

.sidebar__item:hover {
  background: var(--bzp-sidebar-hover);
  color: #fff;
}

.sidebar__item--active {
  background: linear-gradient(90deg, rgba(47, 107, 255, 0.9), rgba(47, 107, 255, 0.65));
  color: #fff;
  font-weight: 600;
  box-shadow: 0 4px 14px rgba(47, 107, 255, 0.28);
}

.sidebar__icon {
  font-size: 16px;
}

.sidebar__label {
  flex: 1;
}

.sidebar__badge {
  margin-right: 2px;
}

.sidebar__footer {
  padding: 14px 16px 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
}

.sidebar__footer-link {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #f0b429;
  font-size: 12.5px;
  text-decoration: none;
}

.sidebar__footer-link:hover {
  text-decoration: underline;
}

.sidebar__footer-note {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.6;
  color: #6d7c95;
}

@media (max-width: 900px) {
  .sidebar {
    width: 68px;
    min-width: 68px;
  }
  .sidebar__brand-text,
  .sidebar__label,
  .sidebar__footer-note {
    display: none;
  }
  .sidebar__item {
    justify-content: center;
  }
}
</style>
