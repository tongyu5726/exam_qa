<script setup>
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NInputNumber } from 'naive-ui'
import { apiGet } from '@/api/client.js'

const props = defineProps({ data: { type: Object, required: true }, busy: Boolean, writable: Boolean })
const emit = defineEmits(['save'])
const host = ref('')
const port = ref(null)
const checking = ref(false)
const checkResult = ref(null)
const checkError = ref('')
const valid = computed(() => Number.isInteger(port.value) && port.value >= 1 && port.value <= 65535 && !!host.value.trim())
const changed = computed(() => host.value.trim() !== props.data.host || port.value !== props.data.port)
const restartNeeded = computed(() => props.data.port !== props.data.running_port || props.data.host !== props.data.running_host)
const suggestions = computed(() => [...new Set([8787, 8888, 9000, (props.data.port || 8787) + 1])].filter((value) => value <= 65535 && value !== props.data.port))

function reset() {
  host.value = props.data.host || '127.0.0.1'
  port.value = props.data.port ?? 8787
  checkResult.value = null
  checkError.value = ''
}
watch(() => props.data, reset, { immediate: true })
watch([host, port], () => { checkResult.value = null; checkError.value = '' })

async function check() {
  if (!valid.value) return
  const checkedPort = port.value
  const checkedHost = host.value.trim()
  checking.value = true
  checkError.value = ''
  try {
    const result = await apiGet('/config/ports/check', { port: checkedPort, host: checkedHost })
    if (port.value === checkedPort && host.value.trim() === checkedHost) checkResult.value = result
  } catch (error) { checkError.value = error.message }
  finally { checking.value = false }
}

function save() {
  if (!valid.value || !changed.value) return
  emit('save', { host: host.value.trim(), port: port.value })
}
</script>

<template>
  <div class="port-settings">
    <div class="port-summary">
      <div><span>当前运行</span><strong>{{ data.running_host || data.host }}:{{ data.running_port ?? data.port }}</strong></div>
      <div><span>下次启动</span><strong>{{ data.host }}:{{ data.port }}</strong></div>
    </div>
    <p v-if="restartNeeded" class="notice" role="status">端口配置已保存。请停止服务并重新运行启动命令，页面随后需从新端口打开。</p>
    <form class="port-form" @submit.prevent="save">
      <label><span>监听地址</span><NInput v-model:value="host" placeholder="127.0.0.1" /></label>
      <label><span>端口号</span><NInputNumber v-model:value="port" :min="1" :max="65535" :precision="0" class="port-input" /></label>
      <div class="suggestions"><span>常用端口</span><NButton v-for="item in suggestions" :key="item" size="small" @click="port = item">{{ item }}</NButton></div>
      <p class="muted">端口范围 1–65535。检测只检查当前监听地址；端口在保存或重启前仍可能被其他程序占用。</p>
      <p v-if="checkResult" role="status" :class="checkResult.available ? 'available' : 'unavailable'">{{ checkResult.current ? '这是当前运行端口' : checkResult.available ? `端口 ${checkResult.port} 当前可用` : `端口 ${checkResult.port} 已被占用或系统保留` }}</p>
      <p v-if="checkError" role="alert" class="unavailable">{{ checkError }}</p>
      <div class="actions"><NButton :disabled="!valid || checking" :loading="checking" @click="check">检测端口</NButton><NButton type="primary" attr-type="submit" :disabled="!writable || !valid || !changed || busy" :loading="busy">保存端口</NButton><NButton :disabled="busy" @click="reset">重置</NButton></div>
      <p v-if="!writable" class="muted">配置文件不可写，无法保存。</p>
    </form>
  </div>
</template>

<style scoped>
.port-settings { max-width: 650px; }
.port-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.port-summary > div { display: grid; gap: 5px; padding: 14px; border: 1px solid var(--sz-border); border-radius: 9px; }
.port-summary span, .muted, .suggestions > span { color: var(--sz-text-muted); }
.port-summary strong { font-size: 18px; overflow-wrap: anywhere; }
.notice { padding: 12px; border-radius: 8px; background: color-mix(in srgb, var(--sz-accent) 10%, var(--sz-bg-panel)); }
.port-form { display: grid; gap: 14px; }
.port-form label { display: grid; gap: 6px; font-weight: 600; }
.port-input { width: 100%; }
.suggestions, .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.muted { margin: 0; font-size: 13px; line-height: 1.6; }
.available { color: #247a4b; }.unavailable { color: #c74444; }
@media (max-width: 540px) { .port-summary { grid-template-columns: 1fr; } }
</style>
