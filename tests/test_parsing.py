"""上传解析：产品支持的格式（MD/TXT/DOCX/PPTX）+ 拒答空文件。"""

import shutil
import sys
import types
from pathlib import Path

import pytest

from src.exceptions import BadRequestException, UnsupportedFormatException
from src.services.parsing import parse_file


class TestParsePlain:
    def test_parse_markdown(self, sample_md_file):
        doc = parse_file(sample_md_file)
        assert "测试文档" in doc.full_text
        assert len(doc.pages) == 1

    def test_parse_txt(self, sample_txt_file):
        assert "纯文本" in parse_file(sample_txt_file).full_text

    def test_empty_and_unsupported(self, temp_dir):
        empty = temp_dir / "empty.txt"
        empty.write_text("   \n", encoding="utf-8")
        with pytest.raises(BadRequestException):
            parse_file(str(empty))
        bad = temp_dir / "data.bin"
        bad.write_bytes(b"\x00\x01")
        with pytest.raises(UnsupportedFormatException):
            parse_file(str(bad))


class TestParseOffice:
    def test_parse_docx(self, temp_dir):
        from docx import Document

        path = temp_dir / "notes.docx"
        doc = Document()
        doc.add_heading("傅里叶变换", level=1)
        doc.add_paragraph("连续时间傅里叶变换的定义。")
        doc.save(str(path))
        parsed = parse_file(str(path))
        assert "傅里叶变换" in parsed.full_text
        assert "连续时间" in parsed.full_text

    @pytest.mark.integration
    def test_parse_pptx(self, temp_dir):
        """真实 LibreOffice/PowerPoint 转换可能启动外部进程，仅显式集成测试运行。"""
        from pptx import Presentation

        path = temp_dir / "slides.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "采样定理"
        slide.placeholders[1].text = "奈奎斯特频率 fs/2"
        prs.save(str(path))
        parsed = parse_file(str(path))
        assert "采样定理" in parsed.full_text
        assert parsed.pages[0].page == 1

    def test_parse_pptx_fallback_without_converter(self, temp_dir, monkeypatch):
        """无 LibreOffice/PowerPoint 时必须回退 python-pptx 提取。"""
        from pptx import Presentation
        from src.services import parsing

        path = temp_dir / "fallback.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "回退测试"
        prs.save(str(path))

        monkeypatch.setattr(parsing, "_convert_pptx_to_pdf", lambda p: None)
        parsed = parse_file(str(path))
        assert "回退测试" in parsed.full_text
        assert parsed.pages[0].page == 1


class TestMineruMapping:
    """MinerU 输出重组逻辑（子进程调用本身不在此测）。"""

    def test_json_to_pages_groups_by_page(self):
        from src.services import parsing

        content = {
            "content_list": [
                {"type": "text", "page_idx": 0, "text": "第一章 引言"},
                {"type": "formula", "page_idx": 0, "latex": "E=mc^2"},
                {"type": "title", "page_idx": 1, "text": "1.1 通信系统模型"},
                {
                    "type": "table",
                    "page_idx": 1,
                    "html": "<table><tr><td>信源</td><td>编码</td></tr></table>",
                },
                {"type": "image", "page_idx": 1},
            ]
        }
        doc = parsing._mineru_json_to_pages(content)
        assert len(doc.pages) == 2
        assert doc.pages[0].page == 1
        assert "第一章" in doc.pages[0].text
        assert "$$E=mc^2$$" in doc.pages[0].text
        assert "信源 | 编码" in doc.pages[1].text

    def test_md_fallback_single_page(self):
        from src.services import parsing

        doc = parsing._mineru_md_to_pages("## 标题\n正文")
        assert len(doc.pages) == 1
        assert doc.pages[0].page is None
        assert "正文" in doc.full_text

    def test_html_table_to_text(self):
        from src.services import parsing

        out = parsing._html_table_to_text(
            "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>"
        )
        assert "a | b" in out
        assert "1 | 2" in out


