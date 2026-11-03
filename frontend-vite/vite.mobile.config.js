import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'opscenter-mobile-entry',
      transformIndexHtml: {
        order: 'pre',
        handler(html) {
          return html
            .replace('/src/main.js', '/src/mobile/main.js')
            .replace('OpsCenter 运维工作台', 'OpsCenter 手机端')
            .replace('width=device-width, initial-scale=1.0', 'width=device-width, initial-scale=1, viewport-fit=cover')
        },
      },
    },
  ],
  base: './',
  build: {
    outDir: 'dist-mobile',
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(import.meta.dirname, 'index.html'),
    },
  },
})
