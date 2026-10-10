<script setup>
defineProps({ calls: { type: Array, default: () => [] }, agentic: Boolean, hasTask: Boolean })
const labels = { respond_to_user: '回应交流或澄清需求', create_task: '建立课程任务', assess_study_progress: '检查作答并决定后续安排', build_study_plan: '规划复习步骤', execute_study_step: '执行复习步骤', replan_study_goal: '根据反馈调整计划', save_study_questions: '保存本轮自测题', finish_study_goal: '检查完成条件', rag_answer: '检索并据源回答', search_pdf: '搜索资料', read_page: '读取原文页面', extract_table: '提取表格', analyze_chart: '复核图表', quote_source: '定位引用', retrieve_evidence: '检索有效证据', generate_question_drafts: '生成题目草稿', validate_question_drafts: '校验题目依据', save_question_drafts: '保存题目', prepare_paper_draft: '按蓝图准备试卷', validate_paper_draft: '校验试卷与依据', save_paper_draft: '保存试卷' }
</script>

<template>
  <details v-if="calls.length" class="tool-trace">
    <summary>查看执行记录（{{ calls.length }} 次工具调用）</summary>
    <p>{{ agentic ? 'Agent 工具决策' : hasTask ? '任务工具决策中断' : 'RAG 兼容路径' }}</p>
    <ol><li v-for="(call, index) in calls" :key="index"><strong>{{ labels[call.name] || '课程工具' }}</strong> · {{ call.ok ? '已执行' : '执行失败' }}<p v-if="call.error" role="status">{{ call.error }}</p></li></ol>
  </details>
</template>

<style scoped>
.tool-trace { margin-top: 14px; font-size: 13px; color: var(--sz-text-muted); }
.tool-trace summary { cursor: pointer; }
.tool-trace li { padding-block: 4px; }
.tool-trace p { margin-block: 6px; }
</style>
