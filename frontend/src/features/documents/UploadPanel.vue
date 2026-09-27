<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { NButton, NCheckbox, NProgress, useDialog, useMessage } from 'naive-ui'
import { postForm } from '@/api/upload.js'

const props = defineProps({ courseId: { type: String, default: '' } })
const emit = defineEmits(['completed'])
const message = useMessage()
const dialog = useDialog()
const input = ref(null)
const force = ref(false)
const busy = ref(false)
const progress = ref(null)
let controller

watch(() => props.courseId, () => controller?.abort())
onUnmounted(() => controller?.abort())

async function submit(path, form, filename = '') {
  if (!props.courseId || busy.value) return
  const courseId = props.courseId
  controller = new AbortController()
  busy.value = true
  progress.value = { phase: 'upload', ratio: 0, filename }
  try {
    const result = await postForm(path, form, {
      signal: controller.signal,
      onProgress: (next) => { progress.value = { ...next, filename } },
    })
    if (props.courseId !== courseId) return
    const updates = result.updates || (result.update ? [result.update] : [])
    const counts = updates.reduce((all, item) => ({ ...all, [item.action]: (all[item.action] || 0) + 1 }), {})
    message.success(updates.length
      ? [`新增 ${counts.created || 0}`, `更新 ${counts.updated || 0}`, `跳过 ${counts.unchanged || 0}`, `失败 ${counts.failed || 0}`].join(' · ')
      : (result.message || `${filename || '资料'}处理完成`))
    emit('completed')
  } catch (error) {
    if (error.name !== 'AbortError') message.error(error.message)
  } finally {
    busy.value = false
    progress.value = null
    if (input.value) input.value.value = ''
  }
}
function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  const form = new FormData()
  form.append('file', file)
  form.append('course_id', props.courseId)
  submit('/documents', form, file.name)
}
function scan() {
  const run = () => {
    const form = new FormData()
    form.append('course_id', props.courseId)
    form.append('force', String(force.value))
    submit('/documents/scan', form, '课程目录')
  }
  if (!force.value) { run(); return }
  dialog.warning({ title: '强制重建', content: '将重新解析课程目录中的资料，可能需要较长时间。', positiveText: '继续', negativeText: '取消', onPositiveClick: run })
}
</script>

<template>
  <section class="panel upload-panel">
    <div class="section-head"><h2>导入资料</h2><span class="muted">PDF · TXT · MD · Word · PPTX</span></div>
    <p class="muted">上传文件或扫描本机课程目录。解析、分块和向量化会在服务端完成。</p>
    <div class="upload-actions">
      <input ref="input" type="file" accept=".pdf,.txt,.md,.doc,.docx,.pptx" class="sr-only" aria-label="选择资料文件" :disabled="busy || !courseId" @change="upload" />
      <NButton type="primary" :disabled="busy || !courseId" @click="input?.click()">上传资料</NButton>
      <NButton :disabled="busy || !courseId" @click="scan">扫描目录</NButton>
      <NCheckbox v-model:checked="force" :disabled="busy">强制重建</NCheckbox>
    </div>
    <div v-if="progress" class="upload-progress" role="status">
      <span>{{ progress.phase === 'processing' ? '服务器正在解析、分块并入库…' : `上传 ${progress.filename || '资料'} ${Math.round((progress.ratio || 0) * 100)}%` }}</span>
      <NProgress v-if="progress.phase === 'upload'" type="line" :percentage="Math.round((progress.ratio || 0) * 100)" :show-indicator="false" />
      <div v-else class="processing-bar" aria-label="服务器处理中" />
      <NButton size="small" text @click="controller?.abort()">取消</NButton>
    </div>
  </section>
</template>

<style scoped>
.upload-panel { border: 1.5px dashed color-mix(in srgb, var(--sz-accent) 42%, var(--sz-border)); background: color-mix(in srgb, var(--sz-accent) 4%, var(--sz-bg-panel)); }
.upload-panel p { margin: -4px 0 16px; font-size: 13px; }
.upload-panel :deep(.n-button) { border-radius: 999px; }
.upload-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.upload-progress { display: grid; gap: 8px; margin-top: 16px; color: var(--sz-text-muted); }
.processing-bar { height: 5px; border-radius: 3px; background: linear-gradient(90deg, var(--sz-bg-surface), var(--sz-accent), var(--sz-bg-surface)); background-size: 200% 100%; animation: process 1.6s linear infinite; }
@keyframes process { to { background-position: 200% 0; } }
@media(max-width: 620px) { .upload-panel .section-head { display: block; }.upload-panel .section-head h2 { margin-bottom: 3px; }.upload-panel .section-head span { font-size: 11px; } }
</style>
