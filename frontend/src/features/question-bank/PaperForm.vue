<script setup>
import { reactive } from 'vue'
import { NButton, NInput } from 'naive-ui'

defineProps({ busy: Boolean, selectedCount: { type: Number, default: 0 } })
const emit = defineEmits(['submit'])
const form = reactive({ title: '', description: '' })
function submit() {
  if (!form.title.trim()) return
  emit('submit', { title: form.title.trim(), description: form.description.trim() })
}
function reset() { form.title = ''; form.description = '' }
defineExpose({ reset })
</script>

<template>
  <form class="stack" @submit.prevent="submit">
    <label>试卷名称 <NInput v-model:value="form.title" maxlength="200" placeholder="如：采样定理练习卷" /></label>
    <label>说明（可选） <NInput v-model:value="form.description" type="textarea" maxlength="1000" :rows="3" /></label>
    <NButton attr-type="submit" :loading="busy" :disabled="!selectedCount || !form.title.trim()">用已勾选题目保存试卷</NButton>
    <small class="muted">{{ selectedCount ? `已勾选 ${selectedCount} 道题，每题默认 1 分` : '尚未勾选题目' }}</small>
  </form>
</template>
