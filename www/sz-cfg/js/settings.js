import { apiGet, apiPatch, apiPost, apiDelete } from "../../shared/js/api.js";
import { initShell, toast } from "../../shared/js/shell.js";
import { enableColumnResize } from "../../shared/js/resize.js";

const MASKED_SECRET = "***";

const GROUPS = [
  {
    id: "llm",
    title: "模型管理",
    fields: [
      { key: "max_tokens", label: "最大输出 Token（推理模型建议 8192）", type: "number" },
      { key: "timeout", label: "超时（秒）", type: "number" },
    ],
  },
  {
    id: "embedding",
    title: "Embedding",
    desc: "local：完整 HuggingFace 模型 ID（如 sentence-transformers/all-MiniLM-L6-v2、BAAI/bge-small-zh-v1.5）。openai：远程 Embedding API。",
    fields: [
      {
        key: "provider",
        label: "Provider",
        type: "select",
        options: ["local", "openai"],
      },
      {
        key: "model",
        label: "模型",
        placeholder: "sentence-transformers/all-MiniLM-L6-v2",
      },
      { key: "base_url", label: "Base URL", remoteOnly: true },
      { key: "api_key", label: "API Key", secret: true, remoteOnly: true },
      { key: "timeout", label: "超时", type: "number", remoteOnly: true },
    ],
  },
  {
    id: "retrieval",
    title: "检索",
    desc: "问答默认走向量 + BM25 → RRF；可开 BGE 精排。低于阈值则拒答（精排开启时阈值为 sigmoid(logit)）。",
    fields: [
      {
        key: "top_k",
        label: "融合后召回条数 (top_k)",
        type: "number",
      },
      {
        key: "score_threshold",
        label: "拒答分数阈值",
        type: "number",
        step: "0.01",
      },
      {
        key: "rerank_enabled",
        label: "启用 BGE 精排",
        type: "checkbox",
      },
      {
        key: "rerank_model",
        label: "精排模型",
      },
      {
        key: "rerank_candidates",
        label: "精排候选池大小",
        type: "number",
      },
      {
        key: "rerank_top_n",
        label: "精排保留条数 (0=同 top_k)",
        type: "number",
      },
    ],
  },
  {
    id: "chunk",
    title: "分块",
    fields: [
      { key: "chunk_size", label: "chunk_size", type: "number" },
      { key: "chunk_overlap", label: "overlap", type: "number" },
    ],
  },
  {
    id: "parsing",
    title: "解析 / OCR",
    fields: [
      { key: "pdf_use_ocr", label: "启用 OCR", type: "checkbox" },
      { key: "pdf_force_ocr", label: "强制 OCR", type: "checkbox" },
      { key: "pdf_ocr_language", label: "OCR 语言" },
      {
        key: "pdf_parser",
        label: "PDF 解析器",
        type: "select",
        options: ["auto", "pymupdf", "mineru"],
      },
      { key: "mineru_cmd", label: "MinerU 命令" },
      { key: "mineru_timeout", label: "MinerU 超时秒 (0=不限)", type: "number" },
      { key: "visual_model", label: "VLM 模型 (空=关闭图片理解)" },
      { key: "visual_base_url", label: "VLM Base URL (OpenAI 兼容)" },
      {
        key: "visual_api_key",
        label: "VLM API Key (留空复用 LLM Key)",
        secret: true,
        configuredKey: "visual_configured",
      },
      { key: "visual_timeout", label: "VLM 超时秒", type: "number" },
    ],
  },
  {
    id: "proxy",
    title: "代理",
    fields: [
      { key: "enabled", label: "启用", type: "checkbox" },
      { key: "url", label: "URL" },
      { key: "no_proxy", label: "NO_PROXY" },
    ],
  },
  {
    id: "app",
    title: "上传 / 日志",
    fields: [
      { key: "max_upload_mb", label: "最大上传 MB", type: "number" },
      {
        key: "log_level",
        label: "日志等级",
        type: "select",
        options: ["DEBUG", "INFO", "WARNING", "ERROR"],
      },
    ],
  },
  {
    id: "server",
    title: "服务",
    fields: [
      { key: "host", label: "Host" },
      { key: "port", label: "Port", type: "number" },
    ],
  },
];

