<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import { apiGet, apiPost } from '@/api/client.js'
import { useCourseStore } from '@/stores/course.js'
import GenerationForm from './GenerationForm.vue'
import QuestionList from './QuestionList.vue'
import PaperForm from './PaperForm.vue'
import BlueprintForm from './BlueprintForm.vue'

const course = useCourseStore()
const route = useRoute()
const message = useMessage()
const questions = ref([])
const papers = ref([])
const selected = ref([])
const loading = ref(false)
const busy = ref('')
const error = ref('')
const paperForm = ref(null)
let controller

async function load(courseId) {
  controller?.abort()
  controller = new AbortController()
  questions.value = []; papers.value = []; selected.value = []
  if (!courseId) return
  loading.value = true; error.value = ''
  try {
    const [items, saved] = await Promise.all([
      apiGet('/question-bank/questions', { course_id: courseId }, controller.signal),
      apiGet('/question-bank/papers', { course_id: courseId }, controller.signal),
    ])
    if (course.currentId !== courseId) return
    questions.value = items; papers.value = saved
  } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message }
  finally { if (course.currentId === courseId) loading.value = false }
}
watch(() => course.currentId, load, { immediate: true })
onUnmounted(() => controller?.abort())
function toggle(id, checked) { selected.value = checked ? [...selected.value, id] : selected.value.filter((item) => item !== id) }
async function generate(payload) {
  const courseId = course.currentId
  if (!courseId) return
  busy.value = 'generate'
  try {
    const result = await apiPost('/question-bank/generate', { course_id: courseId, ...payload })
    if (!result.grounded) message.warning('资料不足，未生成或保存题目')
    else { message.success(`已保存 ${result.questions.length} 道题目草稿`); if (course.currentId === courseId) await load(courseId) }
  } catch (cause) { message.error(cause.message) }
  finally { busy.value = '' }
}
async function savePaper(payload) {
  const courseId = course.currentId
  if (!courseId || !selected.value.length) return
  busy.value = 'paper'
  try {
    await apiPost('/question-bank/papers', { course_id: courseId, ...payload, items: selected.value.map((question_id) => ({ question_id, score: 1 })) })
    if (course.currentId === courseId) { paperForm.value?.reset(); message.success('试卷已保存'); await load(courseId) }
  } catch (cause) { message.error(cause.message) }
  finally { busy.value = '' }
}
async function assemble(payload) {
  const courseId = course.currentId
  if (!courseId) return
  busy.value = 'assemble'
  try {
    const result = await apiPost('/question-bank/papers/assemble', { course_id: courseId, ...payload })
    message.success(`已保存：复用 ${result.reused_count} 题，补生成 ${result.generated_count} 题，共 ${result.total_score} 分`)
    if (course.currentId === courseId) await load(courseId)
  } catch (cause) { message.error(cause.message) }
  finally { busy.value = '' }
}
</script>

<template>
  <div class="page bank-page">
    <header class="page-header"><p class="eyebrow">MY QUESTION BANK</p><h1>我的题库</h1><p>先取证，再出题。题目与试卷按当前课程隔离，每一道题都应有所依据。</p></header>
    <div v-if="route.query.topic" class="panel routed-topic">来自问答的主题：{{ route.query.topic }} <NButton text type="primary" @click="$router.replace({ name: 'question-bank' })">关闭提示</NButton></div>
    <p v-if="error" role="alert" class="error-text">{{ error }} <NButton text @click="load(course.currentId)">重试</NButton></p>
    <div class="bank-grid">
      <section class="panel"><h2>按资料出题</h2><GenerationForm :busy="busy === 'generate'" :initial-topic="String(route.query.topic || '')" @submit="generate" /></section>
      <section class="panel"><h2>保存试卷</h2><PaperForm ref="paperForm" :busy="busy === 'paper'" :selected-count="selected.length" @submit="savePaper" /></section>
    </div>
    <section class="panel"><p class="eyebrow section-eyebrow">CONTROLLED ASSEMBLY</p><h2>按蓝图自动组卷</h2><BlueprintForm :busy="busy === 'assemble'" @submit="assemble" /></section>
    <section class="panel"><div class="section-head"><h2>题目草稿</h2><span class="muted">{{ loading ? '读取中…' : `${questions.length} 道题` }}</span></div><QuestionList :questions="questions" :selected="selected" @toggle="toggle" /></section>
    <section class="panel"><div class="section-head"><h2>已保存试卷</h2><span class="muted">{{ papers.length }} 份</span></div><p v-if="!papers.length" class="empty">尚未保存试卷</p><div v-else class="paper-list"><article v-for="paper in papers" :key="paper.id" class="paper-item"><h3>{{ paper.title }}</h3><p>{{ paper.question_count }} 道题 · {{ paper.total_score }} 分</p><p v-if="paper.description" class="muted">{{ paper.description }}</p></article></div></section>
  </div>
</template>

<style scoped>
.bank-page { display: grid; gap: 18px; width: min(1120px, 100% - 52px); margin: 46px auto 80px; }
.bank-page .page-header { max-width: 720px; margin-bottom: 10px; }
.bank-page .page-header h1 { margin: 6px 0 4px; font-family: var(--sz-font-brand); font-size: clamp(36px, 5vw, 56px); font-weight: 500; line-height: 1.3; letter-spacing: .08em; }
.bank-page .page-header p:last-child { line-height: 1.8; }
.bank-page :deep(.panel) { padding: 22px; background: color-mix(in srgb, var(--sz-bg-panel) 84%, var(--sz-bg-base)); border-radius: 14px; }
.bank-page :deep(.panel h2) { font-size: 17px; margin-bottom: 15px; }
.section-eyebrow { margin-bottom: 7px; }
.bank-page :deep(label) { display: grid; gap: 6px; color: var(--sz-text-muted); font-size: 14px; }
.bank-page :deep(.form-row label) { min-width: 0; }
.bank-page :deep(.n-input), .bank-page :deep(.n-base-selection), .bank-page :deep(.n-input-number) { border-radius: 8px; }
.bank-page :deep(.n-button) { border-radius: 8px; }
.bank-page :deep(.empty) { background: transparent; text-align: left; padding: 10px 0; }
.bank-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.routed-topic { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 12px 20px; }
.question-list, .paper-list { display: grid; gap: 10px; }
.question-item { display: flex; align-items: start; gap: 12px; padding: 16px; border: 1px solid var(--sz-border); border-radius: 10px; }
.question-item h3, .paper-item h3 { margin: 0 0 8px; font-size: 16px; }
.question-item p, .paper-item p { margin: 5px 0; }
.question-tags { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 10px; }
.paper-item { padding: 14px 0; border-top: 1px solid var(--sz-border); }
@media (max-width: 720px) { .bank-grid { grid-template-columns: 1fr; }.bank-page { width: min(100% - 32px, 1120px); margin-top: 28px; } }
</style>
