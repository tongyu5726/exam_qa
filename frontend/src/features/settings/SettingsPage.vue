<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { NButton, NInput, useMessage } from 'naive-ui'
import { apiGet, apiPost, apiPatch } from '@/api/client.js'
import { groups } from './fields.js'
import ConfigGroupForm from './ConfigGroupForm.vue'
import MirrorSettingsForm from './MirrorSettingsForm.vue'
import EmbeddingStatus from './EmbeddingStatus.vue'
import ProviderManager from './ProviderManager.vue'

const message = useMessage()
const cfg = ref(null)
const active = ref('llm')
const search = ref('')
const busy = ref(false)
const error = ref('')
const embedding = ref(null)
let pollTimer
const group = computed(() => groups.find((item) => item.id === active.value) || groups[0])
const visibleGroups = computed(() => groups.filter((item) => item.title.toLowerCase().includes(search.value.trim().toLowerCase()) || item.id.includes(search.value.trim().toLowerCase())))
const effectsText = computed(() => {
  const effects = cfg.value?.settings_effects
  if (!effects) return ''
  return [effects.hot_reload?.length && `已热更新：${effects.hot_reload.join('、')}`, effects.restart_required?.length && `需重启：${effects.restart_required.join('、')}`, ...(effects.notes || [])].filter(Boolean).join('；')
})
async function load() {
  try { cfg.value = await apiGet('/config'); error.value = '' }
  catch (cause) { error.value = cause.message }
}
async function save(payload) {
  if (!Object.keys(payload).length) { message.info('没有需要保存的更改'); return }
  busy.value = true
  try { cfg.value = await apiPatch('/config', { [active.value]: payload }); message.success('配置已保存'); if (effectsText.value) message.info(effectsText.value) }
  catch (cause) { message.error(cause.message) }
  finally { busy.value = false }
}
async function refreshEmbedding() {
  try {
    embedding.value = await apiGet('/embedding/status')
    if (embedding.value.warmup?.phase === 'running') {
      if (!pollTimer) pollTimer = window.setInterval(refreshEmbedding, 1000)
    } else if (pollTimer) { clearInterval(pollTimer); pollTimer = undefined }
  } catch (cause) { if (pollTimer) { clearInterval(pollTimer); pollTimer = undefined }; message.error(cause.message) }
}
async function warmup() {
  try { embedding.value = await apiPost('/embedding/warmup', {}); await refreshEmbedding() }
  catch (cause) { message.error(cause.message) }
}
onMounted(() => { load(); refreshEmbedding() })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <div class="settings-page">
    <p v-if="error" role="alert" class="error-text">{{ error }} <NButton text @click="load">重试</NButton></p>
    <div class="settings-layout"><nav aria-label="设置分组" class="settings-nav"><NInput v-model:value="search" clearable placeholder="搜索设置" aria-label="搜索设置分组" /><div class="nav-list"><button v-for="item in visibleGroups" :key="item.id" type="button" :class="{ active: active === item.id }" @click="active = item.id"><span>{{ item.title }}</span><small>{{ item.id }}</small></button></div><div v-if="cfg" class="settings-meta"><strong>运行信息</strong><dl><dt>配置写入</dt><dd>{{ cfg.meta?.env_writable === false ? '只读' : '可用' }}</dd><dt>当前分组</dt><dd>{{ group.id }}</dd></dl></div></nav>
      <main v-if="cfg" class="settings-content"><header class="settings-header"><h2>{{ group.title }}</h2><p v-if="group.id === 'retrieval'" class="muted">问答采用向量 + BM25 混合召回；低于阈值时拒答。</p></header>
        <p v-if="active === 'proxy'" class="proxy-note">下载镜像与 HTTP 代理是两种地址。Hugging Face 镜像用于模型下载；GitHub 加速服务用于生成公开文件链接，当前服务不自动下载 GitHub 文件。</p>
        <ProviderManager v-if="active === 'llm'" :providers="cfg.llm?.providers || []" :active="cfg.llm?.active || ''" :formats="cfg.llm?.formats || ['openai', 'local']" :writable="cfg.meta?.env_writable !== false" @changed="load" />
        <section v-if="active === 'proxy'" class="settings-section"><h3>公开下载镜像</h3><MirrorSettingsForm :data="cfg.proxy || {}" :busy="busy" :writable="cfg.meta?.env_writable !== false" @save="save" /></section>
        <section class="settings-section"><h3>{{ active === 'llm' ? '生成参数' : active === 'proxy' ? 'HTTP 代理参数' : `${group.title}参数` }}</h3><p v-if="active === 'proxy'" class="muted proxy-field-note">填入正在运行的 HTTP 代理服务地址，如本机代理软件的 http://127.0.0.1:7890。站点专用地址留空时沿用通用代理。</p><ConfigGroupForm :key="active" :group="group" :data="cfg[active] || {}" :busy="busy" :writable="cfg.meta?.env_writable !== false" @save="save" /></section>
        <EmbeddingStatus v-if="active === 'embedding'" :embedding="embedding || {}" :provider="cfg.embedding?.provider || 'local'" @warmup="warmup" />
        <p v-if="effectsText" class="settings-effects">{{ effectsText }}</p>
      </main><div v-else class="panel">读取配置中…</div></div>
  </div>