const FORMAT_LABEL = {
  openai: "OpenAI 兼容",
  "openai-compatible": "OpenAI 兼容",
  local: "本地（Ollama 等）",
};

let cfg = null;
let activeId = GROUPS[0].id;
let filter = "";

const navEl = document.getElementById("sz-config-nav");
const mainEl = document.getElementById("sz-config-main");

async function boot() {
  await initShell({ active: "cfg", catalog: false });
  enableColumnResize({
    root: document.getElementById("sz-config"),
    cssVar: "--sz-cfg-nav-width",
    storageKey: "sz.cfg_nav_width",
    min: 160,
    max: 360,
    defaultWidth: 220,
  });
  await load();
}

boot().catch((err) => {
  console.error(err);
  if (mainEl) {
    mainEl.textContent = err?.message || "页面初始化失败";
  }
});

async function load() {
  try {
    cfg = await apiGet("/config");
    renderNav();
    renderMain();
  } catch (err) {
    toast(err.message || "加载配置失败", "error");
    if (navEl) {
      navEl.replaceChildren();
      const p = document.createElement("p");
      p.className = "sz-muted";
      p.textContent = err.message || "加载失败";
      navEl.appendChild(p);
    }
  }
}

function renderNav() {
  const q = filter.trim().toLowerCase();
  const items = GROUPS.filter(
    (g) =>
      !q ||
      g.id.includes(q) ||
      g.title.toLowerCase().includes(q) ||
      g.fields.some((f) => f.key.includes(q) || f.label.toLowerCase().includes(q)),
  );

  navEl.innerHTML = `
    <label class="sz-config-search">
      <span class="sz-sr-only">搜索分组</span>
      <input type="search" id="sz-config-filter" placeholder="搜索分组…" value="${esc(filter)}" />
    </label>
    <ul class="sz-config-list" role="listbox" aria-label="配置分组">
      ${items
        .map(
          (g) => `
        <li>
          <button type="button" class="sz-config-item${g.id === activeId ? " is-active" : ""}" data-group="${g.id}">
            <span class="sz-config-item-title">${esc(g.title)}</span>
            <span class="sz-config-item-id">${esc(g.id)}</span>
          </button>
        </li>`,
        )
        .join("")}
      ${items.length ? "" : `<li class="sz-muted">无匹配分组</li>`}
    </ul>
    <div class="sz-config-meta" id="sz-config-meta-nav"></div>
  `;

  renderMeta(document.getElementById("sz-config-meta-nav"));

  document.getElementById("sz-config-filter").oninput = (e) => {
    filter = e.target.value;
    renderNav();
  };

  navEl.querySelectorAll("[data-group]").forEach((btn) => {
    btn.onclick = () => {
      activeId = btn.dataset.group;
      renderNav();
      renderMain();
    };
  });
}

function renderMeta(el) {
  if (!el || !cfg) return;
  const storage = cfg.storage || {};
  const meta = cfg.meta || {};
  const writable = meta.env_writable;
  el.innerHTML = `
    <h3 class="sz-config-meta-title">只读信息</h3>
    <dl class="sz-config-meta-dl">
      <dt>knowledge_dir</dt>
      <dd>${esc(storage.knowledge_dir ?? "—")}</dd>
      <dt>config_path</dt>
      <dd>${esc(meta.config_path ?? "—")}</dd>
      <dt>env_writable</dt>
      <dd class="${writable ? "" : "sz-warn"}">${writable ? "是" : "否（只读）"}</dd>
    </dl>
  `;
}

