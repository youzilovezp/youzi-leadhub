// 应用入口
import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
// Tailwind 入口必须独立直连——经 SCSS @import 内联后 @import "tailwindcss" 会失效
import './styles/tailwind.css'
import './styles/index.scss'

const app = createApp(App)

// 全局错误兜底：未捕获的组件异常不打断应用
app.config.errorHandler = (err, _instance, info) => {
   
  console.error(`[全局错误] ${info}:`, err)
}

app.use(createPinia())
app.use(router)

app.mount('#app')
