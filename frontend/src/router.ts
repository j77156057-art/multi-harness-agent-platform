// 路由承载"选中状态"，可深链：/g/:groupId 是项目群，/g/:groupId/t/:taskId 是任务线程。
// 三栏壳常驻（Shell.vue 同时挂在三条路由上，组件实例跨导航复用）。
import type { RouteRecordRaw } from 'vue-router'
import Shell from './components/Shell.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: Shell },
  { path: '/g/:groupId', name: 'group', component: Shell },
  { path: '/g/:groupId/t/:taskId', name: 'task', component: Shell },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]
