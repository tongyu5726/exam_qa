import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', name: 'chat', component: () => import('@/features/chat/ChatPage.vue'), meta: { title: '对话' } },
  { path: '/documents', name: 'documents', component: () => import('@/features/documents/DocumentsPage.vue'), meta: { title: '资料' } },
  { path: '/question-bank', name: 'question-bank', component: () => import('@/features/question-bank/QuestionBankPage.vue'), meta: { title: '我的题库' } },
  { path: '/settings', name: 'settings', component: () => import('@/features/settings/SettingsPage.vue'), meta: { title: '设置' } },
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({ history: createWebHashHistory('/sz/'), routes })
router.afterEach((to) => { document.title = `${to.meta.title || '溯知'} · 溯知` })
export default router
