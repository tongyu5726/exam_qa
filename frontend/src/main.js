import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router/index.js'
import './styles/fonts.css'
import './styles/tokens.css'
import './styles/app.css'
import 'katex/dist/katex.min.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
