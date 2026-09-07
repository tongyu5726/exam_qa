import { apiGet, apiDelete, apiUploadWithProgress } from "../../shared/js/api.js";
import { getCourseId, initShell, toast } from "../../shared/js/shell.js";

const ACCEPT = ".pdf,.txt,.md,.doc,.docx,.pptx";
const PROCESS_STEPS = ["解析中…", "分块中…", "向量化中…", "写入资料库…"];

let uploadProgress = null;

function updateMessage(payload, fallback) {
  const updates = payload?.updates || (payload?.update ? [payload.update] : []);
  if (!updates.length) return fallback;
  const counts = updates.reduce((all, item) => {
    all[item.action] = (all[item.action] || 0) + 1;
    return all;
  }, {});
  const parts = [];
  if (counts.updated) parts.push(`更新 ${counts.updated} 份`);
  if (counts.created) parts.push(`新增 ${counts.created} 份`);
  if (counts.unchanged) parts.push(`跳过 ${counts.unchanged} 份未变化资料`);
  if (counts.failed) parts.push(`失败 ${counts.failed} 份`);
  return parts.length ? parts.join("；") : fallback;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function ensureProgressUi(zone) {
  let root = zone.querySelector(".sz-upload-progress");
  if (!root) {
    root = document.createElement("div");
    root.className = "sz-upload-progress";
    root.hidden = true;
    root.innerHTML = `
      <div class="sz-upload-progress-meta">
        <span class="sz-upload-progress-label"></span>
        <span class="sz-upload-progress-pct"></span>
      </div>
      <div class="sz-upload-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100">
        <div class="sz-upload-progress-bar"></div>
      </div>
    `;
    zone.appendChild(root);
  }
  uploadProgress = {
    root,
    bar: root.querySelector(".sz-upload-progress-bar"),
    label: root.querySelector(".sz-upload-progress-label"),
    pct: root.querySelector(".sz-upload-progress-pct"),
    track: root.querySelector(".sz-upload-progress-track"),
  };
  return uploadProgress;
}

function setBusy(zone, busy) {
  const input = zone.querySelector("#sz-file-input");
  const label = zone.querySelector(".sz-upload-btn");
  const scanBtn = zone.querySelector("#sz-scan-btn");
  if (input) input.disabled = busy;
  if (label) label.classList.toggle("is-busy", busy);
  if (scanBtn) scanBtn.disabled = busy;
}

function showProgress(zone, { phase, ratio, filename, label }) {
  const ui = ensureProgressUi(zone);
  ui.root.hidden = false;
  clearInterval(ui.timer);
  ui.timer = undefined;

  if (phase === "upload") {
    const pct = Math.round((ratio ?? 0) * 100);
    ui.root.classList.remove("is-indeterminate");
    ui.bar.style.width = `${pct}%`;
    ui.track.setAttribute("aria-valuenow", String(pct));
    ui.label.textContent =
      label || (filename ? `上传 ${filename}` : "上传中…");
    ui.pct.textContent = `${pct}%`;
    return;
  }

  ui.root.classList.add("is-indeterminate");
  ui.bar.style.width = "40%";
  ui.track.removeAttribute("aria-valuenow");
  ui.pct.textContent = "";
  if (label) {
    ui.label.textContent = label;
    return;
  }
  let i = 0;
  ui.label.textContent = PROCESS_STEPS[0];
  ui.timer = window.setInterval(() => {
    i = (i + 1) % PROCESS_STEPS.length;
    ui.label.textContent = PROCESS_STEPS[i];
  }, 1600);
}

function hideProgress(zone) {
  const ui = uploadProgress || ensureProgressUi(zone);
  clearInterval(ui.timer);
  ui.timer = undefined;
  ui.root.hidden = true;
  ui.root.classList.remove("is-indeterminate");
  ui.bar.style.width = "0%";
  ui.pct.textContent = "";
}

const STATUS_LABEL = {
  done: "已入库",
  failed: "失败",
  pending: "待处理",
  processing: "处理中",
};

const PROVIDER_LABEL = { openai: "OpenAI 兼容", local: "本地模型" };

let configEmbedding = { provider: "", model: "", configured: null };
let storedDim = null;

function statusLabel(status) {
  return STATUS_LABEL[status] || status || "未知";
}

function providerLabel(provider) {
  const p = String(provider || "").toLowerCase();
  return PROVIDER_LABEL[p] || provider || "";
}

function renderEmbeddingRail() {
  const dimEl = document.getElementById("sz-rail-dim");
  const dimHint = document.getElementById("sz-rail-dim-hint");
  const modelEl = document.getElementById("sz-rail-model");
  const providerEl = document.getElementById("sz-rail-provider");
  const summaryEl = document.getElementById("sz-emb-summary");

  if (dimEl) {
    dimEl.textContent = storedDim != null ? `${storedDim} 维` : "暂无向量";
  }
  if (dimHint) {
    dimHint.textContent =
      storedDim != null
        ? "已写入资料库的向量维度"
        : "集合为空或尚未写入向量";
  }

  const model = configEmbedding.model || "";
  const provider = configEmbedding.provider || "";
  if (modelEl) modelEl.textContent = model || "—";
  if (providerEl) {
    if (!provider && !model) {
      providerEl.textContent = "未能读取配置";
    } else if (configEmbedding.configured === false && provider === "openai") {
      providerEl.textContent = `${providerLabel(provider)} · 未设置 API Key`;
    } else {
      providerEl.textContent = providerLabel(provider) || provider;
    }
  }

  if (summaryEl) {
    const bits = [provider, model].filter(Boolean);
    if (storedDim != null) bits.push(`${storedDim} 维`);
    summaryEl.textContent = bits.join(" · ");
  }
}

function updateCourseStats(items) {
  const docs = items || [];
  const docEl = document.getElementById("sz-rail-doc-count");
  const chunkEl = document.getElementById("sz-rail-chunk-count");
  const doneEl = document.getElementById("sz-rail-done-count");
  if (docEl) docEl.textContent = String(docs.length);
  if (chunkEl) {
    const n = docs.reduce((s, d) => s + (Number(d.chunk_count) || 0), 0);
    chunkEl.textContent = String(n);
  }
  if (doneEl) {
    doneEl.textContent = String(docs.filter((d) => d.status === "done").length);
  }
}

async function refreshConfigEmbedding() {
  try {
    const data = await apiGet("/config");
    const emb = data?.embedding || {};
    configEmbedding = {
      provider: emb.provider || "",
      model: emb.model || "",
      configured: emb.configured,
    };
  } catch {
    /* ignore */
  }
  renderEmbeddingRail();
}

async function refreshDocs() {
  const box = document.getElementById("sz-docs-list");
  if (!box) return;

  const courseId = getCourseId();
  if (!courseId) {
    box.innerHTML = `<p class="sz-docs-empty">请先选择课程</p>`;
    storedDim = null;
    updateCourseStats([]);
    renderEmbeddingRail();
    return;
  }

  try {
    const data = await apiGet("/documents", { course_id: courseId });
    const items = data?.items || [];
    const emb = data?.embedding || {};
    if (emb.provider || emb.model) {
      configEmbedding = {
        provider: emb.provider || configEmbedding.provider,
        model: emb.model || configEmbedding.model,
        configured: configEmbedding.configured,
      };
    }
    storedDim = emb.dim != null ? Number(emb.dim) : null;
    if (Number.isNaN(storedDim)) storedDim = null;
    renderEmbeddingRail();
    updateCourseStats(items);

    if (!items.length) {
      box.innerHTML = `<p class="sz-docs-empty">暂无资料，上传或扫描后显示在此</p>`;
      return;
    }
    box.innerHTML = items
      .map((d) => {
        const status = d.status || "";
        const sourceUrl = `/api/v1/documents/${encodeURIComponent(String(d.id))}/source?course_id=${encodeURIComponent(courseId)}`;
        const dimTag =
          status === "done" && storedDim != null
            ? `<span class="sz-doc-tag" data-kind="dim">入库维度 ${storedDim}</span>`
            : status === "done"
              ? `<span class="sz-doc-tag" data-kind="dim">入库维度 —</span>`
              : "";
        return `<article class="sz-doc-card" data-id="${d.id}">
          <div class="sz-doc-meta">
            <span class="sz-doc-name">${escapeHtml(d.filename || String(d.id))}</span>
            <div class="sz-doc-tags">
              <span class="sz-doc-tag" data-kind="status" data-status="${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>
              ${
                d.chunk_count != null
                  ? `<span class="sz-doc-tag">${escapeHtml(String(d.chunk_count))} 块</span>`
                  : ""
              }
              ${dimTag}
            </div>
          </div>
          <div class="sz-doc-card-actions">
            <a class="sz-doc-open" href="${sourceUrl}" target="_blank" rel="noopener" title="在新窗口查看源文档">查看源文档</a>
            <button type="button" class="sz-doc-del" data-del="${d.id}">删除</button>
          </div>
        </article>`;
      })
      .join("");
  } catch (err) {
    box.innerHTML = `<p class="sz-docs-empty">${escapeHtml(err.message || "加载失败")}</p>`;
    toast(err.message || "加载资料失败", "error");
  }
}

function renderClassification(data) {
  const box = document.getElementById("sz-classify-result");
  if (!box) return;
  const groups = data?.groups || [];
  const dimension = data?.dimension || "type";
  if (!groups.length) {
    box.innerHTML = `<p class="sz-docs-empty">暂无资料可分类</p>`;
    return;
  }

  const heading =
    dimension === "chapter"
      ? `${escapeHtml(String(data.total_chunks ?? 0))} 块 · ${groups.length} 个章节`
      : `${groups.length} 类 · ${data.total ?? 0} 份资料`;

  box.innerHTML = `
    <p class="sz-classify-summary">${heading}</p>
    <div class="sz-classify-groups">
      ${groups
        .map((g) => {
          if (dimension === "chapter") {
            const files = (g.source_files || [])
              .map((f) => `<li>${escapeHtml(f)}</li>`)
              .join("");
            return `<article class="sz-classify-group">
              <div class="sz-classify-group-head">
                <span class="sz-classify-name">${escapeHtml(g.chapter || "未分类")}</span>
                <span class="sz-classify-count">${Number(g.chunk_count) || 0} 块</span>
              </div>
              <ul class="sz-classify-files">${files || "<li>暂无来源文件</li>"}</ul>
            </article>`;
          }
          const docs = g.documents || [];
          const items = docs
            .map(
              (d) =>
                `<li>${escapeHtml(d.filename || String(d.id))}${
                  d.chunk_count != null
                    ? `<span class="sz-classify-count">${Number(d.chunk_count) || 0} 块</span>`
                    : ""
                }</li>`,
            )
            .join("");
          return `<article class="sz-classify-group">
            <div class="sz-classify-group-head">
              <span class="sz-classify-name">${escapeHtml(g.label || g.type || "其他")}</span>
              <span class="sz-classify-count">${docs.length} 份</span>
            </div>
            <ul class="sz-classify-files">${items || "<li>暂无资料</li>"}</ul>
          </article>`;
        })
        .join("")}
    </div>`;
}

async function refreshClassification() {
  const box = document.getElementById("sz-classify-result");
  if (!box) return;
  const courseId = getCourseId();
  if (!courseId) {
    box.innerHTML = `<p class="sz-docs-empty">请先选择课程</p>`;
    return;
  }
  const select = document.getElementById("sz-classify-by");
  const by = select?.value || "type";
  try {
    const data = await apiGet("/documents/summary", {
      course_id: courseId,
      by,
    });
    renderClassification(data);
  } catch (err) {
    box.innerHTML = `<p class="sz-docs-empty">${escapeHtml(err.message || "加载分类失败")}</p>`;
    toast(err.message || "加载分类失败", "error");
  }
}

function setupClassification() {
  const select = document.getElementById("sz-classify-by");
  if (select) select.addEventListener("change", () => refreshClassification());
}

async function refreshDocsAndClassification() {
  await refreshDocs();
  await refreshClassification();
}

function setupUploadZone() {
  const zone = document.getElementById("sz-upload-zone");
  if (!zone) return;

  zone.innerHTML = `
    <div class="sz-docs-actions">
      <label class="sz-upload-btn">
        上传资料
        <input type="file" id="sz-file-input" accept="${ACCEPT}" hidden />
      </label>
      <button type="button" id="sz-scan-btn">扫描目录</button>
      <label class="sz-muted" title="忽略 mtime，整课重入库以补写 chapter 等元数据">
        <input type="checkbox" id="sz-scan-force" />
        强制重建
      </label>
    </div>
    <p class="sz-muted sz-upload-hint">PDF / TXT / MD / DOC / DOCX / PPTX · 章节概览需强制重建或重新上传</p>
  `;
  ensureProgressUi(zone);

  const input = document.getElementById("sz-file-input");
  const scanBtn = document.getElementById("sz-scan-btn");
  const forceEl = document.getElementById("sz-scan-force");

  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;

    const courseId = getCourseId();
    if (!courseId) {
      toast("请先选择课程", "error");
      return;
    }

    const fd = new FormData();
    fd.append("file", file);
    fd.append("course_id", courseId);
    setBusy(zone, true);
    showProgress(zone, { phase: "upload", ratio: 0, filename: file.name });
    try {
      const result = await apiUploadWithProgress("/documents", fd, (ev) => {
        if (ev.phase === "upload") {
          showProgress(zone, {
            phase: "upload",
            ratio: ev.ratio,
            filename: file.name,
          });
        } else {
          showProgress(zone, { phase: "processing" });
        }
      });
      toast(updateMessage(result, "上传成功"), "success");
      await refreshDocsAndClassification();
    } catch (err) {
      toast(err.message || "上传失败", "error");
    } finally {
      hideProgress(zone);
      setBusy(zone, false);
    }
  });

  scanBtn.addEventListener("click", async () => {
    const courseId = getCourseId();
    if (!courseId) {
      toast("请先选择课程", "error");
      return;
    }
    const force = !!forceEl?.checked;
    if (
      force &&
      !window.confirm(
        "强制重建将重新解析并向量化本课全部资料（可补写章节元数据），耗时更长。继续？"
      )
    ) {
      return;
    }
    const fd = new FormData();
    fd.append("course_id", courseId);
    if (force) fd.append("force", "true");
    setBusy(zone, true);
    showProgress(zone, {
      phase: "processing",
      label: force ? "强制重建中…" : "扫描目录中…",
    });
    try {
      const result = await apiUploadWithProgress("/documents/scan", fd, (ev) => {
        if (ev.phase === "processing") {
          showProgress(zone, {
            phase: "processing",
            label: force ? "强制重建 · 入库中…" : "扫描 · 入库中…",
          });
        }
      });
      toast(updateMessage(result, force ? "强制重建完成" : "扫描完成"), "success");
      await refreshDocsAndClassification();
    } catch (err) {
      toast(err.message || "扫描失败", "error");
    } finally {
      hideProgress(zone);
      setBusy(zone, false);
    }
  });
}

