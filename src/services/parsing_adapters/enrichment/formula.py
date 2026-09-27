from __future__ import annotations

import logging
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from src.config import config
from src.services.parsing_adapters.blocks import _annotate_block, _as_bbox
from src.services.parsing_adapters.commands import _command_error_tail, _run_capture
from src.services.parsing_adapters.models import ParsedBlock, ParsedDocument

logger = logging.getLogger(__name__)


def _normalize_formula_latex(value: object) -> str:
    """将不同解析器的公式字段归一为裸 LaTex，避免重复包裹分隔符。"""
    latex = str(value or "").strip()
    if latex.startswith("$$") and latex.endswith("$$") and len(latex) >= 4:
        return latex[2:-2].strip()
    if latex.startswith("\\[") and latex.endswith("\\]") and len(latex) >= 4:
        return latex[2:-2].strip()
    if latex.startswith("\\(") and latex.endswith("\\)") and len(latex) >= 4:
        return latex[2:-2].strip()
    if latex.startswith("$") and latex.endswith("$") and len(latex) >= 2:
        return latex[1:-1].strip()
    return latex


def _formula_latex(block: dict) -> str:
    """兼容 MinerU / 其他解析器的 latex、math_content、text 等公式字段。"""
    for key in ("latex", "rec_formula", "formula", "formula_text", "math_content", "math", "content", "text"):
        value = _normalize_formula_latex(block.get(key))
        if value:
            return value
    return ""


def _paddle_result_payloads(result: object) -> list[dict]:
    """兼容 PaddleOCR 3.x Result 的 json/dict 形态，提取可能含公式的字典。"""
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    found: list[dict] = []

    def walk(value: object, inherited_page: int | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item, inherited_page)
            return
        if not isinstance(value, dict):
            return
        # PaddleOCR 的 page_index/page_idx 均为零基页码；项目内部与 PDF
        # 其余解析器统一使用一基页码。`page` 则视为已经归一化的一基值。
        if value.get("page_index") is not None:
            page = value.get("page_index")
            zero_based = True
        elif value.get("page_idx") is not None:
            page = value.get("page_idx")
            zero_based = True
        else:
            page = value.get("page")
            zero_based = False
        try:
            page = int(page) + 1 if zero_based else int(page)
        except (TypeError, ValueError):
            page = inherited_page
        latex = _formula_latex(value)
        # 只接受明确的公式输出键，防止把一般文字字段误写成 LaTeX。
        has_formula_key = any(key in value for key in ("latex", "rec_formula", "formula", "formula_text"))
        if latex and has_formula_key:
            item = dict(value)
            item["_formula_latex"] = latex
            item["_page"] = page
            found.append(item)
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child, page)

    walk(payload)
    return found


def _formula_bbox(raw: object) -> tuple | None:
    """兼容 PaddleOCR 的 bbox/coordinate/polygon 输出，统一成 x0,y0,x1,y1。"""
    if isinstance(raw, (list, tuple)) and len(raw) == 4 and all(
        isinstance(value, (int, float)) for value in raw
    ):
        return _as_bbox(raw)
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        points = [point for point in raw if isinstance(point, (list, tuple)) and len(point) >= 2]
        if points:
            try:
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
                return min(xs), min(ys), max(xs), max(ys)
            except (TypeError, ValueError):
                return None
    return None


def _run_formula_worker(path: str) -> list[dict]:
    """在独立进程运行 PaddleOCR，隔离 Windows 下 Torch/Paddle 的 cuDNN DLL。"""
    p = config.parsing
    with tempfile.TemporaryDirectory(prefix="formula_worker_") as tmp:
        output = Path(tmp) / "payloads.json"
        args = [
            sys.executable,
            "-m",
            "src.services.formula_worker",
            "--input",
            str(Path(path).resolve()),
            "--output",
            str(output),
            "--device",
            p.formula_recognition_device,
            "--model",
            p.formula_recognition_model,
        ]
        if p.formula_recognition_enable_mkldnn:
            args.append("--enable-mkldnn")
        timeout = p.formula_recognition_timeout or None
        proc, stdout, stderr = _run_capture(args, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                "PaddleOCR 公式子进程退出码 "
                f"{proc.returncode}: {_command_error_tail(stdout, stderr)}"
            )
        if not output.is_file():
            raise RuntimeError(
                "PaddleOCR 公式子进程未产出 JSON: "
                + _command_error_tail(stdout, stderr)
            )
        payloads = json.loads(output.read_text(encoding="utf-8"))
        if not isinstance(payloads, list):
            raise RuntimeError("PaddleOCR 公式子进程输出格式错误")
        return [item for item in payloads if isinstance(item, dict)]


