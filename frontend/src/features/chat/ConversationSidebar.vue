<script setup>
import { computed, nextTick, onUnmounted, ref } from 'vue'

const props = defineProps({ items: { type: Array, default: () => [] }, activeId: { type: String, default: '' }, collapsed: Boolean, courseId: { type: String, default: '' } })
const emit = defineEmits(['new', 'select', 'remove', 'toggle', 'resize'])
const root = ref(null)
const searchInput = ref(null)
const searching = ref(false)
const query = ref('')
const visibleItems = computed(() => props.items.filter((item) => (item.title || '新对话').toLowerCase().includes(query.value.trim().toLowerCase())))

function move(event) { if (root.value) emit('resize', Math.min(360, Math.max(180, event.clientX - root.value.getBoundingClientRect().left))) }
function stop() { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop) }
function start(event) { event.preventDefault(); window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop) }
async function openSearch() {
  if (props.collapsed) emit('toggle')
  searching.value = true
  await nextTick()
  searchInput.value?.focus()
}
function closeSearch() { searching.value = false; query.value = '' }
onUnmounted(stop)
</script>

<template>
  <aside ref="root" class="history" :class="{ collapsed }" aria-label="对话历史">
    <div class="history-toolbar">
      <span v-if="!collapsed" class="history-label">对话</span>
      <button v-if="!collapsed" type="button" class="icon-button search-toggle" aria-label="搜索对话" title="搜索对话" @click="openSearch"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.3"/><path d="m15.4 15.4 4.5 4.5"/></svg></button>
      <button type="button" class="icon-button collapse-toggle" :aria-label="collapsed ? '展开历史' : '收起历史'" :title="collapsed ? '展开历史' : '收起历史'" @click="$emit('toggle')"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/><path v-if="collapsed" d="m13 9 3 3-3 3"/><path v-else d="m16 9-3 3 3 3"/></svg></button>
    </div>
    <button type="button" class="new-chat" :disabled="!courseId" :aria-label="collapsed ? '新对话' : undefined" title="新对话" @click="$emit('new')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12.5 4H6a3 3 0 0 0-3 3v11a3 3 0 0 0 3 3h11a3 3 0 0 0 3-3v-6.5"/><path d="m10 14 8.7-8.7a2 2 0 0 1 2.8 2.8L12.8 16.8 9 18z"/></svg><span v-if="!collapsed">新对话</span></button>
    <template v-if="!collapsed">
      <div v-if="searching" class="history-search"><input ref="searchInput" v-model="query" type="search" placeholder="搜索历史对话" aria-label="搜索历史对话" /><button type="button" aria-label="关闭搜索" @click="closeSearch">×</button></div>
      <div class="history-list">
        <p v-if="!courseId" class="history-empty">请先选择课程</p>
        <p v-else-if="!items.length" class="history-empty">暂无对话，点击上方开始</p>
        <p v-else-if="!visibleItems.length" class="history-empty">没有找到匹配的对话</p>
        <template v-else><p class="list-caption">最近</p><div v-for="item in visibleItems" :key="item.id" class="history-row" :class="{ active: activeId === item.id }"><button type="button" class="history-title" @click="$emit('select', item.id)">{{ item.title || '新对话' }}</button><button type="button" class="remove-button" :aria-label="`删除${item.title || '对话'}`" @click="$emit('remove', item)">删除</button></div></template>
      </div>
      <div class="history-grip" role="separator" aria-label="调整历史栏宽度" aria-orientation="vertical" @pointerdown="start" />
    </template>
    <template v-else><button type="button" class="icon-button rail-search" aria-label="搜索对话" title="搜索对话" @click="openSearch"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.3"/><path d="m15.4 15.4 4.5 4.5"/></svg></button></template>
  </aside>
</template>

<style scoped>
.history { width: var(--history-width, 260px); position: relative; flex: none; display: flex; flex-direction: column; min-width: 0; padding: 8px; border-right: 1px solid var(--sz-border); background: var(--sz-bg-panel); }
.history.collapsed { width: 62px; align-items: center; padding: 8px 6px; }
.history-toolbar { width: 100%; min-height: 43px; display: flex; align-items: center; gap: 3px; padding: 0 6px 7px; }
.history-label { flex: 1; padding-left: 8px; color: var(--sz-text); font-size: 15px; font-weight: 700; }
.icon-button { display: grid; place-items: center; flex: none; width: 36px; height: 36px; border: 0; border-radius: 10px; background: transparent; color: var(--sz-text-muted); cursor: pointer; }
.icon-button:hover, .icon-button:focus-visible { background: var(--sz-bg-surface); color: var(--sz-text); }
.icon-button svg, .new-chat svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.new-chat { display: flex; align-items: center; gap: 12px; width: 100%; min-height: 42px; padding: 8px 12px; border: 0; border-radius: 10px; background: var(--sz-bg-surface); color: var(--sz-text); text-align: left; font-size: 14px; font-weight: 700; cursor: pointer; }
.new-chat:hover, .new-chat:focus-visible { background: color-mix(in srgb, var(--sz-accent) 12%, var(--sz-bg-surface)); }
.new-chat:disabled { opacity: .55; cursor: not-allowed; }
.history.collapsed .history-toolbar { justify-content: center; padding: 0 0 7px; }.history.collapsed .new-chat { justify-content: center; width: 44px; height: 44px; padding: 0; }.history.collapsed .rail-search { margin-top: 6px; }
.history-list { min-height: 0; flex: 1; overflow-y: auto; margin-top: 13px; scrollbar-width: thin; }
.list-caption { margin: 12px 10px 5px; color: var(--sz-text-muted); font-size: 12px; font-weight: 700; }
.history-empty { margin: 18px 7px; color: var(--sz-text-muted); font-size: 14px; line-height: 1.6; }
.history-row { display: flex; align-items: center; min-width: 0; border-radius: 9px; }.history-row:hover, .history-row.active { background: var(--sz-bg-surface); }
.history-title { flex: 1; min-width: 0; padding: 9px 10px; border: 0; background: transparent; color: var(--sz-text); font-size: 13px; font-weight: 600; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.remove-button { display: none; flex: none; margin-right: 6px; padding: 4px; border: 0; border-radius: 5px; background: none; color: var(--sz-text-muted); font-size: 11px; cursor: pointer; }.history-row:hover .remove-button, .history-row:focus-within .remove-button { display: block; }.remove-button:hover { color: var(--sz-danger); }
.history-search { display: flex; margin-top: 10px; border: 1px solid var(--sz-border); border-radius: 9px; background: var(--sz-bg-panel); }.history-search:focus-within { border-color: var(--sz-accent); }.history-search input { width: 100%; min-width: 0; padding: 7px 9px; border: 0; outline: 0; background: transparent; color: var(--sz-text); font-size: 13px; }.history-search button { border: 0; padding: 0 9px; background: none; color: var(--sz-text-muted); cursor: pointer; }
.history-grip { position: absolute; right: -4px; top: 0; bottom: 0; width: 8px; cursor: col-resize; }
@media(max-width: 720px) { .history:not(.collapsed) { width: 185px; } }
@media(max-width: 620px) { .history:not(.collapsed) { width: 170px; }.history.collapsed { width: 52px; }.history-grip { display: none; } }
</style>
