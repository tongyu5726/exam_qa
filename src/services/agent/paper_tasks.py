"""Blueprint paper tasks: prepare without writes, validate, then atomically save."""
from __future__ import annotations

import re
from src.exceptions import BadRequestException
from src.models import PaperAssembleRequest
from src.services import question_bank, rag
from src.services.citations import build_citation
from .tasks import _NUMBERS, validate_drafts

_TYPES = {"选择": "choice", "填空": "fill_blank", "简答": "short_answer"}
_N = r"[0-9]+|[一二两三四五六七八九十]"
_RULE = re.compile(rf"(?:(?P<before>{_N})\s*道\s*(?P<type1>选择|填空|简答)题?|(?P<type2>选择|填空|简答)题?[：:\s]*(?P<after>{_N})\s*(?:道|题))")
_SCORE = re.compile(r"每题\s*([0-9]+(?:\.[0-9]+)?)\s*分")


def update_plan(task, question):
    spec = task["spec"]
    matches = list(_RULE.finditer(question))
    rules = []
    scores = _SCORE.findall(question)
    for i, match in enumerate(matches):
        number = match["before"] or match["after"]
        count = int(number) if number.isdigit() else _NUMBERS[number]
        segment = question[match.end():matches[i+1].start() if i+1 < len(matches) else len(question)]
        score = _SCORE.search(segment)
        difficulty = spec["difficulty"]
        for label, value in (("简单", "easy"), ("容易", "easy"), ("中等", "medium"), ("困难", "hard"), ("高难", "hard")):
            if label in segment:
                difficulty = value
        rules.append({"question_type": _TYPES[match["type1"] or match["type2"]], "count": count,
                      "difficulty": difficulty, "chapter": spec["chapter"],
                      "score": float(score[1]) if score else float(scores[0]) if len(scores) == 1 else None})
    if rules:
        spec["rules"] = rules
    elif len(scores) == 1:
        for rule in spec.get("rules", []):
            rule["score"] = float(scores[0])
    title = re.search(r"(?:标题|名称|命名为)[：:\s]*[“\"]?([^，。！\"”]{1,200})", question)
    spec["title"] = title[1].strip() if title else spec.get("title") or (spec["topic"][:196] + "练习卷" if spec["topic"] else "")
    if re.search(r"(?:不|不要|禁止|不必)(?:允许)?补题|只(?:从)?(?:用|复用)题库", question):
        spec["allow_generate"] = False
    elif re.search(r"补题|补齐|补足|允许生成", question):
        spec["allow_generate"] = True
    else:
        spec.setdefault("allow_generate", False)
    total = re.search(r"总分[：:\s]*([0-9]+(?:\.[0-9]+)?)\s*分?", question)
    if total:
        spec["expected_total"] = float(total[1])
    rules = spec.get("rules", [])
    spec["count"] = sum(rule["count"] for rule in rules)
    spec["total_score"] = sum(rule["count"] * (rule["score"] or 0) for rule in rules)
    missing = [] if spec["topic"] else ["章节或知识点"]
    if not rules:
        missing.append("组卷蓝图，例如：2道选择题，每题5分；1道简答题，每题10分")
    if rules and (spec["count"] > 10 or any(not 1 <= r["count"] <= 10 for r in rules)):
        missing.append("试卷题数须为1—10")
    if any(r["score"] is None or not 0 < r["score"] <= 100 for r in rules):
        missing.append("各题型每题分值（大于0且不超过100分）")
    if spec.get("expected_total") is not None and abs(spec["expected_total"] - spec["total_score"]) > 1e-6:
        missing.append(f"蓝图合计{spec['total_score']:g}分，与要求总分不一致；请调整题数或每题分值")
    task.update(missing=missing, status="waiting_input" if missing else "running")
    return task


def _request(task):
    spec = task["spec"]
    return PaperAssembleRequest(course_id=task["course_id"], title=spec["title"], topic=spec["topic"],
        rules=spec["rules"], scenario=task.get("scenario"), as_of=task.get("as_of"),
        allow_generate=spec["allow_generate"])


def _matching_ids(question, evidence):
    ids = []
    for citation in question.get("citations", []):
        snippet = str(citation.get("snippet") or "").strip()
        for hit in evidence:
            meta = hit.get("metadata") or {}
            if (snippet and snippet in hit["text"] and citation.get("source_file") == meta.get("source_file")
                    and citation.get("page") == meta.get("page")
                    and (not citation.get("doc_id") or str(citation["doc_id"]) == str(meta.get("doc_id")))):
                ids.append(hit["id"])
    return list(dict.fromkeys(ids))


