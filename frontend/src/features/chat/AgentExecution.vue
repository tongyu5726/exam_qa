<script setup>
import { computed } from 'vue'

const props = defineProps({ execution: { type: Object, default: null }, nodes: { type: Array, default: () => [] }, breakpoints: { type: Array, default: () => [] }, checkpoints: { type: Array, default: () => [] }, busy: Boolean })
const emit = defineEmits(['control', 'inspect', 'update:breakpoints'])
const labels = { running: '运行中', paused: '已暂停', interrupted: '连接中断，可恢复', failed: '执行失败', ready: '等待发布', completed: '已完成', cancelled: '已取消' }
const nodeLabels = { agent: '理解与决策', refuse: '结束与拒答', invalid_tool: '校验工具', fallback: '执行兼容能力', rag_answer: '据源回答', search_pdf: '检索资料', read_page: '读取页面', quote_source: '查找引用', extract_table: '提取表格', analyze_chart: '分析图表', respond_to_user: '核验交流回复', create_task: '建立任务', retrieve_evidence: '查找课程证据', generate_question_drafts: '生成题目', validate_question_drafts: '校验题目', save_question_drafts: '保存题目', prepare_paper_draft: '准备试卷', validate_paper_draft: '校验试卷', save_paper_draft: '保存试卷', build_study_plan: '制定复习计划', execute_study_step: '执行复习步骤', assess_study_progress: '检查作答反馈', replan_study_goal: '调整复习计划', save_study_questions: '保存自测题', finish_study_goal: '核验完成条件' }
const trace = computed(() => {
  const items = new Map()
  for (const event of props.execution?.events || []) items.set(event.id, { ...items.get(event.id), ...event })
  return [...items.values()]
})
const resumable = computed(() => ['paused', 'interrupted', 'failed', 'ready'].includes(props.execution?.status))
const active = computed(() => props.execution && !['completed', 'cancelled'].includes(props.execution.status))
function toggle(node, checked) {
  emit('update:breakpoints', checked ? [...new Set([...props.breakpoints, node])] : props.breakpoints.filter((item) => item !== node))
}
</script>

<template>
  <details class="execution-panel" aria-label="Agent 执行控制">
    <summary>
      <span class="panel-title"><span class="disclosure-icon" aria-hidden="true">▶</span>执行过程</span>
      <span class="panel-status"><span v-if="execution" role="status">{{ labels[execution.status] || execution.status }}</span><span v-else>可设置节点断点</span><span class="expand-label">展开</span><span class="collapse-label">收起</span></span>
    </summary>
    <div v-if="execution" class="run-info">
      <small>执行 {{ execution.id }}</small>
      <details class="checkpoint-history"><summary @click="emit('inspect')">检查点历史（只读）</summary>
        <p v-if="execution.checkpoint_id"><small>当前检查点 {{ execution.checkpoint_id }}</small></p>
        <ol><li v-for="point in checkpoints" :key="point.id"><small>步骤 {{ point.step }} · {{ point.id }}</small><pre>{{ JSON.stringify(point.summary, null, 2) }}</pre></li></ol>
      </details>
      <p v-if="execution.next_nodes?.length">下一节点：{{ execution.next_nodes.map((node) => nodeLabels[node] || node).join('、') }}</p>
      <p v-if="execution.control === 'pause' && execution.status === 'running'">暂停已请求，当前节点结束后停下。</p>
      <p v-if="execution.control === 'cancel' && execution.status === 'running'">取消已请求，当前节点结束后停止后续执行。</p>
      <div v-if="active" class="controls">
        <button v-if="execution.status === 'running'" :disabled="execution.control !== 'run'" @click="emit('control', 'pause')">暂停</button>
        <template v-if="resumable">
          <button :disabled="busy" @click="emit('control', execution.status === 'failed' ? 'retry' : 'resume')">{{ execution.status === 'failed' ? '从检查点重试' : '继续' }}</button>
          <button :disabled="busy" @click="emit('control', 'step')">单步执行</button>
        </template>
        <button :disabled="execution.control === 'cancel'" @click="emit('control', 'cancel')">取消执行</button>
      </div>
      <ol class="timeline" aria-label="节点时间线">
        <li v-for="event in trace" :key="event.id" :data-status="event.status">
          <div><strong>{{ nodeLabels[event.node] || event.node }}</strong><span>{{ labels[event.status] || event.status }}<template v-if="event.duration_ms != null"> · {{ event.duration_ms }} ms</template></span></div>
          <details><summary>查看输入与输出摘要</summary><pre>{{ JSON.stringify({ input: event.input, output: event.output, error: event.error }, null, 2) }}</pre></details>
        </li>
      </ol>
    </div>
    <details class="breakpoints"><summary>节点断点（执行前暂停） · {{ breakpoints.length }}</summary>
      <div class="node-options"><label v-for="node in nodes" :key="node"><input type="checkbox" :checked="breakpoints.includes(node)" @change="toggle(node, $event.target.checked)">{{ nodeLabels[node] || node }}</label></div>
      <button v-if="active" @click="emit('control', 'breakpoints')">应用到当前执行</button>
    </details>
    <p class="hint">暂停在节点之间生效，继续沿用原检查点。输入与输出仅显示脱敏摘要。</p>
  </details>
</template>

<style scoped>
.execution-panel { flex-shrink: 0; margin: 8px 16px; padding: 10px 14px; border: 1px solid var(--sz-border); border-radius: 10px; max-height: 40vh; overflow: auto; font-size: 13px; }
summary { cursor: pointer; }
.execution-panel > summary { display: flex; justify-content: space-between; gap: 8px; color: var(--sz-accent); }
.execution-panel > summary::-webkit-details-marker { display: none; }
.execution-panel > summary:focus-visible { outline: 2px solid var(--sz-accent); outline-offset: 4px; border-radius: 3px; }
.panel-title, .panel-status { display: flex; align-items: center; gap: 8px; }
.panel-status { flex-wrap: wrap; justify-content: flex-end; }
.disclosure-icon { font-size: 10px; }
.execution-panel[open] > summary .disclosure-icon { transform: rotate(90deg); }
.expand-label, .collapse-label { font-size: 12px; color: var(--sz-text-muted); }
.collapse-label, .execution-panel[open] > summary .expand-label { display: none; }
.execution-panel[open] > summary .collapse-label { display: inline; }
.run-info small { display: block; margin: 10px 0; overflow-wrap: anywhere; color: var(--sz-text-muted); }
.run-info p { margin: 7px 0; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
button { padding: 5px 12px; border: 1px solid var(--sz-border); border-radius: 6px; background: var(--sz-bg-surface); color: var(--sz-text); cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.timeline { list-style: none; padding: 0 0 0 12px; border-left: 2px solid var(--sz-accent); }
.timeline li { padding: 5px 0 8px 8px; }
.timeline li > div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px; }
.timeline span, .hint { color: var(--sz-text-muted); }
.timeline [data-status="failed"] { color: var(--sz-warning); }
.timeline pre, .checkpoint-history pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; }
.breakpoints { margin: 8px 0; }
.node-options { display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 10px 0; }
.node-options label { display: flex; align-items: center; gap: 4px; }
.hint { margin: 7px 0 0; }
</style>
