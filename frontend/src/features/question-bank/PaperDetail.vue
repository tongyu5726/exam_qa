<script setup>
import CitationList from '../chat/CitationList.vue'
defineProps({ paper: { type: Object, required: true }, courseId: { type: String, required: true } })
const types = { choice: '选择题', fill_blank: '填空题', short_answer: '简答题' }
</script>

<template>
  <section class="panel paper-detail" aria-label="试卷详情">
    <h2>{{ paper.title }}</h2>
    <p>{{ paper.question_count }} 道题 · {{ paper.total_score }} 分</p>
    <article v-for="item in paper.items || []" :key="item.position" class="paper-question">
      <h3>{{ item.position }}. {{ item.question.stem }}</h3>
      <p class="muted">{{ types[item.question.question_type] }} · {{ item.score }} 分 · {{ item.question.chapter }}</p>
      <ol v-if="item.question.options?.length"><li v-for="(option, index) in item.question.options" :key="index">{{ option }}</li></ol>
      <details><summary>查看答案与解析</summary><p>答案：{{ item.question.answer }}</p><p>解析：{{ item.question.analysis }}</p><CitationList :citations="item.question.citations || []" :course-id="courseId" /></details>
    </article>
  </section>
</template>

<style scoped>
.paper-question { padding-block: 16px; border-top: 1px solid var(--sz-border); }
.paper-question h3 { font-size: 16px; line-height: 1.7; margin-block: 0 8px; }
.paper-question summary { cursor: pointer; }
.paper-question li, .paper-question p { line-height: 1.8; }
</style>
