"""pytest fixtures：隔离存储 + 样例文件。"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from dotenv import dotenv_values

_test_runtime = None
_session_patch = None


def pytest_configure(config):
    """在收集测试、导入应用前隔离配置、注册表与资料库。"""
    global _test_runtime, _session_patch
    from src.services import env_store

    _test_runtime = tempfile.TemporaryDirectory(prefix="exam_test_", ignore_cleanup_errors=True)
    root = Path(_test_runtime.name)
    if config.option.basetemp is None:
        # 避开其他用户/旧进程遗留的 pytest-of-* 目录权限与清理冲突。
        config.option.basetemp = str(root / "pytest")
    _session_patch = pytest.MonkeyPatch()
    defaults = dotenv_values(env_store.ENV_EXAMPLE_PATH)
    integration = "integration" in config.getoption("markexpr") and "not integration" not in config.getoption("markexpr")
    for key, value in defaults.items():
        # 集成测试凭据由调用者显式传入环境变量；从不读取开发者的 .env。
        if value is not None and not (integration and key in os.environ):
            _session_patch.setenv(key, value)
    for key, relative in {
        "CHROMA_PATH": "storage/chroma", "SQLITE_PATH": "storage/meta.db",
        "LOG_PATH": "storage/app.log", "KNOWLEDGE_DIR": "data/knowledge",
        "PARSED_ASSETS_DIR": "storage/parsed_assets",
    }.items():
        _session_patch.setenv(key, str(root / relative))
    _session_patch.setenv("PDF_PARSER", "pymupdf")
    _session_patch.setenv("MINERU_TIMEOUT", "10")
    _session_patch.setattr(env_store, "PROJECT_ROOT", root)
    _session_patch.setattr(env_store, "ENV_PATH", root / ".env")
    # ensure_env_file 和设置页写入只会落在临时目录。


def pytest_unconfigure(config):
    if _session_patch is not None:
        _session_patch.undo()
    if _test_runtime is not None:
        _test_runtime.cleanup()


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def doc_store(temp_dir):
    from src.services.storage.doc_store import SQLiteDocStore

    store = SQLiteDocStore(str(temp_dir / "test_meta.db"))
    yield store
    store.close()


@pytest.fixture
def vector_store(temp_dir):
    from src.services.storage.vector_store import ChromaVectorStore

    store = ChromaVectorStore(
        str(temp_dir / "test_chroma"), collection_name="test_exam_rag"
    )
    yield store
    store.close()


@pytest.fixture
def sample_md_file(temp_dir):
    content = """# 测试文档

## 第一节

这是测试内容。用于验证入库管道的分块功能。

## 第二节

傅里叶变换是信号与系统中的核心概念。
"""
    path = temp_dir / "test_sample.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


@pytest.fixture
def sample_txt_file(temp_dir):
    path = temp_dir / "test_sample.txt"
    path.write_text(
        "这是纯文本测试文件。\n用于验证 TXT 格式解析。\n",
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture(autouse=True)
def _clear_retrieval_caches():
    """每个用例执行前清空检索缓存（BM25 语料、查询向量），保证用例隔离。"""
    from src.services.retrieval import clear_query_embed_cache, invalidate_bm25_cache

    clear_query_embed_cache()
    invalidate_bm25_cache()
    yield
