/// <reference types="vitest/config" />
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// R10 §22：前端只调API，不能直接改权威状态。
// 开发期把 /api 代理到本地后端，避免 CORS 与硬编码地址。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.AP_BACKEND ?? 'http://127.0.0.1:8848',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.test.ts'],
  },
})
