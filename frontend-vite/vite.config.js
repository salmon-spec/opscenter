import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// v3.28 F1: 构建产物输出到 dist/，供 Caddy /v3/ 灰度路径使用
export default defineConfig({
  plugins: [vue()],
  base: '/v3/',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    // v3.29: 拆分大体积依赖为独立 chunk，利于缓存与加载
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router'],
          'vendor-echarts': ['echarts'],
          'vendor-xterm': ['@xterm/xterm', '@xterm/addon-fit'],
        },
      },
    },
  },
  server: {
    port: 5199,
    host: '0.0.0.0',
    // v3.29: 开发期代理到后端（免登录模式直连 10.66.66.5:9091）
    proxy: {
      '/api': { target: 'http://10.66.66.5:9091', changeOrigin: true },
      '/ws': { target: 'ws://10.66.66.5:9091', ws: true, changeOrigin: true },
    },
  },
})
