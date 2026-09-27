<script setup>
import { reactive, watch, computed } from 'vue'
import { NButton, NInput, NInputNumber, NSelect, NSwitch } from 'naive-ui'

const props = defineProps({ group: { type: Object, required: true }, data: { type: Object, default: () => ({}) }, busy: Boolean, writable: Boolean })
const emit = defineEmits(['save'])
const values = reactive({})
const fields = computed(() => props.group.fields.filter((field) => !field.remoteOnly || values.provider === 'openai'))
function reset() { for (const field of props.group.fields) values[field.key] = field.type === 'secret' ? '' : props.data[field.key] ?? (field.type === 'boolean' ? false : '') }
watch(() => [props.group, props.data], reset, { immediate: true })
function save() {
  const payload = {}
  for (const field of fields.value) {
    const value = values[field.key]
    if (field.type === 'secret' && (!value || value === '***')) continue
    if (value !== props.data[field.key] && value !== null && value !== undefined) payload[field.key] = value
  }
  emit('save', payload)
}
</script>

<template>
  <form class="settings-form" autocomplete="off" @submit.prevent="save">
    <label v-for="field in fields" :key="field.key" class="settings-field" :class="{ 'switch-field': field.type === 'boolean' }">
      <span>{{ field.label }}</span>
      <NSwitch v-if="field.type === 'boolean'" v-model:value="values[field.key]" />
      <NSelect v-else-if="field.type === 'select'" v-model:value="values[field.key]" :options="field.options" />
      <NInputNumber v-else-if="field.type === 'number'" v-model:value="values[field.key]" :step="field.step || 1" class="number-input" />
      <NInput v-else v-model:value="values[field.key]" :type="field.type === 'secret' ? 'password' : 'text'" :placeholder="field.type === 'secret' ? (data[field.configuredKey || 'configured'] ? '已配置，留空保持不变' : '未配置') : ''" :show-password-on="field.type === 'secret' ? 'click' : undefined" />
    </label>
    <div class="settings-actions"><NButton type="primary" attr-type="submit" :loading="busy" :disabled="!writable">保存配置</NButton><NButton :disabled="busy" @click="reset">重置</NButton><span v-if="!writable" class="muted">配置文件不可写</span></div>
  </form>
</template>

<style scoped>
.settings-form { display: grid; gap: 16px; }
.settings-field { display: grid; gap: 6px; max-width: 600px; }
.settings-field > span { font-weight: 600; }
.switch-field { display: flex; align-items: center; justify-content: space-between; max-width: 600px; }
.number-input { width: 100%; }
.settings-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 6px; }
</style>
