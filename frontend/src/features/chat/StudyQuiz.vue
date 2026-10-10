<script setup>
import { computed, ref, watch } from 'vue'
import CitationList from './CitationList.vue'

const props = defineProps({ quiz: { type: Object, required: true }, disabled: Boolean, courseId: { type: String, default: '' } })
const emit = defineEmits(['submit'])
const answers = ref({})
watch(() => props.quiz.id, () => { answers.value = {} })
const ready = computed(() => props.quiz.items.every((_, i) => answers.value[i]))
const letter = (i) => String.fromCharCode(65 + i)
function submit() {
  if (!props.disabled && ready.value && !props.quiz.submitted) emit('submit', props.quiz.items.map((_, i) => `${i + 1}:${answers.value[i]}`).join('，'))
}
</script>

<template>
  <form class="study-quiz" @submit.prevent="submit">
    <h4>本轮自测<span v-if="quiz.submitted"> · 答对 {{ quiz.correct_count }}/{{ quiz.items.length }} 题</span></h4>
    <fieldset v-for="(item, index) in quiz.items" :key="index" :disabled="disabled || quiz.submitted">
      <legend>{{ index + 1 }}. {{ item.stem }}</legend>
      <label v-for="(option, i) in item.options" :key="i"><input v-model="answers[index]" type="radio" :name="`study-${quiz.id}-${index}`" :value="letter(i)"> {{ letter(i) }}. {{ option }}</label>
      <div v-if="quiz.submitted" class="quiz-feedback">
        <p>{{ quiz.results[index].correct ? '回答正确' : '需要复习' }} · 你的选择：{{ quiz.results[index].selected }} · 答案：{{ item.answer }}</p>
        <p>{{ item.analysis }}</p>
        <CitationList :citations="item.citations || []" :course-id="courseId" />
      </div>
    </fieldset>
    <button v-if="!quiz.submitted" type="submit" :disabled="disabled || !ready">提交本轮答案</button>
  </form>
</template>

<style scoped>
.study-quiz { margin-block: 14px; }
fieldset { border: 1px solid var(--sz-border); border-radius: 8px; padding: 12px; margin-block: 10px; min-width: 0; }
legend { white-space: pre-wrap; overflow-wrap: anywhere; }
label { display: block; padding-block: 5px; cursor: pointer; }
.quiz-feedback { font-size: 14px; }
button { padding: 8px 12px; cursor: pointer; }
</style>
