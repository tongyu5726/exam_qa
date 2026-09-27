<script setup>
import { reactive } from 'vue'
import { NButton, NCheckbox, NInput, NInputNumber, NSelect } from 'naive-ui'

defineProps({ busy: Boolean })
const emit = defineEmits(['submit'])
const form = reactive({ title: '', topic: '', chapter: '', question_type: 'short_answer', difficulty: 'medium', count: 5, score: 2, allow_generate: true })
const types = [{ label: '简答题', value: 'short_answer' }, { label: '选择题', value: 'choice' }, { label: '填空题', value: 'fill_blank' }]
const levels = [{ label: '基础', value: 'easy' }, { label: '中等', value: 'medium' }, { label: '困难', value: 'hard' }]
function submit() {
  if (!form.title.trim() || !form.topic.trim()) return
  emit('submit', {
    title: form.title.trim(), topic: form.topic.trim(), allow_generate: form.allow_generate,
    rules: [{ chapter: form.chapter.trim(), question_type: form.question_type, difficulty: form.difficulty, count: Number(form.count), score: Number(form.score) }],
  })
}
</script>

<template>
  <form class="stack" @submit.prevent="submit">
    <p class="muted">优先复用有有效资料依据的题目，不足时按主题补题；保存前由后端校验蓝图。</p>
    <div class="form-row"><label>试卷名称 <NInput v-model:value="form.title" maxlength="200" /></label><label>出题主题 <NInput v-model:value="form.topic" maxlength="300" /></label></div>
    <div class="form-row">
      <label>章节 <NInput v-model:value="form.chapter" maxlength="160" placeholder="可选" /></label>
      <label>题型 <NSelect v-model:value="form.question_type" :options="types" /></label>
      <label>难度 <NSelect v-model:value="form.difficulty" :options="levels" /></label>
      <label>题数 <NInputNumber v-model:value="form.count" :min="1" :max="50" /></label>
      <label>每题分 <NInputNumber v-model:value="form.score" :min="0.5" :max="100" :step="0.5" /></label>
    </div>
    <NCheckbox v-model:checked="form.allow_generate">题库不足时允许依据资料补题</NCheckbox>
    <NButton type="primary" attr-type="submit" :loading="busy" :disabled="!form.title.trim() || !form.topic.trim()">按蓝图保存试卷</NButton>
  </form>
</template>
