<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useCourseStore } from '@/stores/course.js'
import { usePractice } from './usePractice.js'
import PracticeCard from './PracticeCard.vue'
import ReviewOverview from './ReviewOverview.vue'

const course = useCourseStore()
const route = useRoute()
const router = useRouter()
function selectAttempt(id) { return router.replace({ name: 'practice', query: id ? { attempt: id } : {} }) }
const { review, attempt, busy, loading, error, load, submit, assess, retry, openRecommendation } = usePractice({
  courseId: () => course.currentId, attemptId: () => String(route.query.attempt || ''),
  questionId: () => String(route.query.question || ''), selectAttempt,
})
</script>

<template>
  <div class="practice-page">
    <header class="page-header"><h1>练习与复习</h1><p>先作答，再核对依据。错题重练会保留每一次结果。</p><RouterLink :to="{ name: 'question-bank' }">去题库选题或出题</RouterLink></header>
    <p v-if="!course.currentId" class="empty">请选择课程后开始练习。</p>
    <p v-if="loading" role="status">正在读取练习记录…</p>
    <p v-if="error" role="alert" class="error-text">{{ error }} <button @click="load">重新读取</button></p>
    <PracticeCard v-if="attempt" :key="attempt.id" :attempt="attempt" :busy="busy" @submit="submit" @assess="assess" @retry="retry" />
    <ReviewOverview v-if="review" :review="review" :busy="busy || loading" @open="selectAttempt" @recommend="openRecommendation" />
  </div>
</template>

<style scoped>
.practice-page { display: grid; gap: 20px; width: min(1000px, calc(100% - 32px)); margin: 32px auto 70px; }
.practice-page a { color: var(--sz-accent); }
</style>
