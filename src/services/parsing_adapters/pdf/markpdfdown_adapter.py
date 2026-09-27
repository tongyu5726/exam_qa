from __future__ import annotations

import logging
import json
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from src.services.parsing_adapters.blocks import _annotate_block, _persist_parser_assets
from src.services.parsing_adapters.commands import _command_error_tail, _run_capture
from src.services.parsing_adapters.enrichment.formula import _formula_latex
from src.services.parsing_adapters.models import ParsedBlock, ParsedDocument, ParsedPage
from src.services.parsing_adapters.pdf.mineru_adapter import _blocks_to_pages, _mineru_json_to_document, _mineru_md_to_pages
from src.services.parsing_adapters.pdf.pymupdf_adapter import _page_num

logger = logging.getLogger(__name__)


def _markpdfdown_available(cmd: str) -> bool:
    """外部增强解析器命令是否可调用。"""
    return bool(cmd and shutil.which(cmd))


def _markpdfdown_command(
    cmd: str,
    args_template: str,
    *,
    path: str,
    output_dir: Path,
    output_file: Path,
) -> list[str] | None:
    """按显式模板构建外部解析器命令，禁止 shell 执行与猜测 CLI 参数。

    {output} 指临时目录，适用于目录型 CLI；{output_file} 指临时 .md 文件，
    适用于 MarkPDFdown 的 ``--output`` 文件参数。
    """
    if "{input}" not in args_template or (
        "{output}" not in args_template and "{output_file}" not in args_template
    ):
        logger.warning(
            "MARKPDFDOWN_ARGS 必须包含 {input} 与 {output}/{output_file} 占位符，跳过增强解析器"
        )
        return None
    try:
        # 命令最终以 list 传给 subprocess，不需要保留引号；posix=True 可统一
        # 去除模板中的引号（Windows 下 shlex 的非 POSIX 模式会把引号当作字符）。
        tokens = shlex.split(args_template, posix=True)
    except ValueError as exc:
        logger.warning("MARKPDFDOWN_ARGS 格式错误，跳过增强解析器: %s", exc)
        return None
    if not tokens:
        logger.warning("MARKPDFDOWN_ARGS 为空，跳过增强解析器")
        return None
    replacements = {
        "{input}": str(Path(path).resolve()),
        "{output}": str(output_dir),
        "{output_file}": str(output_file),
    }
    return [
        cmd,
        *[
            token.replace("{input}", replacements["{input}"])
            .replace("{output_file}", replacements["{output_file}"])
            .replace("{output}", replacements["{output}"])
            for token in tokens
        ],
    ]


def _generic_json_to_document(content: object, *, base_dir: Path) -> ParsedDocument | None:
    """兼容外部解析器常见 JSON 输出；优先复用 MinerU 的 content_list 结构。"""
    if isinstance(content, dict) and isinstance(content.get("content_list"), list):
        return _mineru_json_to_document(content, base_dir=base_dir)
    if isinstance(content, dict):
        raw_pages = content.get("pages") or (content.get("data") or {}).get("pages")
    else:
        raw_pages = None
    if not isinstance(raw_pages, list):
        return None
    pages: list[ParsedPage] = []
    blocks: list[ParsedBlock] = []
    for index, item in enumerate(raw_pages, 1):
        if not isinstance(item, dict):
            continue
        page = _page_num(item) or index
        text = str(item.get("text") or item.get("content") or item.get("markdown") or "").strip()
        if text:
            pages.append(ParsedPage(page=page, text=text))
        for order, block in enumerate(item.get("blocks") or []):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "text")
            if block_type in {"equation", "equation_interline", "display_formula"}:
                block_type = "formula"
            elif block_type in {"inline_equation", "inline_formula"}:
                block_type = "formula_inline"
            if block_type in {"formula", "formula_inline"}:
                latex = _formula_latex(block)
                block_text = f"$${latex}$$" if latex else ""
            else:
                block_text = str(block.get("text") or block.get("content") or "").strip()
            if block_text:
                blocks.append(
                    _annotate_block(ParsedBlock(
                        block_type=block_type,
                        text=block_text,
                        page=page,
                        order=order,
                        pdf_page_label=str(item.get("page_label") or "").strip(),
                    ))
                )
    if blocks and not pages:
        pages = _blocks_to_pages(blocks)
    return ParsedDocument(pages=pages, blocks=blocks) if pages else None


def _parse_pdf_markpdfdown(
    path: str, *, cmd: str, args_template: str, timeout: int
) -> ParsedDocument | None:
    """运行可配置的 MarkPDFdown 适配器并读取 Markdown/JSON 输出。

    项目不假设第三方 CLI 的参数名；使用者必须在 MARKPDFDOWN_ARGS 指定
    带 {input}/{output} 的准确命令模板。任何异常均回退内置解析链。
    """
    if not _markpdfdown_available(cmd):
        logger.warning("MarkPDFdown 命令不可用: %s", cmd or "<未配置>")
        return None
    tmp = Path(tempfile.mkdtemp(prefix="markpdfdown_"))
    try:
        args = _markpdfdown_command(
            cmd,
            args_template,
            path=path,
            output_dir=tmp,
            output_file=tmp / "document.md",
        )
        if not args:
            return None
        logger.info("MarkPDFdown 增强解析开始: %s", Path(path).name)
        proc, stdout, stderr = _run_capture(
            args, timeout=None if timeout <= 0 else timeout
        )
        if proc.returncode != 0:
            logger.warning(
                "MarkPDFdown 退出码 %s: %s",
                proc.returncode,
                _command_error_tail(stdout, stderr),
            )
            return None
        for json_path in sorted(tmp.rglob("*.json")):
            try:
                content = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if doc := _generic_json_to_document(content, base_dir=json_path.parent):
                _persist_parser_assets(doc, work_dir=tmp, source_path=path)
                logger.info("MarkPDFdown 解析完成: %s (json)", Path(path).name)
                return doc
        markdown_files = [*tmp.rglob("*.md"), *tmp.rglob("*.markdown")]
        if markdown_files:
            best = max(markdown_files, key=lambda item: item.stat().st_size)
            doc = _mineru_md_to_pages(
                best.read_text(encoding="utf-8", errors="replace"),
                base_dir=best.parent,
                source_path=path,
            )
            if doc.pages:
                _persist_parser_assets(doc, work_dir=tmp, source_path=path)
                logger.info("MarkPDFdown 解析完成: %s (markdown)", Path(path).name)
                return doc
        logger.warning("MarkPDFdown 未产出可用 JSON/Markdown: %s", Path(path).name)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("MarkPDFdown 解析超时（%s 秒）: %s", timeout, Path(path).name)
        return None
    except Exception as exc:
        logger.warning("MarkPDFdown 解析异常: %s", exc)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
