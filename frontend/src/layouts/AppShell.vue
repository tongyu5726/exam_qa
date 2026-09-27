<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { NButton, NSelect } from 'naive-ui'
import { RouterLink, RouterView } from 'vue-router'
import { apiGet } from '@/api/client.js'
import { useCourseStore } from '@/stores/course.js'
import { useThemeStore } from '@/stores/theme.js'

const course = useCourseStore()
const theme = useThemeStore()
const health = ref('loading')
const error = ref('')
let timer
const links = [
  { to: '/chat', label: '对话' },
  { to: '/documents', label: '资料' },
  { to: '/question-bank', label: '我的题库' },
  { to: '/settings', label: '设置' },
]
const themeLabels = { system: '跟随系统', light: '亮色', dark: '暗色' }
function cycleTheme() {
  const options = ['system', 'light', 'dark']
  theme.setPreference(options[(options.indexOf(theme.preference) + 1) % options.length])
}
async function refreshHealth() {
  try { health.value = (await apiGet('/health')).status || 'unavailable' }
  catch { health.value = 'unavailable' }
}
onMounted(async () => {
  try { await course.load() } catch (cause) { error.value = cause.message || '课程目录加载失败' }
  await refreshHealth()
  timer = window.setInterval(refreshHealth, 15000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div class="app-shell">
    <header class="app-topbar">
      <RouterLink class="app-brand" to="/chat" aria-label="溯知 · 对话"><svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><rect x="1.5" y="1.5" width="29" height="29" rx="10" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.35"/><path fill="currentColor" d="M8 22c0-6.2 3.4-10.2 8.2-11.6.4-.1.8.3.7.7-.5 2.1-.3 3.8.7 5.2 1.4 1.9 3.9 2.8 6.4 2.1.4-.1.7.3.5.7C22.8 24.2 18.6 27 13.8 27 10.4 27 8 24.8 8 22Z"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M11.2 12.2c1.8-2.4 4.2-3.6 7-3.6 1.2 0 2.3.2 3.3.7"/><circle cx="21.5" cy="9.2" r="1.35" fill="currentColor"/></svg><span class="brand-word">溯知</span></RouterLink>
      <nav class="app-nav" aria-label="主导航">
        <RouterLink v-for="link in links" :key="link.to" :to="link.to">{{ link.label }}</RouterLink>
      </nav>
      <div class="app-controls">
        <NSelect :value="course.collegeId" :options="course.colleges.map((item) => ({ label: item.name, value: item.id }))" aria-label="学院" placeholder="学院" class="college-select" @update:value="course.setCollege" />
        <NSelect :value="course.currentId" :options="course.courses.map((item) => ({ label: item.name, value: item.id }))" aria-label="课程" placeholder="课程" class="course-select" @update:value="course.setCourse" />
        <span class="health-dot" :data-state="health" :title="`服务状态：${health}`" :aria-label="`服务状态：${health}`">●</span>
        <NButton size="small" quaternary :aria-label="`主题：${themeLabels[theme.preference]}`" @click="cycleTheme">{{ themeLabels[theme.preference] }}</NButton>
      </div>
    </header>
    <p v-if="error" class="shell-error" role="alert">{{ error }}</p>
    <main class="app-main"><RouterView /></main>
  </div>
</template>
