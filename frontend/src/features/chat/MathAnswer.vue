<script setup>
import { nextTick, ref, watch } from 'vue'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'

const props = defineProps({ text: { type: String, default: '' }, final: Boolean })
const root = ref(null)
watch(() => [props.text, props.final], async () => {
  await nextTick()
  if (!root.value) return
  root.value.textContent = props.text
  if (props.final) renderMathInElement(root.value, { delimiters: [
    { left: '$$', right: '$$', display: true }, { left: '\\[', right: '\\]', display: true },
    { left: '$', right: '$', display: false }, { left: '\\(', right: '\\)', display: false },
  ], throwOnError: false, trust: false })
}, { immediate: true })
</script>

<template><div ref="root" class="math-answer" /></template>

<style scoped>.math-answer { white-space: pre-wrap; line-height: 1.8; overflow-wrap: anywhere; }</style>
