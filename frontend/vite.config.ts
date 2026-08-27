import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import Components from 'unplugin-vue-components/vite'
import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'
import net from 'node:net'
import { uiMockPlugin } from './mock/server'

/** 端口动态避让：3000 被旧进程占用时自动改用 3001/3002...（避免看到旧页面） */
function isFreePort(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = net.createServer()
    srv.once('error', () => resolve(false))
    srv.once('listening', () => srv.close(() => resolve(true)))
    srv.listen(port, '0.0.0.0')
  })
}

async function pickFreePort(start: number): Promise<number> {
  for (let p = start; p < start + 20; p++) {
    if (await isFreePort(p)) {
      if (p !== start) console.log(`\n⚠️  端口 ${start} 已被占用，本次改用 ${p}（以本行地址为准，别看旧 tab）\n`)
      return p
    }
  }
  return start
}

export default defineConfig(async ({ mode }) => {
  // 只加载 VITE_* 前缀的环境变量（不读 CI 密钥等）
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const port = await pickFreePort(Number(process.env.PORT) || 3000)
  return {
    plugins: [
      tailwindcss(),
      vue(),
      // UI 预览模式（--only ui 生成，无后端）：dev 时用 mock API，admin/admin 可登录
      ...(env.VITE_USE_MOCK === 'true' ? [uiMockPlugin()] : []),
      Components({
        resolvers: [NaiveUiResolver()],
        dts: 'components.d.ts',
      }),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    css: {
      preprocessorOptions: {
        // modern-compiler：Dart Sass 2.0 将移除 legacy JS API（消除 Deprecation Warning）
        scss: { api: 'modern-compiler' },
      },
    },
    server: {
      host: '0.0.0.0',
      // 已在配置阶段探测出空闲端口——strictPort 防止再静默漂移
      port,
      strictPort: true,
      proxy: {
        '/api': {
          target: env.VITE_PROXY_TARGET || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            vue: ['vue', 'vue-router', 'pinia'],
            naive: ['naive-ui'],
          },
        },
      },
    },
  }
})
