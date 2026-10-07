import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import { routes } from './router'
import './style.css'

createApp(App)
  .use(createPinia())
  .use(createRouter({ history: createWebHistory(), routes }))
  .mount('#app')
