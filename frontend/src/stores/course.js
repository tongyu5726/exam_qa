import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiGet } from '@/api/client.js'

export const useCourseStore = defineStore('course', () => {
  const colleges = ref([])
  const allCourses = ref([])
  const currentId = ref(localStorage.getItem('sz.course_id') || '')
  const collegeId = ref('')
  const loading = ref(false)
  const courses = computed(() => allCourses.value.filter((item) => item.college_id === collegeId.value))
  const current = computed(() => allCourses.value.find((item) => item.id === currentId.value) || null)

  async function load() {
    loading.value = true
    try {
      const [collegeData, courseData] = await Promise.all([apiGet('/colleges'), apiGet('/courses')])
      colleges.value = collegeData.items || []
      allCourses.value = courseData.items || []
      const selected = allCourses.value.find((item) => item.id === currentId.value)
      collegeId.value = selected?.college_id || colleges.value[0]?.id || ''
      if (!selected) setCourse(courses.value[0]?.id || '')
    } finally { loading.value = false }
  }

  function setCollege(id) {
    collegeId.value = id
    if (!courses.value.some((item) => item.id === currentId.value)) setCourse(courses.value[0]?.id || '')
  }

  function setCourse(id) {
    currentId.value = id
    if (id) localStorage.setItem('sz.course_id', id)
    else localStorage.removeItem('sz.course_id')
  }

  return { colleges, courses, allCourses, current, currentId, collegeId, loading, load, setCollege, setCourse }
})
