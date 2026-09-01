import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles.css'
import '@xterm/xterm/css/xterm.css'
import packageInfo from '../package.json'

document.title = `OpsCenter ${packageInfo.version} 资源控制台`

// v3.29：统一运维工作台入口（hash 路由，视图内切换）
createApp(App).use(router).mount('#app')
