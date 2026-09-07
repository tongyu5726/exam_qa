import { apiGet, apiPost } from "../../shared/js/api.js";
import { getCourseId, initShell, toast } from "../../shared/js/shell.js";

let questions = [];
let selected = new Set();

function tag(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el;
}

function empty(message) {
  const el = document.createElement("p");
  el.className = "bank-empty";
  el.textContent = message;
  return el;
}

function updateSelection() {
  const el = document.getElementById("bank-selection");
  if (el) el.textContent = selected.size ? `已勾选 ${selected.size} 道题，每题默认 1 分` : "尚未勾选题目";
}

function renderQuestions() {
  const root = document.getElementById("bank-questions");
  const count = document.getElementById("bank-question-count");
  root.replaceChildren();
  count.textContent = `${questions.length} 道题`;
  if (!questions.length) {
    root.append(empty("当前课程还没有题目。填写上方主题后，Agent 会根据资料生成草稿。"));
    return;
  }
  for (const question of questions) {
    const article = document.createElement("article");
    article.className = "bank-question";
    const top = document.createElement("div");
    top.className = "bank-question-top";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = selected.has(question.id);
    check.setAttribute("aria-label", "加入试卷");
    check.onchange = () => {
      if (check.checked) selected.add(question.id); else selected.delete(question.id);
      updateSelection();
    };
    const content = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = question.stem;
    content.append(title);
    if (question.options?.length) {
      const options = document.createElement("ol");
      options.className = "bank-options";
      for (const option of question.options) {
        const li = document.createElement("li"); li.textContent = option; options.append(li);
      }
      content.append(options);
    }
    const answer = document.createElement("p");
    answer.textContent = `答案：${question.answer}`;
    content.append(answer);
    if (question.analysis) {
      const analysis = document.createElement("p"); analysis.textContent = `解析：${question.analysis}`; content.append(analysis);
    }
    const tags = document.createElement("div"); tags.className = "bank-tags";
    tags.append(tag(question.question_type), tag(question.difficulty), tag(question.status));
    if (question.chapter) tags.append(tag(question.chapter));
    if (question.citations?.length) tags.append(tag(`${question.citations.length} 条资料依据`));
    content.append(tags);
    top.append(check, content); article.append(top); root.append(article);
  }
}

function renderPapers(papers) {
  const root = document.getElementById("bank-papers");
  const count = document.getElementById("bank-paper-count");
  root.replaceChildren(); count.textContent = `${papers.length} 份试卷`;
  if (!papers.length) { root.append(empty("尚未保存试卷。勾选题目并填写试卷名称即可创建。")); return; }
  for (const paper of papers) {
    const article = document.createElement("article"); article.className = "bank-paper";
    const title = document.createElement("h3"); title.textContent = paper.title;
    const meta = document.createElement("p"); meta.textContent = `${paper.question_count} 道题 · ${paper.total_score} 分`;
    article.append(title, meta);
    if (paper.description) { const desc = document.createElement("p"); desc.textContent = paper.description; article.append(desc); }
    root.append(article);
  }
}

async function loadAll() {
  const courseId = getCourseId();
  if (!courseId) return;
  try {
    const [loadedQuestions, papers] = await Promise.all([
      apiGet("/question-bank/questions", { course_id: courseId }),
      apiGet("/question-bank/papers", { course_id: courseId }),
    ]);
    questions = loadedQuestions;
    selected = new Set([...selected].filter((id) => questions.some((question) => question.id === id)));
    renderQuestions(); renderPapers(papers); updateSelection();
  } catch (err) { toast(err.message || "读取题库失败", "error"); }
}

function bindForms() {
  document.getElementById("bank-generate-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const courseId = getCourseId();
    if (!courseId) { toast("请先选择课程", "error"); return; }
    const button = document.getElementById("bank-generate"); button.disabled = true; button.textContent = "正在检索资料并出题…";
    try {
      const result = await apiPost("/question-bank/generate", {
        course_id: courseId, topic: document.getElementById("bank-topic").value.trim(),
        chapter: document.getElementById("bank-chapter").value.trim(), question_type: document.getElementById("bank-type").value,
        difficulty: document.getElementById("bank-difficulty").value, count: Number(document.getElementById("bank-count").value),
      });
      if (!result.grounded) toast("资料不足，未生成或保存题目", "error");
      else { toast(`已保存 ${result.questions.length} 道题目草稿`); await loadAll(); }
    } catch (err) { toast(err.message || "出题失败", "error"); }
    finally { button.disabled = false; button.textContent = "生成并存入题库"; }
  });

  document.getElementById("bank-paper-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const courseId = getCourseId();
    if (!courseId || !selected.size) { toast("请先勾选至少一道题", "error"); return; }
    try {
      await apiPost("/question-bank/papers", {
        course_id: courseId, title: document.getElementById("bank-paper-title").value.trim(),
        description: document.getElementById("bank-paper-description").value.trim(),
        items: [...selected].map((question_id) => ({ question_id, score: 1 })),
      });
      selected.clear(); document.getElementById("bank-paper-form").reset();
      toast("试卷已保存到我的题库"); await loadAll();
    } catch (err) { toast(err.message || "保存试卷失败", "error"); }
  });

  document.getElementById("bank-assemble-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const courseId = getCourseId();
    if (!courseId) { toast("请先选择课程", "error"); return; }
    const button = document.getElementById("bank-assemble");
    button.disabled = true; button.textContent = "正在校验蓝图并组卷…";
    try {
      const result = await apiPost("/question-bank/papers/assemble", {
        course_id: courseId, title: document.getElementById("bank-assemble-title").value.trim(),
        topic: document.getElementById("bank-assemble-topic").value.trim(),
        rules: [{
          chapter: document.getElementById("bank-assemble-chapter").value.trim(),
          question_type: document.getElementById("bank-assemble-type").value,
          difficulty: document.getElementById("bank-assemble-difficulty").value,
          count: Number(document.getElementById("bank-assemble-count").value),
          score: Number(document.getElementById("bank-assemble-score").value),
        }],
        allow_generate: document.getElementById("bank-assemble-generate").checked,
      });
      document.getElementById("bank-assemble-form").reset();
      toast(`试卷已保存：复用 ${result.reused_count} 题，补生成 ${result.generated_count} 题，共 ${result.total_score} 分`);
      await loadAll();
    } catch (err) { toast(err.message || "组卷失败", "error"); }
    finally { button.disabled = false; button.textContent = "按蓝图保存试卷"; }
  });
}

await initShell({ active: "bank" });
bindForms();
const routedTopic = new URLSearchParams(window.location.search).get("topic");
if (routedTopic) {
  const topicInput = document.getElementById("bank-topic");
  if (topicInput) topicInput.value = routedTopic;
}
document.getElementById("sz-course")?.addEventListener("change", loadAll);
await loadAll();
