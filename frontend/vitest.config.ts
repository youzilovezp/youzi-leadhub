import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/__tests__/**/*.{test,spec}.ts'],
    globals: true,
    // happy-dom 14+ 需要显式 setup 才能拿到 localStorage / window
    setupFiles: ['./src/__tests__/setup.ts'],
  },
})