function renderMain() {
  if (!cfg) {
    mainEl.innerHTML = `<p class="sz-muted">加载中…</p>`;
    return;
  }
  const group = GROUPS.find((g) => g.id === activeId) || GROUPS[0];
  if (group.id === "llm") {
    renderLlmMain(group);
    return;
  }
  if (group.id === "embedding") {
    renderEmbeddingMain(group);
    return;
  }
  const data = cfg[group.id] || {};

  mainEl.innerHTML = `
    <header class="sz-config-header">
      <h2>${esc(group.title)}</h2>
      <p class="sz-muted">${esc(group.desc || group.id)}</p>
    </header>
    <form class="sz-config-form" id="sz-config-form" autocomplete="off">
      ${group.fields.map((f) => fieldHtml(f, data)).join("")}
      <div class="sz-config-actions">
        <button type="submit" class="sz-btn sz-btn-primary" ${cfg.meta?.env_writable === false ? "disabled title=\"env 不可写\"" : ""}>保存</button>
        <button type="button" class="sz-btn" id="sz-config-reset">重置</button>
      </div>
    </form>
    <div class="sz-config-effects" id="sz-config-effects" hidden></div>
  `;

  const form = document.getElementById("sz-config-form");
  form.onsubmit = (e) => {
    e.preventDefault();
    save(group);
  };
  document.getElementById("sz-config-reset").onclick = () => renderMain();

  if (cfg.settings_effects) {
    showEffects(cfg.settings_effects);
  }
}

