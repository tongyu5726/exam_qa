<script setup>
import { computed } from 'vue'
import { NButton, NProgress, NSpin } from 'naive-ui'
import { measuredPercent } from './embeddingProgress.js'

const props = defineProps({ embedding: { type: Object, default: () => ({}) }, provider: { type: String, default: 'local' } })
const emit = defineEmits(['warmup'])

const running = computed(() => props.embedding.warmup?.phase === 'running')
const percent = computed(() => measuredPercent(props.embedding.warmup?.percent))
</script>

<template>
  <div class="settings-section embedding-status">
    <h3>模型状态</h3>
    <p role="status" aria-live="polite">{{ embedding.warmup?.message || embedding.status || '读取中…' }}</p>
    <NProgress v-if="running && percent !== null" type="line" :percentage="percent" />
    <div v-else-if="running" class="indeterminate" role="status">
      <NSpin size="small" />
      <span>正在检查、下载或装载模型，此阶段暂时无法计算百分比。</span>
    </div>
    <p v-if="embedding.warmup?.error" class="error-text">{{ embedding.warmup.error }}</p>
    <p class="muted">更改模型参数后请先保存，再开始预热。</p>
    <NButton :disabled="running" @click="emit('warmup')">{{ provider === 'local' ? '拉取并加载模型' : '探测连通性' }}</NButton>
  </div>
</template>

<style scoped>
.embedding-status h3 { margin: 0 0 10px; }
.indeterminate { display: flex; align-items: center; gap: 12px; min-height: 32px; color: var(--sz-text-muted); font-size: 14px; }
</style>