function setupDocList() {
  const box = document.getElementById("sz-docs-list");
  if (!box) return;
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-del]");
    if (!btn) return;
    const id = btn.getAttribute("data-del");
    if (!id || !confirm("确认删除该资料？")) return;

    const row = btn.closest(".sz-doc-card");
    btn.disabled = true;
    row?.remove();
    if (!box.querySelector(".sz-doc-card")) {
      box.innerHTML = `<p class="sz-docs-empty">暂无资料，上传或扫描后显示在此</p>`;
    }

    try {
      const courseId = getCourseId();
      if (!courseId) {
        toast("请先选择课程", "error");
        await refreshDocsAndClassification();
        return;
      }
      await apiDelete(`/documents/${id}`, { course_id: courseId });
      toast("已删除", "success");
      await refreshDocsAndClassification();
    } catch (err) {
      toast(err.message || "删除失败", "error");
      await refreshDocsAndClassification();
    }
  });
}

function bindCourseChange() {
  const collegeSel = document.getElementById("sz-college");
  const courseSel = document.getElementById("sz-course");
  if (!collegeSel || !courseSel) return;

  const shellCollege = collegeSel.onchange;
  const shellCourse = courseSel.onchange;

  collegeSel.onchange = async (ev) => {
    if (shellCollege) await shellCollege.call(collegeSel, ev);
    await refreshDocsAndClassification();
  };
  courseSel.onchange = (ev) => {
    if (shellCourse) shellCourse.call(courseSel, ev);
    refreshDocsAndClassification();
  };
}