def execute(name, task, *, vs, llm, bank):
    request = _request(task)
    if name == "retrieve_evidence":
        hits = rag._retrieve(task["spec"]["chapter"] or request.topic,
            "chapter" if task["spec"]["chapter"] else "qa", vs, request.course_id,
            scenario=request.scenario, as_of=request.as_of)
        task.update(evidence=hits, drafts=[], paper_items=[], rejected_reuse_ids=[], validated=False, issues=[], completed_steps=[])
        if not hits:
            task["status"] = "no_evidence"
        result = {"evidence": [{"id": h["id"], "text": h["text"]} for h in hits], "next": "prepare_paper_draft"}
    elif name == "prepare_paper_draft":
        if not task["evidence"]:
            raise BadRequestException("必须先取证，才能选择或生成试卷题目")
        hits = {h["id"]: h for h in task["evidence"]}
        serial = 0

        def generate(*, request):
            nonlocal serial
            drafts = question_bank.draft_questions(request=request, hits=task["evidence"], llm=llm, require_evidence_ids=True)
            for draft in drafts:
                ids = draft.get("evidence_ids")
                if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in hits for i in ids):
                    raise BadRequestException("补题必须引用当前有效证据 ID")
                serial += 1
                draft.update(id=f"draft_{serial}", course_id=request.course_id, question_type=request.question_type,
                    difficulty=request.difficulty, chapter=request.chapter, status="draft", origin="agent",
                    scenario=request.scenario or "", as_of=request.as_of or "",
                    citations=[build_citation(hits[i], scenario=request.scenario, as_of=request.as_of) for i in dict.fromkeys(ids)])
            return {"questions": drafts, "grounded": True}

        prepared = question_bank.prepare_paper(request=request, store=bank, generate=generate,
            evidence_check=lambda q: q["id"] not in task.get("rejected_reuse_ids", [])
                                     and question_bank._question_has_usable_evidence(q, request.scenario, request.as_of)
                                     and bool(_matching_ids(q, task["evidence"])))
        for item in prepared["items"]:
            draft = item["question"]
            if not draft["id"].startswith("draft_"):
                draft.update(reuse_id=draft["id"], reuse_updated_at=draft["updated_at"],
                             evidence_ids=_matching_ids(draft, task["evidence"]))
            draft["paper_score"] = item["score"]
            draft["citations"] = [build_citation(hits[i], scenario=request.scenario, as_of=request.as_of) for i in draft["evidence_ids"]]
        task.update(paper_items=prepared["items"], drafts=[i["question"] for i in prepared["items"]],
                    assembly={k: prepared[k] for k in ("reused_count", "generated_count")}, validated=False, issues=[])
        task["completed_steps"] = [n for n in task["completed_steps"] if n not in ("validate_paper_draft", "save_paper_draft")]
        result = {"drafts": task["drafts"], "assembly": task["assembly"], "total_score": task["spec"]["total_score"], "next": "validate_paper_draft"}
    elif name == "validate_paper_draft":
        if not task["drafts"]:
            raise BadRequestException("必须先准备试卷草稿")
        issues = validate_drafts(task["drafts"], task["evidence"], course_id=request.course_id,
            expected_count=task["spec"]["count"], llm=llm, bank=bank,
            reused_ids={d["reuse_id"] for d in task["drafts"] if d.get("reuse_id")})
        offset = 0
        for rule in request.rules:
            group = task["paper_items"][offset:offset+rule.count]
            if len(group) != rule.count or any(i["question"]["question_type"] != rule.question_type or
                i["question"]["difficulty"] != rule.difficulty or i["score"] != rule.score or
                (rule.chapter and i["question"]["chapter"] != rule.chapter) for i in group):
                issues.append("题型、难度、章节或分值不符合组卷蓝图")
            offset += rule.count
        if abs(sum(i["score"] for i in task["paper_items"]) - task["spec"]["total_score"]) > 1e-6:
            issues.append("试卷总分不符合蓝图")
        task.update(validated=not issues, issues=issues)
        if issues:
            task["rejected_reuse_ids"] = list(set(task.get("rejected_reuse_ids", [])) |
                {d["reuse_id"] for d in task["drafts"] if d.get("reuse_id")})
        if not issues and not task["spec"]["save_requested"]:
            task["status"] = "completed"
        result = {"valid": not issues, "issues": issues, "next": "prepare_paper_draft" if issues else "save_paper_draft"}
    elif name == "save_paper_draft":
        if not task["spec"]["save_requested"] or not task["validated"] or not task["drafts"]:
            raise BadRequestException("只有用户授权且完成校验的试卷才能保存")
        current = rag._retrieve(task["spec"]["chapter"] or request.topic,
            "chapter" if task["spec"]["chapter"] else "qa", vs, request.course_id,
            scenario=request.scenario, as_of=request.as_of)
        current = {h["id"]: (h["text"], h.get("metadata")) for h in current}
        used = {i for d in task["drafts"] for i in d["evidence_ids"]}
        if any(current.get(h["id"]) != (h["text"], h.get("metadata")) for h in task["evidence"] if h["id"] in used):
            task.update(evidence=[], drafts=[], paper_items=[], validated=False, completed_steps=[], issues=["资料已变更，需要重新取证和组卷。"])
            return task, {"issues": task["issues"], "next": "retrieve_evidence"}
        paper = bank.save_task_paper(task["id"], request.course_id, request.title, task["paper_items"])
        task.update(status="completed", artifacts=[{"type": "paper", "id": paper["id"], "title": paper["title"],
                    "url": "/sz/#/question-bank?paper_id=" + paper["id"]}])
        result = {"saved_count": 1, "artifacts": task["artifacts"]}
    else:
        raise BadRequestException("当前组卷任务不支持此工具")
    if name not in task["completed_steps"] and not (name == "validate_paper_draft" and task["issues"]):
        task["completed_steps"].append(name)
    return task, result