function renderLlmMain(group) {
  const data = cfg.llm || {};
  const providers = data.providers || [];
  const active = data.active || "";
  const formats = data.formats || ["openai", "local"];
  const showAdd = mainEl?.dataset.llmAdd === "1";

  const formatOpts = formats
    .map((f) => `<option value="${esc(f)}">${esc(FORMAT_LABEL[f] || f)}</option>`)
    .join("");

  const tableRows = providers.length
    ? providers
        .map((p) => {
          const isActive = p.name === active;
          const fmt = p.format || "openai";
          const fmtClass =
            fmt === "local" ? "is-local" : fmt.startsWith("openai") ? "is-openai" : "";
          return `<tr class="${isActive ? "is-active" : ""}" data-name="${esc(p.name)}">
            <td><strong>${esc(p.name)}</strong></td>
            <td><span class="sz-tag ${fmtClass}">${esc(FORMAT_LABEL[fmt] || fmt)}</span></td>
            <td class="sz-mono">${esc(p.model || "—")}</td>
            <td class="sz-llm-url-cell" title="${esc(p.base_url || "")}">${esc(p.base_url || "—")}</td>
            <td>${
              p.has_api_key
                ? '<span class="sz-tag is-ok">已配置</span>'
                : '<span class="sz-tag is-warn">未配置</span>'
            }</td>
            <td class="sz-llm-ops">
              ${
                isActive
                  ? '<span class="sz-tag is-active-badge">使用中</span>'
                  : `<button type="button" class="sz-link-btn" data-activate="${esc(p.name)}">设为当前</button>`
              }
              <button type="button" class="sz-btn sz-btn-danger sz-btn-sm" data-remove="${esc(p.name)}" ${
                isActive && providers.length === 1 ? "disabled" : ""
              }>删除</button>
            </td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="6" class="sz-muted sz-llm-empty">尚未注册模型，点击右上角添加</td></tr>`;

  mainEl.innerHTML = `
    <header class="sz-config-header sz-llm-header">
      <div>
        <h2>模型管理</h2>
        <p class="sz-muted">注册多个对话模型，切换后立即写入当前配置</p>
      </div>
      <button type="button" class="sz-btn sz-btn-primary" id="sz-llm-toggle-add">${
        showAdd ? "收起表单" : "+ 添加模型"
      }</button>
    </header>

    <div class="sz-llm-tip" role="note">
      活跃模型会同步到 <code>.env</code> 的 <code>LLM_*</code> / <code>LLM_PROVIDER</code>。本地 Ollama 可将 API Key 留空。
    </div>

    <section class="sz-llm-card">
      <div class="sz-llm-table-wrap">
        <table class="sz-llm-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>格式</th>
              <th>模型</th>
              <th>Base URL</th>
              <th>Key</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="sz-llm-list">${tableRows}</tbody>
        </table>
      </div>
    </section>

    <section class="sz-llm-card${showAdd ? "" : " is-collapsed"}" id="sz-llm-add-panel">
      <h3>注册新模型</h3>
      <form class="sz-config-form sz-llm-form" id="sz-llm-add" autocomplete="off">
        <label class="sz-field">
          <span class="sz-field-label">名称</span>
          <input type="text" name="name" required placeholder="如 deepseek、qwen" />
        </label>
        <label class="sz-field">
          <span class="sz-field-label">类型</span>
          <select name="format">${formatOpts}</select>
        </label>
        <label class="sz-field">
          <span class="sz-field-label">模型 ID</span>
          <input type="text" name="model" required placeholder="如 deepseek-chat、gpt-4o-mini" />
        </label>
        <label class="sz-field">
          <span class="sz-field-label">Base URL</span>
          <input type="text" name="base_url" placeholder="https://api.deepseek.com/v1" />
        </label>
        <label class="sz-field">
          <span class="sz-field-label">API Key</span>
          <input type="password" name="api_key" placeholder="local 类型可留空" autocomplete="new-password" />
        </label>
        <div class="sz-config-actions">
          <button type="submit" class="sz-btn sz-btn-primary">注册</button>
          <button type="button" class="sz-btn" id="sz-llm-add-cancel">取消</button>
        </div>
      </form>
    </section>

    <section class="sz-llm-card">
      <h3>生成参数</h3>
      <form class="sz-config-form sz-llm-form" id="sz-config-form" autocomplete="off">
        ${group.fields.map((f) => fieldHtml(f, data)).join("")}
        <div class="sz-config-actions">
          <button type="submit" class="sz-btn sz-btn-primary" ${
            cfg.meta?.env_writable === false ? "disabled" : ""
          }>保存超时</button>
        </div>
      </form>
    </section>
    <div class="sz-config-effects" id="sz-config-effects" hidden></div>
  `;

  document.getElementById("sz-llm-toggle-add")?.addEventListener("click", () => {
    mainEl.dataset.llmAdd = showAdd ? "0" : "1";
    renderLlmMain(group);
  });
  document.getElementById("sz-llm-add-cancel")?.addEventListener("click", () => {
    mainEl.dataset.llmAdd = "0";
    renderLlmMain(group);
  });
  document.getElementById("sz-llm-list")?.addEventListener("click", onLlmListClick);
  document.getElementById("sz-llm-add").onsubmit = (e) => {
    e.preventDefault();
    addLlmProvider(e.target);
  };
  document.getElementById("sz-config-form").onsubmit = (e) => {
    e.preventDefault();
    save(group);
  };
  if (cfg.settings_effects) showEffects(cfg.settings_effects);
}

async function onLlmListClick(e) {
  const act = e.target.closest("[data-activate]");
  const del = e.target.closest("[data-remove]");
  if (act) {
    try {
      const data = await apiPost("/llm-providers/active", {
        name: act.getAttribute("data-activate"),
      });
      cfg.llm = {
        ...cfg.llm,
        active: data.active,
        providers: data.items,
        model: data.items?.find((p) => p.name === data.active)?.model || cfg.llm.model,
      };
      toast(`已切换为 ${data.active}`, "success");
      renderMain();
      await load();
    } catch (err) {
      toast(err.message || "切换失败", "error");
    }
    return;
  }
  if (del) {
    const name = del.getAttribute("data-remove");
    if (!name || !confirm(`删除模型「${name}」？`)) return;
    try {
      const data = await apiDelete(`/llm-providers/${encodeURIComponent(name)}`);
      cfg.llm = { ...cfg.llm, active: data.active, providers: data.items };
      toast("已删除", "success");
      await load();
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  }
}

async function addLlmProvider(form) {
  const fd = new FormData(form);
  const payload = {
    name: String(fd.get("name") || "").trim(),
    format: String(fd.get("format") || "openai").trim(),
    model: String(fd.get("model") || "").trim(),
    base_url: String(fd.get("base_url") || "").trim(),
    api_key: String(fd.get("api_key") || "").trim(),
  };
  try {
    await apiPost("/llm-providers", payload);
    toast(`已注册 ${payload.name}`, "success");
    form.reset();
    if (mainEl) mainEl.dataset.llmAdd = "0";
    await load();
  } catch (err) {
    toast(err.message || "注册失败", "error");
  }
}

function renderEmbeddingMain(group) {
  const data = cfg.embedding || {};
  const provider = data.provider || "local";
  const isLocal = provider === "local";

  mainEl.innerHTML = `
    <header class="sz-config-header">
      <h2>${esc(group.title)}</h2>
      <p class="sz-muted">${esc(group.desc || group.id)}</p>
    </header>
    <form class="sz-config-form" id="sz-config-form" autocomplete="off">
      ${group.fields
        .filter((f) => !f.remoteOnly || !isLocal)
        .map((f) => fieldHtml(f, data))
        .join("")}
      <div class="sz-config-actions">
        <button type="submit" class="sz-btn sz-btn-primary" ${
          cfg.meta?.env_writable === false ? "disabled title=\"env 不可写\"" : ""
        }>保存</button>
        <button type="button" class="sz-btn" id="sz-config-reset">重置</button>
        <button type="button" class="sz-btn" id="sz-emb-warmup">
          ${isLocal ? "拉取并加载模型" : "探测连通性"}
        </button>
      </div>
    </form>
    <div class="sz-emb-warmup" id="sz-emb-warmup-panel" hidden>
      <div class="sz-upload-progress" id="sz-emb-progress">
        <div class="sz-upload-progress-meta">
          <span class="sz-upload-progress-label" id="sz-emb-progress-label">待命</span>
          <span class="sz-upload-progress-pct" id="sz-emb-progress-pct"></span>
        </div>
        <div class="sz-upload-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" id="sz-emb-progress-track">
          <div class="sz-upload-progress-bar" id="sz-emb-progress-bar"></div>
        </div>
      </div>
      <p class="sz-muted sz-emb-warmup-hint" id="sz-emb-warmup-hint"></p>
    </div>
    <div class="sz-config-effects" id="sz-config-effects" hidden></div>
  `;

  const form = document.getElementById("sz-config-form");
  form.onsubmit = (e) => {
    e.preventDefault();
    save(group);
  };
  document.getElementById("sz-config-reset").onclick = () => renderMain();
  const providerEl = form.elements.namedItem("provider");
  if (providerEl) {
    providerEl.onchange = () => {
      const next = providerEl.value;
      if (cfg.embedding) cfg.embedding.provider = next;
      renderEmbeddingMain(group);
    };
  }
  document.getElementById("sz-emb-warmup").onclick = () => startEmbeddingWarmup(isLocal);
  if (cfg.settings_effects) showEffects(cfg.settings_effects);
  refreshEmbeddingStatus();
}

let _embPollTimer = null;

function setEmbProgress({ phase, percent, message, error }) {
  const panel = document.getElementById("sz-emb-warmup-panel");
  const root = document.getElementById("sz-emb-progress");
  const bar = document.getElementById("sz-emb-progress-bar");
  const label = document.getElementById("sz-emb-progress-label");
  const pct = document.getElementById("sz-emb-progress-pct");
  const hint = document.getElementById("sz-emb-warmup-hint");
  const track = document.getElementById("sz-emb-progress-track");
  const btn = document.getElementById("sz-emb-warmup");
  if (!panel || !root || !bar) return;

  panel.hidden = false;
  const running = phase === "running";
  if (btn) btn.disabled = running;

  if (label) label.textContent = message || (phase === "ok" ? "已就绪" : "…");
  if (hint) {
    hint.textContent =
      error ||
      (phase === "ok"
        ? "模型已就绪，可在资料页扫描入库。"
        : phase === "running"
          ? "首次拉取可能较大，请保持网络畅通。"
          : "");
    hint.classList.toggle("sz-warn", !!error);
  }

  const known = percent != null && Number.isFinite(Number(percent));
  root.classList.toggle("is-indeterminate", running && !known);
  if (known) {
    const p = Math.max(0, Math.min(100, Number(percent)));
    bar.style.width = `${p}%`;
    if (pct) pct.textContent = `${Math.round(p)}%`;
    if (track) {
      track.setAttribute("aria-valuenow", String(Math.round(p)));
    }
  } else if (phase === "ok") {
    bar.style.width = "100%";
    if (pct) pct.textContent = "100%";
  } else if (running) {
    bar.style.width = "40%";
    if (pct) pct.textContent = "…";
  } else if (phase === "error") {
    bar.style.width = "0%";
    if (pct) pct.textContent = "";
  } else {
    bar.style.width = "0%";
    if (pct) pct.textContent = "";
  }
}

async function refreshEmbeddingStatus() {
  try {
    const data = await apiGet("/embedding/status");
    const w = data.warmup || {};
    if (w.phase && w.phase !== "idle") {
      setEmbProgress(w);
    } else if (data.status === "ok") {
      setEmbProgress({
        phase: "ok",
        percent: 100,
        message: `已就绪 · ${data.model || ""}`,
        error: null,
      });
    }
    if (w.phase === "running") {
      scheduleEmbPoll();
    }
  } catch {
    /* 状态接口失败时不打扰 */
  }
}

function scheduleEmbPoll() {
  if (_embPollTimer) return;
  _embPollTimer = setInterval(async () => {
    try {
      const data = await apiGet("/embedding/status");
      const w = data.warmup || {};
      const done =
        data.status === "ok" || w.phase === "ok" || w.phase === "error";
      if (data.status === "ok") {
        setEmbProgress({
          phase: "ok",
          percent: 100,
          message: w.message || `已就绪 · ${data.model || ""}`,
          error: null,
        });
      } else {
        setEmbProgress(w);
      }
      if (done) {
        clearInterval(_embPollTimer);
        _embPollTimer = null;
        if (data.status === "ok" || w.phase === "ok") {
          toast("Embedding 已就绪", "success");
        }
        if (w.phase === "error" && data.status !== "ok") {
          toast(w.error || "加载失败", "error");
        }
      }
    } catch (err) {
      clearInterval(_embPollTimer);
      _embPollTimer = null;
      setEmbProgress({
        phase: "error",
        percent: null,
        message: "轮询失败",
        error: err.message || "轮询失败",
      });
    }
  }, 300);
}

async function startEmbeddingWarmup(isLocal) {
  const group = GROUPS.find((g) => g.id === "embedding");
  if (group && cfg.meta?.env_writable !== false) {
    try {
      await save(group, { quiet: true });
    } catch {
      return;
    }
  }
  setEmbProgress({
    phase: "running",
    percent: isLocal ? 0 : null,
    message: isLocal ? "开始拉取…" : "探测中…",
    error: null,
  });
  try {
    const data = await apiPost("/embedding/warmup", {});
    const w = data.warmup || {};
    if (data.status === "ok" || w.phase === "ok") {
      setEmbProgress({
        phase: "ok",
        percent: 100,
        message: w.message || `已就绪 · ${data.model || ""}`,
        error: null,
      });
      toast("向量化已就绪", "success");
      return;
    }
    setEmbProgress(w);
    scheduleEmbPoll();
  } catch (err) {
    setEmbProgress({
      phase: "error",
      percent: null,
      message: "启动失败",
      error: err.message || "启动失败",
    });
    toast(err.message || "拉取失败", "error");
  }
}

function fieldHtml(f, data) {
  const id = `field-${f.key}`;
  if (f.type === "checkbox") {
    const checked = !!data[f.key];
    return `
      <label class="sz-field sz-field-check">
        <input type="checkbox" id="${id}" name="${f.key}" ${checked ? "checked" : ""} ${f.readonly ? "disabled" : ""} />
        <span>${esc(f.label)}${f.readonly ? " <span class=\"sz-muted\">（只读）</span>" : ""}</span>
      </label>`;
  }

  if (f.secret) {
    const configured = !!data[f.configuredKey || "configured"];
    const ph = configured ? "已配置" : "未配置";
    return `
      <label class="sz-field">
        <span class="sz-field-label">${esc(f.label)}</span>
        <input type="password" id="${id}" name="${f.key}" value="" placeholder="${esc(ph)}" autocomplete="new-password" />
      </label>`;
  }

  if (f.type === "select") {
    const val = data[f.key] ?? "";
    const opts = (f.options || [])
      .map((o) => `<option value="${esc(o)}" ${o === val ? "selected" : ""}>${esc(o)}</option>`)
      .join("");
    return `
      <label class="sz-field">
        <span class="sz-field-label">${esc(f.label)}</span>
        <select id="${id}" name="${f.key}">${opts}</select>
      </label>`;
  }

  const type = f.type === "number" ? "number" : "text";
  const step = f.step ? ` step="${esc(f.step)}"` : "";
  const val = data[f.key] ?? "";
  const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : "";
  return `
    <label class="sz-field">
      <span class="sz-field-label">${esc(f.label)}</span>
      <input type="${type}" id="${id}" name="${f.key}" value="${esc(String(val))}"${step}${ph} />
    </label>`;
}

async function save(group, opts = {}) {
  const quiet = !!opts.quiet;
  const form = document.getElementById("sz-config-form");
  const payload = {};

  for (const f of group.fields) {
    if (f.readonly) continue;
    const el = form.elements.namedItem(f.key);
    if (!el) continue;

    if (f.type === "checkbox") {
      payload[f.key] = el.checked;
      continue;
    }

    if (f.secret) {
      const raw = String(el.value || "").trim();
      if (!raw || raw === MASKED_SECRET) continue;
      payload[f.key] = raw;
      continue;
    }

    let v = el.value;
    if (f.type === "number") {
      if (v === "" || v == null) continue;
      v = Number(v);
      if (Number.isNaN(v)) {
        toast(`${f.label} 不是有效数字`, "error");
        throw new Error("invalid number");
      }
    }
    payload[f.key] = v;
  }

  if (!Object.keys(payload).length) {
    if (!quiet) toast("没有可保存的更改", "error");
    return;
  }

  try {
    const data = await apiPatch("/config", { [group.id]: payload });
    cfg = data;
    if (!quiet) {
      toast("已保存", "success");
      renderNav();
      renderMain();
      if (data.settings_effects) showEffects(data.settings_effects);
    } else if (group.id === "embedding" && cfg.embedding) {
      Object.assign(cfg.embedding, payload);
    }
  } catch (err) {
    toast(err.message || "保存失败", "error");
    throw err;
  }
}

function showEffects(effects) {
  const el = document.getElementById("sz-config-effects");
  if (!el || !effects) return;
  const hot = effects.hot_reload || [];
  const restart = effects.restart_required || [];
  const notes = effects.notes || [];
  if (!hot.length && !restart.length && !notes.length) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.innerHTML = `
    <h3>生效说明</h3>
    ${hot.length ? `<p>热更新：${esc(hot.join("、"))}</p>` : ""}
    ${restart.length ? `<p class="sz-warn">需重启：${esc(restart.join("、"))}</p>` : ""}
    ${notes.map((n) => `<p class="sz-muted">${esc(n)}</p>`).join("")}
  `;
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
