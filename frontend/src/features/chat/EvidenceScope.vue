<script setup>
import { ref, shallowRef, watch } from 'vue'
import { apiGet } from '@/api/client.js'

const props = defineProps({ courseId: { type: String, default: '' }, disabled: Boolean })
const scenario = defineModel('scenario', { type: String, default: 'all' })
const asOf = defineModel('asOf', { type: String, default: '' })
const fixed = defineModel('fixed', { type: Boolean, default: false })
const scopes = ref([])
const error = shallowRef('')
watch(() => props.courseId, async (courseId, _old, onCleanup) => {
  scenario.value = 'all'; asOf.value = ''; fixed.value = false; scopes.value = []; error.value = ''
  if (!courseId) return
  const controller = new AbortController()
  onCleanup(() => controller.abort())
  try {
    const data = await apiGet('/documents/scenarios', { course_id: courseId }, controller.signal)
    if (!controller.signal.aborted) scopes.value = data.items
  } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message }
}, { immediate: true })
</script>

<template>
  <details class="scope-settings">
    <summary>资料范围 · {{ fixed ? `${scenario === 'all' ? '不限场景' : scenario} · ${asOf || '不限日期'}` : '按问题识别或追问继承' }}</summary>
    <label><input v-model="fixed" type="checkbox" :disabled="disabled || !courseId" /> 固定使用以下范围</label>
    <div class="scope-fields">
      <label>适用场景 <select v-model="scenario" :disabled="disabled || !courseId || !fixed"><option value="all">不限场景</option><option v-for="scope in scopes" :key="scope" :value="scope">{{ scope }}</option></select></label>
      <label>证据生效日 <input v-model="asOf" type="date" :disabled="disabled || !courseId || !fixed" /></label>
    </div>
    <p class="muted">场景来自当前课程资料中人工维护的适用范围。固定范围下，清空日期表示不过滤生效期。</p>
    <p v-if="error" role="alert" class="error-text">场景读取失败：{{ error }}</p>
  </details>
</template>

<style scoped>
.scope-settings { padding: 10px 20px; border-top: 1px solid var(--sz-border); font-size: 13px; }
.scope-settings summary { cursor: pointer; }
.scope-fields { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; }
.scope-fields select, .scope-fields input { padding: 5px; color: var(--sz-text); background: var(--sz-bg-panel); border: 1px solid var(--sz-border); border-radius: 4px; }
</style>
