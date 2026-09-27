<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { NButton, useDialog, useMessage } from 'naive-ui'
import { RouterLink } from 'vue-router'
import { apiGet, apiDelete } from '@/api/client.js'
import { useCourseStore } from '@/stores/course.js'
import UploadPanel from './UploadPanel.vue'
import DocumentList from './DocumentList.vue'
import DocumentSummary from './DocumentSummary.vue'

const course = useCourseStore()
const dialog = useDialog()
const message = useMessage()
const docs = ref([])
const embedding = ref({})
const summary = ref({})
const by = ref('type')
const loading = ref(false)
const error = ref('')
const busyId = ref('')
let controller
let summaryController
const chunks = computed(() => docs.value.reduce((total, item) => total + (Number(item.chunk_count) || 0), 0))

async function load(courseId) {
  controller?.abort()
  controller = new AbortController()
  docs.value = []; embedding.value = {}; error.value = ''
  if (!courseId) return
  loading.value = true
  try {
    const data = await apiGet('/documents', { course_id: courseId }, controller.signal)
    if (course.currentId !== courseId) return
    docs.value = data.items || []
    embedding.value = data.embedding || {}
  } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message }
  finally { if (course.currentId === courseId) loading.value = false }
}
async function loadSummary(courseId, dimension) {
  summaryController?.abort()
  summaryController = new AbortController()
  summary.value = {}
  if (!courseId) return
  try {
    const data = await apiGet('/documents/summary', { course_id: courseId, by: dimension }, summaryController.signal)
    if (course.currentId === courseId && by.value === dimension) summary.value = data
  } catch (cause) { if (cause.name !== 'AbortError') message.error(cause.message) }
}
function refresh() { load(course.currentId); loadSummary(course.currentId, by.value) }
function remove(doc) {
  const courseId = course.currentId
  dialog.warning({ title: '删除资料', content: `确定删除「${doc.filename || doc.id}」及其已入库内容吗？`, positiveText: '删除', negativeText: '取消', onPositiveClick: async () => {
    busyId.value = doc.id
    try { await apiDelete(`/documents/${encodeURIComponent(doc.id)}`, { course_id: courseId }); message.success('已删除'); if (course.currentId === courseId) refresh() }
    catch (cause) { message.error(cause.message) }
    finally { busyId.value = '' }
  } })
}
watch(() => course.currentId, refresh, { immediate: true })
watch(by, () => loadSummary(course.currentId, by.value))
onUnmounted(() => { controller?.abort(); summaryController?.abort() })
</script>

<template>
  <div class="documents-page">
    <aside class="docs-rail docs-rail-left" aria-label="资料说明">
      <div class="rail-card rail-quote"><p class="rail-epigraph">「博学之，审问之，慎思之，明辨之，笃行之。」</p><p class="rail-cite">— 《中庸》</p></div>
      <div class="rail-card"><h3>据源而入</h3><p>讲义、笔记、真题化作可检索之块。对话时按课程隔离召回，答有所据。</p></div>
      <div class="rail-card"><h3>入库三步</h3><ol><li>解析原文</li><li>语义分块</li><li>向量落库</li></ol></div>
      <div class="rail-card"><h3>格式</h3><div class="format-tags"><span v-for="format in ['PDF', 'TXT', 'MD', 'DOC', 'DOCX', 'PPTX']" :key="format">{{ format }}</span></div><p class="rail-note">PPT 按页分块；扫描版 PDF 可开 OCR。章节概览需「强制重建」或重新上传以写入章节元数据。</p></div>
      <div class="rail-card"><h3>守则</h3><p>一课一库，互不串味。换 Embedding 维度后需重新入库；普通扫描不改未变更文件。</p></div>
    </aside>
    <section class="docs-center">
      <header class="docs-hero"><p class="eyebrow">资料库</p><h1>课程资料</h1><p>上传或扫描本地目录，按当前课程隔离入库。旧资料要补章节元数据时，勾选「强制重建」再扫描。</p></header>
      <UploadPanel :course-id="course.currentId" @completed="refresh" />
      <p v-if="error" role="alert" class="error-text">{{ error }} <NButton text @click="refresh">重试</NButton></p>
      <p v-if="loading" role="status" class="muted">正在读取课程资料…</p>
      <DocumentList :documents="docs" :course-id="course.currentId" :busy-id="busyId" @remove="remove" />
      <DocumentSummary v-model:by="by" :summary="summary" />
    </section>
    <aside class="docs-rail docs-rail-right" aria-label="资料库状态">
      <div class="rail-card"><h3>向量维度</h3><p class="rail-value">{{ embedding.dim || '—' }}</p><p>取自当前课程的向量集合</p></div>
      <div class="rail-card"><h3>Embedding</h3><p class="rail-model">{{ embedding.model || '—' }}</p><p>{{ embedding.provider || '等待模型配置' }}</p></div>
      <div class="rail-card"><h3>本课概况</h3><dl class="rail-stats"><div><dt>资料</dt><dd>{{ docs.length }}</dd></div><div><dt>分块</dt><dd>{{ chunks }}</dd></div><div><dt>已就绪</dt><dd>{{ docs.filter((d) => d.status === 'done').length }}</dd></div></dl></div>
      <div class="rail-card rail-bank"><h3>我的题库</h3><p>依据当前课程资料生成题目草稿，勾选题目即可保存为试卷。</p><RouterLink to="/question-bank" class="rail-bank-link">打开题库 →</RouterLink></div>
      <div class="rail-card rail-quote"><p class="rail-epigraph">「温故而知新，可以为师矣。」</p><p class="rail-cite">— 《论语》</p></div>
      <div class="rail-card"><h3>提示</h3><p>维度取自已写入的向量集合；模型名取自当前配置。二者不必同时变更。</p></div>
    </aside>
  </div>
