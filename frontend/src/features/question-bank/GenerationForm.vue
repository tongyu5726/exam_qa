<script setup>
import { reactive, watch } from 'vue'
import { NButton, NInput, NInputNumber, NSelect } from 'naive-ui'

const props = defineProps({ busy: Boolean, initialTopic: { type: String, default: '' } })
const emit = defineEmits(['submit'])
const form = reactive({ topic: '', chapter: '', question_type: 'short_answer', difficulty: 'medium', count: 3 })
watch(() => props.initialTopic, (topic) => { if (topic) form.topic = topic }, { immediate: true })
const types = [{ label: '简答题', value: 'short_answer' }, { label: '选择题', value: 'choice' }, { label: '填空题', value: 'fill_blank' }]
const levels = [{ label: '基础', value: 'easy' }, { label: '中等', value: 'medium' }, { label: '困难', value: 'hard' }]
function submit() { if (form.topic.trim()) emit('submit', { ...form, topic: form.topic.trim(), chapter: form.chapter.trim(), count: Number(form.count) }) }
</script>

<template>
  <form class="stack" @submit.prevent="submit">
    <label>知识点或主题 <NInput v-model:value="form.topic" maxlength="300" placeholder="如：采样定理" /></label>
    <label>章节（可选） <NInput v-model:value="form.chapter" maxlength="160" placeholder="如：第 4 章" /></label>
    <div class="form-row">
      <label>题型 <NSelect v-model:value="form.question_type" :options="types" /></label>
      <label>难度 <NSelect v-model:value="form.difficulty" :options="levels" /></label>
      <label>题数 <NInputNumber v-model:value="form.count" :min="1" :max="10" /></label>
    </div>
    <NButton type="primary" attr-type="submit" :loading="busy" :disabled="!form.topic.trim()">生成并存入题库</NButton>
    <small class="muted">没有检索到足够资料时不会生成或保存题目。</small>
  </form>
</template>
