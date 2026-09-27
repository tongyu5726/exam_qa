const select = (values) => values.map((value) => ({ label: value, value }))
const f = (key, label, type = 'text', more = {}) => ({ key, label, type, ...more })

export const groups = [
  { id: 'llm', title: '模型管理', fields: [f('max_tokens', '最大输出 Token', 'number'), f('timeout', '超时（秒）', 'number')] },
  { id: 'embedding', title: 'Embedding', fields: [f('provider', 'Provider', 'select', { options: select(['local', 'openai']) }), f('model', '模型 ID'), f('base_url', 'Base URL', 'text', { remoteOnly: true }), f('api_key', 'API Key', 'secret', { remoteOnly: true }), f('timeout', '超时（秒）', 'number', { remoteOnly: true })] },
  { id: 'retrieval', title: '检索', fields: [f('top_k', '融合后召回条数', 'number'), f('score_threshold', '拒答分数阈值', 'number', { step: 0.01 }), f('rerank_enabled', '启用 BGE 精排', 'boolean'), f('rerank_model', '精排模型'), f('rerank_candidates', '精排候选池', 'number'), f('rerank_top_n', '精排保留条数（0 = top_k）', 'number')] },
  { id: 'chunk', title: '分块', fields: [f('chunk_size', '分块大小', 'number'), f('chunk_overlap', '重叠大小', 'number')] },
  { id: 'parsing', title: '解析 / OCR', fields: [
    f('pdf_parser', 'PDF 解析器', 'select', { options: select(['auto', 'pymupdf', 'mineru', 'markpdfdown']) }),
    f('pdf_use_ocr', '启用 OCR', 'boolean'), f('pdf_force_ocr', '强制 OCR', 'boolean'), f('pdf_ocr_language', 'OCR 语言'),
    f('pdf_quality_threshold', 'PDF 质量阈值', 'number', { step: 0.01 }),
    f('mineru_cmd', 'MinerU 命令'), f('mineru_timeout', 'MinerU 超时（秒，0 = 不限）', 'number'), f('mineru_backend', 'MinerU Backend'),
    f('mineru_effort', 'MinerU Effort', 'select', { options: select(['medium', 'high']) }), f('mineru_lang', 'MinerU 语言'), f('mineru_formula', 'MinerU 公式', 'boolean'), f('mineru_table', 'MinerU 表格', 'boolean'), f('mineru_image_analysis', 'MinerU 图片分析', 'boolean'), f('mineru_retry_high', '低质量时重试 high', 'boolean'),
    f('formula_recognition_enabled', '启用公式识别', 'boolean'), f('formula_recognition_device', '公式识别设备', 'select', { options: select(['auto', 'cpu', 'gpu']) }), f('formula_recognition_model', '公式识别模型'), f('formula_recognition_enable_mkldnn', '启用 MKLDNN', 'boolean'),
    f('markpdfdown_enabled', '启用 MarkPDFDown', 'boolean'), f('markpdfdown_cmd', 'MarkPDFDown 命令'), f('markpdfdown_args', 'MarkPDFDown 参数'), f('markpdfdown_timeout', 'MarkPDFDown 超时（秒）', 'number'),
    f('visual_model', '视觉模型'), f('visual_base_url', '视觉模型 Base URL'), f('visual_api_key', '视觉模型 API Key', 'secret', { configuredKey: 'visual_configured' }), f('visual_timeout', '视觉模型超时（秒）', 'number'),
  ] },
  { id: 'proxy', title: '代理', fields: [f('enabled', '启用通用代理', 'boolean'), f('url', '通用代理地址'), f('hf_url', 'Hugging Face 专用代理地址'), f('github_url', 'GitHub 专用代理地址'), f('no_proxy', 'NO_PROXY')] },
  { id: 'app', title: '上传 / 日志', fields: [f('max_upload_mb', '最大上传 MB', 'number'), f('log_level', '日志等级', 'select', { options: select(['DEBUG', 'INFO', 'WARNING', 'ERROR']) })] },
  { id: 'server', title: '服务', fields: [f('host', 'Host'), f('port', 'Port', 'number')] },
]
