import { describe, expect, it, vi } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import CitationList from './CitationList.vue'
vi.stubGlobal('window', { location: { origin: 'http://localhost' } })

describe('citation evidence', () => {
  it('distinguishes model selected evidence from unverified retrieval candidates', async () => {
    const selected = await renderToString(createSSRApp(CitationList, { citations: [{ source_file: '讲义', evidence_role: 'model_selected', evidence_index: 3 }] }))
    expect(selected).toContain('模型选用的资料')
    expect(selected).toMatch(/class="citation-index"[^>]*>3<\/span>/)
    const legacy = await renderToString(createSSRApp(CitationList, { citations: [{ source_file: '旧引用' }] }))
    expect(legacy).toContain('检索候选资料')
    expect(legacy).toContain('未标明使用来源')
    expect(legacy).not.toContain('查看回答依据')
  })
  it('shows evidence selection metadata and a course scoped page link', async () => {
    const html = await renderToString(createSSRApp(CitationList, { courseId: 'math', citations: [{
      doc_id: '7', source_file: '讲义.pdf', page: 3, snippet: '正文', source_version: '2',
      effective_from: '2026-01-01', effective_to: '2026-12-31', applicability_scope: '考试',
      authority_label: '课程资料', selection_reason: '版本=2',
    }] }))
    for (const text of ['版本：2', '2026-01-01', '2026-12-31', '考试', '课程资料', '版本=2']) expect(html).toContain(text)
    expect(html).toContain('course_id=math')
    expect(html).toContain('#page=3')
  })
  it('does not treat sentinel dates or missing metadata as verified facts', async () => {
    const html = await renderToString(createSSRApp(CitationList, { citations: [{
      source_file: '旧资料', snippet: '正文', effective_from: '0001-01-01', effective_to: '9999-12-31',
    }] }))
    expect(html).toContain('生效期未标注')
    expect(html).not.toContain('9999-12-31')
    expect(html).not.toContain('正确率')
  })
})
