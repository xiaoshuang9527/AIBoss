import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import { createPinia } from 'pinia'

import 'element-plus/dist/index.css'
import '@/styles/main.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)

// 全量注册图标，模板里可直接 <el-icon><Search /></el-icon>
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn, size: 'default' })

app.config.errorHandler = (error, _instance, info) => {
  // 兜底：避免未捕获异常导致整页白屏
  console.error('[workbench] 未捕获异常', error, info)
}

app.mount('#app')