</template>

<style scoped>
.settings-layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); height: calc(100dvh - 52px); min-height: 520px; overflow: hidden; }
.settings-nav { display: flex; flex-direction: column; gap: 12px; overflow-y: auto; padding: 12px; background: color-mix(in srgb, var(--sz-bg-panel) 92%, transparent); border-right: 5px solid var(--sz-border); }
.settings-nav :deep(.n-input) { border-radius: 999px; }
.nav-list { display: grid; align-content: start; gap: 4px; flex: 1; }
.settings-nav button { display: flex; flex-direction: column; align-items: flex-start; border: 1px solid transparent; background: none; color: var(--sz-text); padding: 7px 9px; text-align: left; cursor: pointer; border-radius: 8px; }
.settings-nav button span { font-size: 14px; }.settings-nav button small { font-size: 12px; color: var(--sz-text-muted); }
.settings-nav button:hover, .settings-nav button.active { background: var(--sz-bg-surface); border-color: var(--sz-border); }
.settings-meta { border-top: 1px solid var(--sz-border); padding-top: 12px; font-size: 12px; }.settings-meta strong { color: var(--sz-text-muted); }.settings-meta dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 8px; }.settings-meta dt { color: var(--sz-text-muted); }.settings-meta dd { margin: 0; }
.settings-content { min-width: 0; overflow-y: auto; padding: 18px 20px 40px; background: var(--sz-bg-surface); }
.settings-header { margin-bottom: 16px; }.settings-header h2 { margin: 0; font-size: 20px; }.settings-header p { margin: 4px 0 0; }
.proxy-note { max-width: 760px; margin: 0 0 16px; padding: 12px 15px; border: 1px solid color-mix(in srgb, var(--sz-accent) 22%, var(--sz-border)); border-radius: 9px; background: color-mix(in srgb, var(--sz-accent) 6%, var(--sz-bg-panel)); color: var(--sz-text-muted); font-size: 14px; line-height: 1.7; }
.proxy-field-note { margin: -5px 0 18px; line-height: 1.6; }
.settings-section { padding: 20px; margin-top: 16px; border: 1px solid var(--sz-border); border-radius: 10px; background: var(--sz-bg-panel); }.settings-section h3 { margin: 0 0 18px; font-size: 16px; }
.settings-effects { margin-top: 20px; color: var(--sz-accent); }
@media(max-width: 720px) { .settings-layout { display: flex; flex-direction: column; height: auto; min-height: calc(100dvh - 90px); }.settings-nav { overflow: visible; border-right: 0; border-bottom: 1px solid var(--sz-border); }.nav-list { display: flex; overflow-x: auto; }.settings-nav button { flex: none; }.settings-meta { display: none; }.settings-content { overflow: visible; padding: 16px; } }
</style>
