import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

const choices = new Set(['system', 'light', 'dark'])

export const useThemeStore = defineStore('theme', () => {
  const saved = localStorage.getItem('sz.theme') || 'system'
  const preference = ref(choices.has(saved) ? saved : 'system')
  const systemDark = ref(window.matchMedia('(prefers-color-scheme: dark)').matches)
  const resolved = computed(() => preference.value === 'system' ? (systemDark.value ? 'dark' : 'light') : preference.value)
  let initialized = false

  function setPreference(value) {
    if (!choices.has(value)) return
    preference.value = value
    localStorage.setItem('sz.theme', value)
    document.documentElement.dataset.theme = resolved.value
  }

  function init() {
    if (initialized) return
    initialized = true
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', (event) => {
      systemDark.value = event.matches
      document.documentElement.dataset.theme = resolved.value
    })
    document.documentElement.dataset.theme = resolved.value
  }

  return { preference, resolved, setPreference, init }
})
