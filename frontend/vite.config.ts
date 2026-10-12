import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

//: 本项目的后端默认端口。
//:
//: ⚠️ 这里刻意不用 8000：8000 是"约定俗成"的端口，在很多机器上已被别的
//: 应用占用。一旦被占用，代理会把请求发给那个应用，返回 404「资源不存在」——
//: 现象上非常像"后端没启动"，实际后端是好的，排查方向会被完全带偏。
const DEFAULT_BACKEND_PORT = 18080

export default defineConfig(({ mode }) => {
  // ⚠️ 必须用 loadEnv 显式加载，不能读 process.env。
  // Vite **不会**把 .env.local 的内容注入 process.env，只有通过 loadEnv
  // 才能拿到。用 process.env 会导致配置文件里的设置被静默忽略，
  // 退回默认值（这正是之前代理指向错误端口的原因）。
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || `http://127.0.0.1:${DEFAULT_BACKEND_PORT}`

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        // WebSocket 要放在 /api 之前。
        // Vite 的代理规则按声明顺序匹配，'/api' 是 '/api/ws' 的前缀，
        // 若 '/api' 在前，ws 升级请求会先被它吃掉，导致连接失败。
        '/api/ws': {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
        },
        // REST 接口
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      chunkSizeWarningLimit: 1500,
      rollupOptions: {
        output: {
          manualChunks: {
            vue: ['vue', 'vue-router', 'pinia'],
            element: ['element-plus', '@element-plus/icons-vue'],
            charts: ['echarts'],
          },
        },
      },
    },
  }
})
