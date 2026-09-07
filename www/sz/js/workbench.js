import { apiAskStream } from "../../shared/js/api.js";
import { getCourseId, initShell, toast } from "../../shared/js/shell.js";
import { enableColumnResize } from "../../shared/js/resize.js";
import {
  appendTurn,
  createConversation,
  getActiveConversationId,
  getConversation,
  listConversations,
  removeConversation,
  setActiveConversationId,
} from "../../shared/js/conversations.js";

const ASK_MODE_KEY = "sz.ask_mode";
const ASK_MODES = new Set(["auto", "qa", "concept", "chapter"]);

function getAskMode() {
  const m = localStorage.getItem(ASK_MODE_KEY);
  return ASK_MODES.has(m) ? m : "auto";
}

function setAskMode(mode) {
  localStorage.setItem(ASK_MODE_KEY, ASK_MODES.has(mode) ? mode : "auto");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function cleanAnswerForDisplay(answer, cites = []) {
  const sources = (cites || [])
    .map((c) => String(c.source_file || "").replace(/\.(pdf|txt|md|docx?|pptx)$/i, ""))
    .filter(Boolean);
  return String(answer || "")
    .replace(/\s*【([^】]{1,500})】/g, (full, label) => {
      if (/正文证据|公式证据/.test(label)) return "";
      return sources.some((source) => label.includes(source)) ? "" : full;
    })
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function renderCitations(el, cites) {
  if (!el) return;
  if (!cites?.length) {
    el.innerHTML = "";
    return;
  }
  const courseId = getCourseId();
  const cards = cites
    .map((c, i) => {
      const page = c.page != null ? `第 ${escapeHtml(c.page)} 页` : "页码未标注";
      const kind = c.block_type === "formula" ? "公式" : "正文";
      const section = c.section_path || c.chapter || "";
      const sourceUrl = c.doc_id
        ? `/api/v1/documents/${encodeURIComponent(String(c.doc_id))}/source?course_id=${encodeURIComponent(courseId)}${
            c.page != null ? `#page=${encodeURIComponent(String(c.page))}` : ""
          }`
        : "";
      return `<article class="sz-evidence-card">
        <div class="sz-evidence-card-head">
          <span class="sz-evidence-index">${i + 1}</span>
          <div class="sz-evidence-title">
            <strong>${escapeHtml(c.source_file || "未知资料")}</strong>
            <span>${page}${section ? ` · ${escapeHtml(section)}` : ""}</span>
          </div>
          <span class="sz-evidence-kind">${kind}</span>
        </div>
        <p class="sz-evidence-snippet">${escapeHtml(c.snippet || "暂无摘要")}</p>
        ${
          sourceUrl
            ? `<a class="sz-evidence-open" href="${sourceUrl}" target="_blank" rel="noopener">打开源文档 <span aria-hidden="true">↗</span></a>`
            : ""
        }
      </article>`;
    })
    .join("");
  el.innerHTML = `<details class="sz-evidence-portal">
    <summary>
      <span class="sz-evidence-portal-icon" aria-hidden="true">≡</span>
      <span>查看回答依据</span>
      <span class="sz-evidence-count">${cites.length}</span>
      <span class="sz-evidence-chevron" aria-hidden="true">⌄</span>
    </summary>
    <div class="sz-evidence-list">${cards}</div>
  </details>`;
}

function renderAnswerMath(el) {
  if (!el || !window.renderMathInElement) return;
  window.renderMathInElement(el, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "$", right: "$", display: false },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
  });
}

function renderTurnHtml(turn) {
  const grounded = turn.grounded;
  const groundedAttr =
    grounded === false ? ` data-grounded="false"` : grounded === true ? ` data-grounded="true"` : "";
  const mode = ASK_MODES.has(turn.mode) ? turn.mode : "auto";
  return `<div class="sz-turn" data-mode="${mode}">
    <div class="sz-q"></div>
    <div class="sz-a"${groundedAttr}></div>
    <div class="sz-cites"></div>
  </div>`;
}

function modeLabel(mode) {
  if (mode === "auto") return "自动识别";
  if (mode === "concept") return "知识点";
  if (mode === "chapter") return "章节概览";
  return "自由问答";
}

function paintTurn(node, turn) {
  const q = node.querySelector(".sz-q");
  const mode = ASK_MODES.has(turn.mode) ? turn.mode : "auto";
  node.dataset.mode = mode;
  q.replaceChildren();
  const label = document.createElement("span");
  label.className = "sz-q-mode";
  label.textContent = modeLabel(mode);
  const text = document.createElement("span");
  text.className = "sz-q-text";
  text.textContent = turn.question || "";
  q.append(label, text);
  const a = node.querySelector(".sz-a");
  a.textContent = cleanAnswerForDisplay(turn.answer, turn.citations || []);
  if (turn.grounded === false) a.dataset.grounded = "false";
  else if (turn.grounded === true) a.dataset.grounded = "true";
  renderCitations(node.querySelector(".sz-cites"), turn.citations || []);
  renderAnswerMath(a);
}

function showAskEmpty() {
  const streamEl = document.getElementById("sz-ask-stream");
  if (!streamEl) return;
  const mode = getAskMode();
  if (mode === "concept") {
    streamEl.innerHTML =
      `<p class="sz-ask-empty">选择课程后输入知识点名称<br />将按「定义 → 公式 → 例题」聚合资料</p>`;
  } else if (mode === "chapter") {
    streamEl.innerHTML =
      `<p class="sz-ask-empty">选择课程后输入章节名，如：第3章 傅里叶变换<br />按章聚合资料生成概览。旧资料请到资料页勾选「强制重建」再扫描</p>`;
  } else {
    streamEl.innerHTML =
      `<p class="sz-ask-empty">选择课程后提问；左侧可切换历史对话<br />检索已启用向量 + BM25 混合召回</p>`;
  }
}

function renderConversationTurns(conv) {
  const streamEl = document.getElementById("sz-ask-stream");
  if (!streamEl) return;
  if (!conv?.turns?.length) {
    showAskEmpty();
    return;
  }
  streamEl.innerHTML = conv.turns.map((t) => renderTurnHtml(t)).join("");
  const nodes = streamEl.querySelectorAll(".sz-turn");
  conv.turns.forEach((t, i) => paintTurn(nodes[i], t));
  streamEl.scrollTop = streamEl.scrollHeight;
}

function refreshHistoryList() {
  const box = document.getElementById("sz-history-list");
  if (!box) return;
  const courseId = getCourseId();
  if (!courseId) {
    box.innerHTML = `<p class="sz-muted">请先选择课程</p>`;
    return;
  }
  const active = getActiveConversationId(courseId);
  const list = listConversations(courseId);
  if (!list.length) {
    box.innerHTML = `<p class="sz-muted">暂无对话，点击上方开始</p>`;
    return;
  }
  box.innerHTML = list
    .map(
      (c) => `<div class="sz-conv-row${c.id === active ? " is-active" : ""}" data-id="${c.id}" role="button" tabindex="0">
        <span class="sz-conv-title">${escapeHtml(c.title || "新对话")}</span>
        <button type="button" class="sz-conv-del" data-del="${c.id}" title="删除">删除</button>
      </div>`,
    )
    .join("");
}

function loadActiveConversation() {
  const courseId = getCourseId();
  if (!courseId) {
    showAskEmpty();
    refreshHistoryList();
    return;
  }
  let id = getActiveConversationId(courseId);
  let conv = id ? getConversation(courseId, id) : null;
  if (!conv) {
    const list = listConversations(courseId);
    conv = list[0] || null;
    if (conv) setActiveConversationId(courseId, conv.id);
  }
  refreshHistoryList();
  if (conv) renderConversationTurns(conv);
  else showAskEmpty();
}

function setupHistory() {
  const box = document.getElementById("sz-history-list");
  const newBtn = document.getElementById("sz-new-chat");
  if (!box || !newBtn) return;

  newBtn.addEventListener("click", () => {
    const courseId = getCourseId();
    if (!courseId) {
      toast("请先选择课程", "error");
      return;
    }
    createConversation(courseId);
    refreshHistoryList();
    showAskEmpty();
    document.getElementById("sz-ask-input")?.focus();
  });

  box.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      e.stopPropagation();
      const id = del.getAttribute("data-del");
      const courseId = getCourseId();
      if (!id || !courseId || !confirm("删除该对话？")) return;
      removeConversation(courseId, id);
      loadActiveConversation();
      return;
    }
    const row = e.target.closest(".sz-conv-row");
    if (!row) return;
    const id = row.getAttribute("data-id");
    const courseId = getCourseId();
    if (!id || !courseId) return;
    setActiveConversationId(courseId, id);
    refreshHistoryList();
    const conv = getConversation(courseId, id);
    renderConversationTurns(conv);
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
    loadActiveConversation();
  };
  courseSel.onchange = (ev) => {
    if (shellCourse) shellCourse.call(courseSel, ev);
    loadActiveConversation();
  };
}

function setupAsk() {
  const streamEl = document.getElementById("sz-ask-stream");
  const form = document.getElementById("sz-ask-form");
  const input = document.getElementById("sz-ask-input");
  const modeEl = document.getElementById("sz-ask-mode");
  const submitBtn = document.getElementById("sz-ask-submit");
  if (!streamEl || !form || !input || !modeEl) return;

  let asking = false;

  modeEl.value = getAskMode();
  const syncModeUi = () => {
  const mode = ASK_MODES.has(modeEl.value) ? modeEl.value : "auto";
    setAskMode(mode);
    if (mode === "concept") {
      input.placeholder = "输入知识点名称，如：卷积定理";
      if (submitBtn) submitBtn.textContent = "检索";
    } else if (mode === "chapter") {
      input.placeholder = "输入章节名，如：第3章 傅里叶变换";
      if (submitBtn) submitBtn.textContent = "概览";
    } else {
      input.placeholder = mode === "auto" ? "输入问题，自动识别章节、规则与场景…" : "输入问题…";
      if (submitBtn) submitBtn.textContent = "提问";
    }
    if (streamEl.querySelector(".sz-ask-empty")) showAskEmpty();
  };
  modeEl.addEventListener("change", syncModeUi);
  syncModeUi();

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (asking) return;

    const question = input.value.trim();
    const courseId = getCourseId();
    const mode = ASK_MODES.has(modeEl.value) ? modeEl.value : "auto";
    if (!question || !courseId) {
      toast("需要课程与问题", "error");
      return;
    }

    if (streamEl.querySelector(".sz-ask-empty")) {
      streamEl.replaceChildren();
    }

    let answer = "";
    let resolvedMode = mode;
    let resolvedTask = "qa";
    const turnWrap = document.createElement("div");
    turnWrap.className = "sz-turn";
    turnWrap.dataset.mode = mode;
    turnWrap.innerHTML =
      '<div class="sz-q"></div><div class="sz-a"></div><div class="sz-cites"></div>';
    paintTurn(turnWrap, { question, mode, answer: "", citations: [] });
    const live = turnWrap.querySelector(".sz-a");
    live.textContent = "";
    delete live.dataset.grounded;
    streamEl.appendChild(turnWrap);
    const citesEl = turnWrap.querySelector(".sz-cites");

    asking = true;
    if (submitBtn) submitBtn.disabled = true;
    modeEl.disabled = true;
    input.value = "";
    streamEl.scrollTop = streamEl.scrollHeight;

    let citations = [];
    let grounded = true;
    let finished = false;

    try {
      const convId = getActiveConversationId(courseId);
      await apiAskStream({ question, course_id: courseId, mode, conversation_id: convId || undefined }, (ev) => {
        if (ev.type === "phase") {
          if (ev.intent?.mode) resolvedMode = ev.intent.mode;
          if (ev.intent?.task) resolvedTask = ev.intent.task;
          live.dataset.phase = ev.phase || "";
          if (!answer) {
            live.textContent =
              ev.phase === "retrieving"
                ? mode === "chapter"
                  ? "聚合章节资料…"
                  : "检索中…"
                : ev.phase === "generating"
                  ? mode === "concept"
                    ? "聚合生成中…"
                    : mode === "chapter"
                      ? "生成概览中…"
                      : "生成中…"
                  : ev.phase === "reasoning"
                    ? "模型正在推理，请稍候…"
                  : ev.phase === "routing"
                    ? "正在转到我的题库…"
                  : "";
          }
        } else if (ev.type === "delta") {
          answer += ev.text || "";
          live.textContent = cleanAnswerForDisplay(answer);
          streamEl.scrollTop = streamEl.scrollHeight;
        } else if (ev.type === "done") {
          const d = ev.data || {};
          answer = d.answer || answer;
          live.textContent = cleanAnswerForDisplay(answer, d.citations || []);
          delete live.dataset.phase;
          grounded = !!d.grounded;
          live.dataset.grounded = String(grounded);
          citations = d.citations || [];
          renderCitations(citesEl, citations);
          resolvedTask = d.intent?.task || resolvedTask;
          if (resolvedTask === "question_generate") {
            const bankUrl = `/sz-bank/?topic=${encodeURIComponent(question)}`;
            citesEl.innerHTML = `<a class="sz-bank-route" href="${bankUrl}">打开我的题库 <span aria-hidden="true">→</span></a>`;
          }
          renderAnswerMath(live);
          streamEl.scrollTop = streamEl.scrollHeight;
          finished = true;
        } else if (ev.type === "error") {
          const msg = ev.message || "问答失败";
          live.textContent = msg;
          live.dataset.grounded = "false";
          toast(msg, "error");
        }
      });

      if (finished) {
        appendTurn(courseId, { question, answer, citations, grounded, mode: resolvedMode });
        refreshHistoryList();
      }
    } catch (err) {
      const msg = err.message || "问答失败";
      live.textContent = msg;
      live.dataset.grounded = "false";
      toast(msg, "error");
    } finally {
      asking = false;
      if (submitBtn) submitBtn.disabled = false;
      modeEl.disabled = false;
    }
  });
}

function setupHistoryCollapse() {
  const root = document.getElementById("sz-workbench");
  const collapseBtn = document.getElementById("sz-history-toggle");
  const expandBtn = document.getElementById("sz-history-expand");
  if (!root || !collapseBtn || !expandBtn) return;

  const KEY = "sz.history_collapsed";

  function apply(collapsed) {
    root.classList.toggle("is-history-collapsed", collapsed);
    collapseBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    collapseBtn.title = collapsed ? "展开侧栏" : "收起侧栏";
    expandBtn.hidden = !collapsed;
    localStorage.setItem(KEY, collapsed ? "1" : "0");
  }

  apply(localStorage.getItem(KEY) === "1");
  collapseBtn.addEventListener("click", () => apply(true));
  expandBtn.addEventListener("click", () => apply(false));
}

async function main() {
  await initShell({ active: "sz" });
  enableColumnResize({
    root: document.getElementById("sz-workbench"),
    cssVar: "--sz-docs-width",
    storageKey: "sz.history_width",
    min: 180,
    max: 360,
    defaultWidth: 240,
  });
  setupHistoryCollapse();
  setupHistory();
  bindCourseChange();
  setupAsk();
  loadActiveConversation();
}

main();
