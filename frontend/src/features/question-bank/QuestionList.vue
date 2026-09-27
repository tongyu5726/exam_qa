<script setup>
import { NCheckbox, NTag } from 'naive-ui'

defineProps({ questions: { type: Array, default: () => [] }, selected: { type: Array, default: () => [] } })
const emit = defineEmits(['toggle'])
</script>

<template>
  <p v-if="!questions.length" class="empty">当前课程还没有题目。填写主题后可依据资料生成草稿。</p>
  <div v-else class="question-list">
    <article v-for="item in questions" :key="item.id" class="question-item">
      <NCheckbox :checked="selected.includes(item.id)" :aria-label="`将题目 ${item.stem} 加入试卷`" @update:checked="(value) => emit('toggle', item.id, value)" />
      <div>
        <h3>{{ item.stem }}</h3>
        <ol v-if="item.options?.length"><li v-for="option in item.options" :key="option">{{ option }}</li></ol>
        <p>答案：{{ item.answer }}</p>
        <p v-if="item.analysis" class="muted">解析：{{ item.analysis }}</p>
        <div class="question-tags"><NTag size="small">{{ item.question_type }}</NTag><NTag size="small">{{ item.difficulty }}</NTag><NTag size="small">{{ item.status }}</NTag><NTag v-if="item.chapter" size="small">{{ item.chapter }}</NTag><NTag v-if="item.citations?.length" size="small" type="success">{{ item.citations.length }} 条资料依据</NTag></div>
      </div>
    </article>
  </div>
</template>
