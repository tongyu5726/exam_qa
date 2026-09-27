<script setup>
import { reactive, ref } from 'vue'
import { NButton, NInput, NSelect, NTag, useDialog, useMessage } from 'naive-ui'
import { apiPost, apiDelete } from '@/api/client.js'

const props = defineProps({ providers: { type: Array, default: () => [] }, active: { type: String, default: '' }, formats: { type: Array, default: () => ['openai', 'local'] }, writable: Boolean })
const emit = defineEmits(['changed'])
const message = useMessage()
const dialog = useDialog()
const form = reactive({ name: '', format: 'openai', model: '', base_url: '', api_key: '' })
const adding = ref(false)
const busy = ref('')
async function add() {
  if (!form.name.trim() || !form.model.trim()) { message.warning('填写名称和模型 ID'); return }
  busy.value = 'add'
  try { await apiPost('/llm-providers', { ...form, name: form.name.trim(), model: form.model.trim() }); message.success('模型已注册'); Object.assign(form, { name: '', format: 'openai', model: '', base_url: '', api_key: '' }); adding.value = false; emit('changed') }
  catch (error) { message.error(error.message) }
  finally { busy.value = '' }
}
async function activate(name) {
  busy.value = name
  try { await apiPost('/llm-providers/active', { name }); message.success(`已切换为 ${name}`); emit('changed') }
  catch (error) { message.error(error.message) }
  finally { busy.value = '' }
}
function remove(name) {
  dialog.warning({ title: '删除模型', content: `确定删除「${name}」？`, positiveText: '删除', negativeText: '取消', onPositiveClick: async () => {
    busy.value = name
    try { await apiDelete(`/llm-providers/${encodeURIComponent(name)}`); message.success('已删除'); emit('changed') }
    catch (error) { message.error(error.message) }
    finally { busy.value = '' }
  } })
}
</script>

<template>
  <div class="provider-manager">
    <div class="section-head"><h3>已注册模型</h3><NButton size="small" :disabled="!writable" @click="adding = !adding">{{ adding ? '收起' : '+ 添加模型' }}</NButton></div>
    <p class="provider-note">模型凭据仅在本机配置中保存。切换当前模型后，新的问答会使用所选模型。</p>
    <div v-if="!providers.length" class="empty">尚未注册模型。</div>
    <div v-else class="providers"><div class="provider-table-head"><span>名称</span><span>模型 / 类型</span><span>地址</span><span>操作</span></div><article v-for="provider in providers" :key="provider.name" class="provider-row"><div><strong>{{ provider.name }}</strong> <NTag v-if="provider.name === active" type="success" size="small">使用中</NTag></div><div><strong>{{ provider.model }}</strong><small>{{ provider.format }} <span v-if="provider.has_api_key">· Key 已配置</span></small></div><div class="provider-url">{{ provider.base_url || '—' }}</div><div class="provider-actions"><NButton v-if="provider.name !== active" size="small" :loading="busy === provider.name" :disabled="!writable" @click="activate(provider.name)">设为当前</NButton><NButton size="small" text type="error" :disabled="!writable || (provider.name === active && providers.length === 1)" @click="remove(provider.name)">删除</NButton></div></article></div>
    <form v-if="adding" class="add-provider" autocomplete="off" @submit.prevent="add"><h3>注册新模型</h3><label>名称<NInput v-model:value="form.name" placeholder="如 deepseek" /></label><label>类型<NSelect v-model:value="form.format" :options="formats.map((item) => ({ label: item, value: item }))" /></label><label>模型 ID<NInput v-model:value="form.model" placeholder="如 deepseek-chat" /></label><label>Base URL<NInput v-model:value="form.base_url" placeholder="https://.../v1" /></label><label>API Key<NInput v-model:value="form.api_key" type="password" show-password-on="click" placeholder="本地模型可留空" /></label><NButton type="primary" attr-type="submit" :loading="busy === 'add'" :disabled="!writable">注册</NButton></form>
  </div>
</template>

<style scoped>
.provider-manager { display: grid; gap: 12px; margin-bottom: 16px; }
.provider-manager h3 { margin: 0; font-size: 16px; }
.provider-note { margin: 0; padding: 12px 16px; border: 1px solid color-mix(in srgb, var(--sz-accent) 20%, var(--sz-border)); border-radius: 9px; background: color-mix(in srgb, var(--sz-accent) 7%, var(--sz-bg-panel)); color: var(--sz-text-muted); font-size: 13px; }
.providers { border: 1px solid var(--sz-border); border-radius: 9px; background: var(--sz-bg-panel); overflow: hidden; }
.provider-table-head, .provider-row { display: grid; grid-template-columns: minmax(130px, 1fr) minmax(150px, 1.3fr) minmax(160px, 1.6fr) auto; align-items: center; gap: 12px; padding: 11px 14px; }
.provider-table-head { background: var(--sz-bg-surface); color: var(--sz-text-muted); font-size: 12px; font-weight: 700; }
.provider-row { border-top: 1px solid var(--sz-border); font-size: 13px; overflow-wrap: anywhere; }
.provider-row strong { font-weight: 700; }.provider-row small { display: block; color: var(--sz-text-muted); }.provider-url { color: var(--sz-text-muted); }
.provider-actions { display: flex; gap: 7px; flex: none; }
.add-provider { display: grid; gap: 12px; padding: 18px; border: 1px solid var(--sz-border); border-radius: 9px; }
.add-provider label { display: grid; gap: 6px; }
@media(max-width: 900px) { .provider-table-head { display: none; }.provider-row { grid-template-columns: 1fr auto; }.provider-url { grid-column: 1 / -1; }.provider-actions { grid-column: 2; grid-row: 1 / 3; } }
@media(max-width: 620px) { .provider-row { grid-template-columns: 1fr; }.provider-actions { grid-column: 1; grid-row: auto; } }
</style>