async function main() {
  await initShell({ active: "docs" });
  setupUploadZone();
  setupDocList();
  bindCourseChange();
  setupClassification();
  loadHitokotoQuotes();
  await refreshConfigEmbedding();
  await refreshDocsAndClassification();
}

const HITOKOTO_URLS = [
  "https://v1.hitokoto.cn/?c=i&encode=json&max_length=36",
  "https://v1.hitokoto.cn/?c=d&c=k&encode=json&max_length=36",
];

function formatHitokotoCite(data) {
  const from = (data.from || "").trim();
  const who = (data.from_who || "").trim();
  if (from && who) return `— ${who} · 《${from}》`;
  if (from) return `— 《${from}》`;
  if (who) return `— ${who}`;
  return "— 一言";
}

function wrapQuote(text) {
  const t = String(text || "").trim();
  if (!t) return "";
  if (t.startsWith("「") || t.startsWith("『") || t.startsWith('"')) return t;
  return `「${t}」`;
}

function quoteKey(data) {
  return String(data?.uuid || data?.hitokoto || "").trim();
}

async function fetchHitokoto(url, exclude = new Set()) {
  for (let i = 0; i < 5; i++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);
    try {
      const sep = url.includes("?") ? "&" : "?";
      const res = await fetch(`${url}${sep}_=${Date.now()}-${i}`, {
        signal: ctrl.signal,
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`hitokoto ${res.status}`);
      const data = await res.json();
      const key = quoteKey(data);
      if (!key || exclude.has(key)) continue;
      return data;
    } finally {
      clearTimeout(timer);
    }
  }
  throw new Error("hitokoto exhausted");
}