class TestPdfQualityAndFallback:
    def test_quality_rewards_page_coverage_and_structure(self):
        from src.services import parsing

        sparse = parsing.ParsedDocument([parsing.ParsedPage(page=1, text="很短")])
        structured = parsing.ParsedDocument(
            [parsing.ParsedPage(page=1, text="测" * 200)],
            [parsing.ParsedBlock(block_type="title", text="标题", page=1)],
        )
        assert parsing._assess_pdf_quality(
            structured, expected_pages=1
        ).score > parsing._assess_pdf_quality(sparse, expected_pages=1).score

    def test_markpdfdown_template_requires_input_and_output(self, tmp_path):
        from src.services import parsing

        assert parsing._markpdfdown_command(
            "markpdfdown",
            "--input {input}",
            path="sample.pdf",
            output_dir=tmp_path,
            output_file=tmp_path / "document.md",
        ) is None
        args = parsing._markpdfdown_command(
            "markpdfdown",
            '--input "{input}" --output "{output_file}"',
            path="sample.pdf",
            output_dir=tmp_path,
            output_file=tmp_path / "document.md",
        )
        assert args is not None
        assert args[0] == "markpdfdown"
        assert str(tmp_path / "document.md") in args
        assert any(item.endswith("sample.pdf") for item in args)

    def test_mineru_uses_high_retry_when_first_result_is_low_quality(self, monkeypatch):
        from src.services import parsing

        calls = []
        monkeypatch.setattr(parsing, "_pdf_kind", lambda path: "scanned")
        monkeypatch.setattr(parsing, "_pdf_page_count", lambda path: 1)
        monkeypatch.setattr(parsing, "_mineru_available", lambda cmd: True)

        def fake_mineru(path, **kwargs):
            calls.append(kwargs["effort"])
            if kwargs["effort"] == "medium":
                return parsing.ParsedDocument([parsing.ParsedPage(page=1, text="短")])
            return parsing.ParsedDocument(
                [parsing.ParsedPage(page=1, text="识别结果" * 70)],
                [parsing.ParsedBlock(block_type="table", text="识别表格", page=1)],
            )

        monkeypatch.setattr(parsing, "_parse_pdf_mineru", fake_mineru)
        monkeypatch.setattr(parsing.config.parsing, "pdf_parser", "mineru")
        monkeypatch.setattr(parsing.config.parsing, "mineru_effort", "medium")
        monkeypatch.setattr(parsing.config.parsing, "mineru_retry_high", True)
        monkeypatch.setattr(parsing.config.parsing, "pdf_quality_threshold", 0.55)
        doc = parsing._parse_pdf("sample.pdf")
        assert calls == ["medium", "high"]
        assert "识别结果" in doc.full_text

    def test_mineru_passes_verified_high_accuracy_options(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from src.services import parsing

        captured = []
        monkeypatch.setattr(parsing, "_mineru_available", lambda cmd: True)

        def fake_run(args, *, timeout=None, encoding="utf-8"):
            captured.extend(args)
            # _parse_pdf_mineru 传入的临时输出目录是 -o 后的值。
            requested_out = Path(args[args.index("-o") + 1])
            requested_out.mkdir(parents=True, exist_ok=True)
            (requested_out / "result.json").write_text(
                '{"content_list":[{"type":"text","page_idx":0,"text":"解析成功"}]}',
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0), "", ""

        monkeypatch.setattr(parsing, "_run_capture", fake_run)
        doc = parsing._parse_pdf_mineru(
            "sample.pdf",
            cmd="mineru",
            backend="hybrid-engine",
            effort="high",
            lang="ch",
            formula=True,
            table=True,
            image_analysis=True,
        )
        assert doc is not None and "解析成功" in doc.full_text
        assert ["-b", "hybrid-engine"] == captured[captured.index("-b") : captured.index("-b") + 2]
        assert ["--effort", "high"] == captured[
            captured.index("--effort") : captured.index("--effort") + 2
        ]
        assert ["--image-analysis", "true"] == captured[
            captured.index("--image-analysis") : captured.index("--image-analysis") + 2
        ]


class TestMineruStructure:
    """结构还原：block 类型、标题层级、表格 HTML、图片路径与说明关联。"""

    def _content(self, tmp_path):
        img = tmp_path / "fig1.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        return {
            "content_list": [
                {"type": "header", "page_idx": 0, "text": "信号与系统"},
                {"type": "title", "page_idx": 0, "text": "第一章 绪论", "level": 1},
                {"type": "title", "page_idx": 0, "text": "1.1 通信系统模型", "level": 2},
                {"type": "text", "page_idx": 0, "text": "通信系统由信源、信道和信宿组成。"},
                {
                    "type": "table",
                    "page_idx": 1,
                    "html": (
                        "<table><tr><th>名称</th><th>说明</th></tr>"
                        "<tr><td>信源</td><td>信息发起点</td></tr></table>"
                    ),
                },
                {"type": "image", "page_idx": 1, "img_path": "fig1.png"},
                {"type": "image_caption", "page_idx": 1, "text": "图1-1 通信系统框图"},
                {"type": "formula", "page_idx": 1, "latex": "C = B\\log_2(1+SNR)"},
            ]
        }

    def test_structure_restored(self, tmp_path):
        from src.services import parsing

        doc = parsing._mineru_json_to_document(
            self._content(tmp_path), base_dir=tmp_path
        )
        assert doc.has_blocks
        # header + title×2 + text + table + image + formula = 7 块（caption 挂载不单独成块）
        assert len(doc.blocks) == 7

        titles = [b for b in doc.blocks if b.block_type == "title"]
        assert [t.level for t in titles] == [1, 2]
        assert titles[0].page == 1 and titles[1].page == 1

        table = next(b for b in doc.blocks if b.block_type == "table")
        assert table.html and "名称" in table.html
        assert parsing._table_headers(table.html) == "名称 | 说明"
        assert table.page == 2

        image = next(b for b in doc.blocks if b.block_type == "image")
        assert image.image_path == str(tmp_path / "fig1.png")
        assert image.caption == "图1-1 通信系统框图"

        formula = next(b for b in doc.blocks if b.block_type == "formula")
        assert "$$C = B\\log_2(1+SNR)$$" in formula.text

        header = next(b for b in doc.blocks if b.block_type == "header")
        assert header.text == "信号与系统"
        assert len(doc.pages) == 2

    def test_table_headers_extraction(self):
        from src.services import parsing

        assert (
            parsing._table_headers(
                "<table><tr><th>信源</th><th>编码</th></tr><tr><td>a</td><td>b</td></tr></table>"
            )
            == "信源 | 编码"
        )
        assert parsing._table_headers("<table><tr><td>a</td></tr></table>") == ""

    def test_handwritten_formula_reference_and_noise_roles(self, tmp_path):
        """复杂笔记中的公式、教材页码、批注和手机状态栏必须被区别对待。"""
        from src.services import parsing

        doc = parsing._mineru_json_to_document(
            {
                "content_list": [
                    {"type": "text", "page_idx": 0, "text": "P28 Ex2"},
                    {"type": "text", "page_idx": 0, "text": "考点：ε-N语言"},
                    {"type": "text", "page_idx": 0, "text": "10:29"},
                    {
                        "type": "equation",
                        "page_idx": 0,
                        "math_content": r"\\lim_{n\\to\\infty} a_n = 1",
                        "page_label": "i",
                    },
                ]
            },
            base_dir=tmp_path,
        )
        reference = next(b for b in doc.blocks if b.text == "P28 Ex2")
        annotation = next(b for b in doc.blocks if b.text.startswith("考点"))
        noise = next(b for b in doc.blocks if b.text == "10:29")
        formula = next(b for b in doc.blocks if b.block_type == "formula")

        assert reference.content_role == "reference"
        assert reference.textbook_references == ("P28 Ex2",)
        assert annotation.content_role == "annotation"
        assert noise.content_role == "noise"
        assert formula.latex == r"\\lim_{n\\to\\infty} a_n = 1"
        assert formula.text == r"$$\\lim_{n\\to\\infty} a_n = 1$$"
        assert formula.pdf_page_label == "i"

    def test_parser_assets_survive_external_temp_cleanup(self, tmp_path, monkeypatch):
        """外部解析器临时目录被清理后，视觉摘要仍可读取已持久化的裁剪图。"""
        from src.services import parsing

        source = tmp_path / "notes.pdf"
        source.write_bytes(b"fake pdf")
        work_dir = tmp_path / "mineru-output"
        work_dir.mkdir()
        image = work_dir / "crop.png"
        image.write_bytes(b"image bytes")
        assets = tmp_path / "persisted-assets"
        monkeypatch.setattr(parsing.config.storage, "parsed_assets_dir", str(assets))
        doc = parsing.ParsedDocument(
            pages=[],
            blocks=[parsing.ParsedBlock(block_type="image", text="", page=1, image_path=str(image))],
        )

        parsing._persist_parser_assets(doc, work_dir=work_dir, source_path=str(source))
        shutil.rmtree(work_dir)

        persisted = Path(doc.blocks[0].image_path)
        assert persisted.is_file()
        assert persisted.read_bytes() == b"image bytes"

    def test_paddle_formula_payloads_keep_page_and_bbox(self):
        from src.services import parsing

        payloads = parsing._paddle_result_payloads(
            {
                "page_idx": 2,
                "formulas": [
                    {
                        "rec_formula": r"\frac{n}{n+1}",
                        "polygon": [[10, 20], [110, 20], [110, 60], [10, 60]],
                    }
                ],
            }
        )
        assert len(payloads) == 1
        assert payloads[0]["_page"] == 3
        assert payloads[0]["_formula_latex"] == r"\frac{n}{n+1}"
        assert parsing._formula_bbox(payloads[0]["polygon"]) == (10.0, 20.0, 110.0, 60.0)

    def test_paddle_formula_page_index_is_zero_based_but_page_is_one_based(self):
        from src.services import parsing

        by_index = parsing._paddle_result_payloads(
            {"page_index": 4, "formulas": [{"rec_formula": r"n=\sqrt{\varepsilon_r}"}]}
        )
        by_page = parsing._paddle_result_payloads(
            {"page": 5, "formulas": [{"rec_formula": r"\varepsilon_r=n^2"}]}
        )

        assert by_index[0]["_page"] == 5
        assert by_page[0]["_page"] == 5

    def test_formula_enrichment_preserves_fallback_text(self, monkeypatch):
        from src.services import parsing

        class FakePipeline:
            def __init__(self, **kwargs):
                assert kwargs["device"] == "cpu"
                assert kwargs["enable_mkldnn"] is False

            def predict(self, path):
                return iter(
                    [
                        {
                            "page_idx": 0,
                            "formulas": [{"rec_formula": r"x^2+y^2=z^2", "bbox": [1, 2, 30, 12]}],
                        }
                    ]
                )

        monkeypatch.setitem(
            sys.modules,
            "paddleocr",
            types.SimpleNamespace(FormulaRecognitionPipeline=FakePipeline),
        )
        monkeypatch.setattr(parsing.config.parsing, "formula_recognition_enabled", True)
        monkeypatch.setattr(parsing.config.parsing, "formula_recognition_device", "cpu")
        monkeypatch.setattr(
            parsing.config.parsing, "formula_recognition_enable_mkldnn", False
        )
        doc = parsing.ParsedDocument([parsing.ParsedPage(page=1, text="论文正文")])

        enriched = parsing._enrich_document_with_formulas(doc, "paper.pdf")

        assert enriched is not None
        assert [block.block_type for block in enriched.blocks] == ["text", "formula"]
        assert enriched.blocks[1].latex == r"x^2+y^2=z^2"
        assert "论文正文" in enriched.full_text
        assert r"$$x^2+y^2=z^2$$" in enriched.full_text

    def test_formula_enrichment_completes_partial_formulas_without_duplicates(
        self, monkeypatch
    ):
        from src.services import parsing

        class FakePipeline:
            def __init__(self, **kwargs):
                pass

            def predict(self, path):
                return iter(
                    [
                        {
                            "page_idx": 0,
                            "formulas": [
                                {"rec_formula": r"x^2"},
                                {"rec_formula": r"n=\sqrt{\varepsilon_r}"},
                            ],
                        }
                    ]
                )

        monkeypatch.setitem(
            sys.modules,
            "paddleocr",
            types.SimpleNamespace(FormulaRecognitionPipeline=FakePipeline),
        )
        monkeypatch.setattr(parsing.config.parsing, "formula_recognition_enabled", True)
        monkeypatch.setattr(parsing.config.parsing, "formula_recognition_device", "cpu")
        existing = parsing.ParsedBlock(
            block_type="formula", text=r"$$x^2$$", latex=r"x^2", page=1
        )
        doc = parsing.ParsedDocument(
            [parsing.ParsedPage(page=1, text="已有部分公式")], blocks=[existing]
        )

        enriched = parsing._enrich_document_with_formulas(doc, "paper.pdf")

        assert enriched is not None
        latex = [block.latex for block in enriched.blocks if block.latex]
        assert latex.count(r"x^2") == 1
        assert latex.count(r"n=\sqrt{\varepsilon_r}") == 1
