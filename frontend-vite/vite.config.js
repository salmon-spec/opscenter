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
  },
  server: {
    port: 5199,
    host: '0.0.0.0',
  },
})