def _run_formula_pipeline(path: str) -> list[dict]:
    """运行公式识别；Windows GPU 使用子进程以避免 cuDNN DLL 版本冲突。"""
    p = config.parsing
    device = p.formula_recognition_device.strip().lower()
    if os.name == "nt" and (device == "auto" or device.startswith(("gpu", "cuda"))):
        logger.info("公式识别使用独立 GPU 子进程: device=%s", p.formula_recognition_device)
        return _run_formula_worker(path)

    # CPU Torch 与 Paddle 在当前 Windows 环境可按该顺序共存；GPU 路径必须走上面的隔离分支。
    if os.name == "nt":
        import torch  # noqa: F401

    from paddleocr import FormulaRecognitionPipeline
    from src.services.inference_device import resolve_paddle_device

    pipeline = FormulaRecognitionPipeline(
        device=resolve_paddle_device(p.formula_recognition_device),
        formula_recognition_model_name=p.formula_recognition_model,
        enable_mkldnn=p.formula_recognition_enable_mkldnn,
    )
    payloads: list[dict] = []
    for result in pipeline.predict(path):
        payloads.extend(_paddle_result_payloads(result))
    return payloads


def _enrich_document_with_formulas(doc: ParsedDocument | None, path: str) -> ParsedDocument | None:
    """对全文补偿公式块；失败时保留原解析结果，不中断文档入库。"""
    p = config.parsing
    if not doc or not doc.pages or not p.formula_recognition_enabled:
        return doc
    try:
        payloads = _run_formula_pipeline(path)
    except Exception as exc:
        logger.warning("全文公式识别失败，保留原解析文本: %s", exc)
        return doc

    if not payloads:
        logger.info("全文公式识别未发现可用 LaTeX: %s", Path(path).name)
        return doc

    # 回退解析页先显式建为文本块；否则 has_blocks=True 后结构化切片会只保留公式而丢正文。
    blocks = list(doc.blocks) or [
        _annotate_block(
            ParsedBlock(
                block_type="text",
                text=page.text,
                page=page.page,
                order=(page.page or index + 1) * 100000,
                pdf_page_label=page.pdf_page_label,
            )
        )
        for index, page in enumerate(doc.pages)
        if page.text.strip()
    ]
    per_page_order: dict[int | None, int] = {}
    formulas: list[ParsedBlock] = []
    existing_formula_ids = {
        re.sub(r"\s+", "", block.latex or _formula_latex(block.text))
        for block in blocks
        if block.block_type in {"formula", "formula_inline"}
    }
    for payload in payloads:
        page = payload.get("_page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        per_page_order[page] = per_page_order.get(page, 0) + 1
        latex = str(payload["_formula_latex"])
        formula_id = re.sub(r"\s+", "", latex)
        if not formula_id or formula_id in existing_formula_ids:
            continue
        existing_formula_ids.add(formula_id)
        formula = _annotate_block(
            ParsedBlock(
                block_type="formula",
                text="$$" + latex + "$$",
                latex=latex,
                page=page,
                order=(page or 0) * 100000 + per_page_order[page],
                bbox=_formula_bbox(
                    payload.get("bbox") or payload.get("coordinate") or payload.get("polygon")
                ),
            )
        )
        formulas.append(formula)
    blocks.extend(formulas)
    blocks.sort(key=lambda block: (block.page or 0, block.order))
    for formula in formulas:
        for page in doc.pages:
            if page.page == formula.page:
                page.text = (page.text + "\n\n" + formula.text).strip()
                break
    doc.blocks = blocks
    logger.info("全文公式识别完成: %s, 新增 %d 条 LaTeX 公式", Path(path).name, len(formulas))
    return doc
