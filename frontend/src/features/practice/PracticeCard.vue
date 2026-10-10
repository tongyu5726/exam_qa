<script setup>
import { computed, ref, watch } from 'vue'
import CitationList from '@/features/chat/CitationList.vue'

const props = defineProps({ attempt: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['submit', 'assess', 'retry'])
const draftKey = `sz.practice.${props.attempt.course_id}.${props.attempt.id}`
function readDraft() {
  try { return JSON.parse(localStorage.getItem(draftKey) || '{}') || {} } catch { return {} }
}
const draft = readDraft()
const text = ref(typeof draft.text === 'string' ? draft.text : '')
const selected = ref(Number.isInteger(draft.selected) ? draft.selected : null)
const started = computed(() => props.attempt.status === 'started')
const choice = computed(() => props.attempt.grading_method === 'objective')
const canSubmit = computed(() => choice.value ? selected.value !== null : !!text.value.trim())
watch([text, selected], () => {
  if (started.value) localStorage.setItem(draftKey, JSON.stringify({ text: text.value, selected: selected.value }))
}, { flush: 'sync' })
watch(() => props.attempt.status, (status) => { if (status !== 'started') localStorage.removeItem(draftKey) }, { immediate: true })
</script>

<template>
  <section class="panel practice-card" aria-label="当前练习">
    <p class="muted">{{ attempt.question.chapter || '单题练习' }} · {{ choice ? '选择题 · 自动判分' : '主观题 · 提交后自评' }}</p>
    <h2>{{ attempt.question.stem }}</h2>
    <p v-if="attempt.evidence_status === 'stale'" role="status" class="source-warning">资料依据已失效或无法核实。本题保留历史内容，请核对当前资料。</p>
    <p v-else-if="attempt.evidence_status === 'unverified'" class="muted">本题尚无可核实的资料引用。</p>
    <form v-if="started" @submit.prevent="emit('submit', { selected_option: choice ? selected : null, user_answer: choice ? '' : text })">
      <fieldset v-if="choice" :disabled="busy"><legend>选择一个答案</legend><label v-for="(option, index) in attempt.question.options" :key="index" class="option"><input v-model="selected" type="radio" :name="attempt.id" :value="index" /> {{ option }}</label></fieldset>
      <label v-else class="text-answer">你的答案<textarea v-model="text" rows="5" maxlength="4000" :disabled="busy" placeholder="先写下你的理解，再核对资料" /></label>
      <button class="practice-button" type="submit" :disabled="busy || !canSubmit">{{ busy ? '保存中…' : '提交答案' }}</button>
      <p class="muted">刷新后可继续练习；输入草稿保存在当前浏览器。</p>
    </form>
    <div v-else aria-live="polite">
      <p class="user-answer">你的答案：{{ attempt.user_answer }}</p>
      <p v-if="attempt.correct !== null" class="result">{{ choice ? '判分结果' : '自评结果' }}：{{ attempt.correct ? '正确' : '错误，建议回看资料后重练' }}</p>
      <p class="reference-answer">参考答案：{{ attempt.question.answer }}</p>
      <p v-if="attempt.question.analysis" class="analysis">解析：{{ attempt.question.analysis }}</p>
      <CitationList :citations="attempt.question.citations || []" :course-id="attempt.course_id" />
      <div v-if="attempt.status === 'submitted'" class="practice-actions"><p class="muted">对照答案和依据，自评本次作答。</p><button class="practice-button" :disabled="busy" @click="emit('assess', true)">自评正确</button><button class="practice-button" :disabled="busy" @click="emit('assess', false)">自评错误</button></div>
      <button v-else class="practice-button" :disabled="busy" @click="emit('retry')">再练一次</button>
    </div>
  </section>
</template>

<style scoped>
.practice-card { min-width: 0; }
.practice-card h2 { white-space: pre-wrap; }
.option { display: block; padding: 10px; margin: 7px 0; background: var(--sz-bg-surface); border-radius: 6px; cursor: pointer; }
.text-answer { display: grid; gap: 8px; }
.text-answer textarea { width: 100%; box-sizing: border-box; padding: 12px; color: var(--sz-text); background: var(--sz-bg-panel); border: 1px solid var(--sz-border); border-radius: 6px; resize: vertical; }
.practice-button { margin: 12px 10px 0 0; border: 1px solid var(--sz-border); border-radius: 6px; background: var(--sz-bg-surface); color: var(--sz-accent); padding: 8px 15px; cursor: pointer; }
.practice-button:disabled { opacity: .5; cursor: default; }
.source-warning { color: var(--sz-warning); }
.user-answer, .reference-answer, .analysis { white-space: pre-wrap; overflow-wrap: anywhere; }
.result { color: var(--sz-accent); }
</style>
