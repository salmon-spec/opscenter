import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles.css'
import '@xterm/xterm/css/xterm.css'

// v3.29：统一运维工作台入口（hash 路由，视图内切换）
createApp(App).use(router).mount('#app')
