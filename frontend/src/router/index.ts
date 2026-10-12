import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

/**
 * 路由表与左侧导航一一对应。
 * 全部使用懒加载，首屏只加载工作台。
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('@/views/DashboardView.vue'),
    meta: { title: '工作台', icon: 'Odometer' },
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: 'AI 对话', icon: 'ChatDotRound' },
  },
  {
    path: '/auth',
    name: 'auth',
    component: () => import('@/views/AuthView.vue'),
    meta: { title: '账号授权', icon: 'Key' },
  },
  {
    path: '/tasks',
    name: 'create-task',
    component: () => import('@/views/CreateTaskView.vue'),
    meta: { title: '创建抓取任务', icon: 'Promotion' },
  },
  {
    path: '/jobs',
    name: 'jobs',
    component: () => import('@/views/JobsView.vue'),
    meta: { title: '岗位结果', icon: 'Briefcase' },
  },
  {
    path: '/history',
    name: 'history',
    component: () => import('@/views/HistoryView.vue'),
    meta: { title: '历史记录', icon: 'Clock' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: '系统设置', icon: 'Setting' },
  },
  {
    path: '/disclaimer',
    name: 'disclaimer',
    component: () => import('@/views/DisclaimerView.vue'),
    meta: { title: '免责声明与合规', icon: 'Warning', hidden: true },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const title = (to.meta?.title as string) || ''
  document.title = title ? `${title} · 智能求职辅助工作台` : '智能求职辅助工作台'
})

export default router