function applyHitokoto(card, data) {
  const epigraph = card.querySelector(".sz-rail-epigraph");
  const cite = card.querySelector(".sz-rail-cite");
  const text = wrapQuote(data.hitokoto);
  if (!text || !epigraph || !cite) return false;
  epigraph.textContent = text;
  cite.textContent = formatHitokotoCite(data);
  card.dataset.hitokotoKey = quoteKey(data);
  return true;
}

function otherHitokotoKeys(exceptCard) {
  return new Set(
    [...document.querySelectorAll("[data-hitokoto]")]
      .filter((c) => c !== exceptCard)
      .map((c) => c.dataset.hitokotoKey)
      .filter(Boolean),
  );
}

async function loadHitokotoQuotes() {
  const cards = [...document.querySelectorAll("[data-hitokoto]")];
  if (!cards.length) return;
  const used = new Set();
  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    try {
      const data = await fetchHitokoto(
        HITOKOTO_URLS[i % HITOKOTO_URLS.length],
        used,
      );
      if (!applyHitokoto(card, data)) continue;
      used.add(quoteKey(data));
      card.title = "来自一言 · 点击换一句";
      card.style.cursor = "pointer";
      if (!card.dataset.hitokotoBound) {
        card.dataset.hitokotoBound = "1";
        card.addEventListener("click", () => refreshOneHitokoto(card));
      }
    } catch {
      /* keep HTML fallback */
    }
  }
}

async function refreshOneHitokoto(card) {
  const idx = [...document.querySelectorAll("[data-hitokoto]")].indexOf(card);
  try {
    const data = await fetchHitokoto(
      HITOKOTO_URLS[Math.max(0, idx) % HITOKOTO_URLS.length],
      otherHitokotoKeys(card),
    );
    applyHitokoto(card, data);
  } catch {
    /* keep current */
  }
}

main();