</template>

<style scoped>
.documents-page { display: grid; grid-template-columns: minmax(10rem, 1fr) minmax(0, 40rem) minmax(10rem, 1fr); gap: 20px; align-items: start; min-height: calc(100dvh - 52px); padding: 24px 20px 40px; background: radial-gradient(ellipse at 50% 18%, color-mix(in srgb, var(--sz-accent) 7%, transparent), transparent 54%), var(--sz-bg-base); }
.docs-rail { display: flex; flex-direction: column; gap: 12px; padding-top: 40px; position: sticky; top: 0; max-height: calc(100dvh - 72px); overflow-y: auto; scrollbar-width: thin; }
.docs-rail-left { align-items: flex-end; }.docs-rail-right { align-items: flex-start; }
.rail-card { width: min(100%, 232px); padding: 16px 18px; border: 1px solid color-mix(in srgb, var(--sz-border) 80%, transparent); border-radius: 16px; background: color-mix(in srgb, var(--sz-bg-panel) 88%, transparent); box-shadow: 0 10px 30px color-mix(in srgb, var(--sz-text) 4%, transparent); }
.rail-card h3 { margin: 0 0 6px; color: var(--sz-accent); font: 700 12px var(--sz-font-ui); letter-spacing: .08em; }
.rail-card p, .rail-card ol { margin: 0; color: var(--sz-text-muted); font-size: 14px; line-height: 1.55; }
.rail-card ol { padding-left: 18px; line-height: 1.7; }
.rail-quote, .rail-bank { background: color-mix(in srgb, var(--sz-accent) 6%, var(--sz-bg-panel)); }
.rail-card .rail-epigraph { font-family: var(--sz-font-brand); color: var(--sz-text); letter-spacing: .04em; font-size: 16px; }
.rail-card .rail-cite { margin-top: 8px; text-align: right; font-size: 12px; }
.format-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.format-tags span { padding: 2px 7px; border-radius: 999px; border: 1px solid var(--sz-border); background: var(--sz-bg-surface); color: var(--sz-text-muted); font: 600 11px var(--sz-font-ui); }
.rail-card .rail-note { margin-top: 8px; }
.rail-card .rail-value { color: var(--sz-text); font: 700 27px var(--sz-font-ui); line-height: 1.3; }
.rail-card .rail-model { color: var(--sz-text); font: 700 15px var(--sz-font-ui); overflow-wrap: anywhere; }
.rail-stats { display: grid; gap: 8px; margin: 0; }.rail-stats div { display: flex; justify-content: space-between; }.rail-stats dt { color: var(--sz-text-muted); font-size: 13px; }.rail-stats dd { margin: 0; font-weight: 700; }
.rail-bank-link { display: inline-flex; margin-top: 12px; padding: 6px 11px; border-radius: 7px; background: var(--sz-accent); color: var(--sz-on-accent); text-decoration: none; font-size: 13px; font-weight: 700; }
.docs-center { min-width: 0; display: grid; gap: 16px; padding: 24px 22px 28px; border: 1px solid color-mix(in srgb, var(--sz-border) 80%, transparent); border-radius: 20px; background: color-mix(in srgb, var(--sz-bg-panel) 94%, transparent); box-shadow: 0 18px 50px color-mix(in srgb, var(--sz-text) 5%, transparent); }
.docs-hero .eyebrow { font-size: 12px; }.docs-hero h1 { margin: 6px 0; font: 400 clamp(27px, 3vw, 34px) var(--sz-font-brand); letter-spacing: .12em; }.docs-hero > p:last-child { margin: 0; max-width: 36ch; color: var(--sz-text-muted); font-size: 15px; line-height: 1.65; }
.docs-center :deep(.panel) { border-radius: 12px; box-shadow: none; }
@media(max-width: 1050px) { .documents-page { grid-template-columns: minmax(0, 40rem) minmax(12rem, 1fr); justify-content: center; }.docs-rail-left { display: none; } }
@media(max-width: 720px) { .documents-page { display: block; padding: 12px; }.docs-center { padding: 18px 14px; }.docs-rail-right { position: static; max-height: none; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 12px; padding: 0; }.rail-card { width: 100%; }.docs-hero h1 { font-size: 34px; } }
</style>
