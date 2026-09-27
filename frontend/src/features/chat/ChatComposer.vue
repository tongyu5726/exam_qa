<script setup>
import { computed, ref } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'

const props = defineProps({ mode: { type: String, default: 'auto' }, busy: Boolean, disabled: Boolean })
const emit = defineEmits(['update:mode', 'submit'])
const question = ref('')
const modes = [
  { label: '自动识别', value: 'auto' }, { label: '自由问答', value: 'qa' },
  { label: '知识点', value: 'concept' }, { label: '章节概览', value: 'chapter' },
]
const placeholder = computed(() => ({
  chapter: '输入章节名…', concept: '输入知识点名称…',
}[props.mode] || '输入问题…'))
const actionLabel = computed(() => ({ chapter: '概览', concept: '检索' }[props.mode] || '提问'))

function submit() {
  const text = question.value.trim()
  if (!text || props.busy || props.disabled) return
  question.value = ''
  emit('submit', text)
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}
</script>

<template>
  <form class="chat-composer" @submit.prevent="submit">
    <NSelect :value="mode" :options="modes" class="mode-select" :disabled="busy" @update:value="emit('update:mode', $event)" />
    <NInput v-model:value="question" type="textarea" :autosize="{ minRows: 1, maxRows: 5 }" :placeholder="placeholder" :disabled="disabled || busy" aria-label="输入问题" @keydown="handleKeydown" />
    <NButton type="primary" attr-type="submit" :loading="busy" :disabled="!question.trim() || disabled">{{ actionLabel }}</NButton>
  </form>
</template>

<style scoped>
.chat-composer { display: flex; align-items: end; gap: 9px; border-top: 1px solid var(--sz-border); padding: 12px 18px; background: var(--sz-bg-surface); }
.mode-select { flex: none; width: 125px; margin-bottom: 1px; }
.chat-composer :deep(.n-input) { flex: 1; min-height: 52px; border-radius: 16px; align-items: center; }
.chat-composer :deep(.n-input__textarea-el) { min-height: 36px !important; padding-top: 8px; }
.chat-composer :deep(.n-button) { border-radius: 999px; padding-inline: 22px; }
@media(max-width: 720px) { .chat-composer { padding: 10px; } }
@media(max-width: 620px) { .mode-select { width: 105px; } .chat-composer { gap: 5px; } .chat-composer :deep(.n-button) { padding-inline: 12px; } }
</style>
