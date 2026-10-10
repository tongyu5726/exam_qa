"""中文分词模块，基于 jieba 分词器。

提供统一的 tokenize 接口：
- jieba 可用时：搜索引擎模式分词 + 英文词提取 + 可选字符二元组
- jieba 不可用时：回退到原有字符级 N-gram 方案
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# 英文/数字 token 正则
_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
# 中文字符及 CJK 扩展范围
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+")


class ChineseTokenizer:
    """基于 jieba 的中文分词器。

    单例模式，延迟加载 jieba 以避免启动强制依赖。
    同时支持自定义词典扩展。
    """

    _instance: ChineseTokenizer | None = None
    _loaded: bool = False

    def __new__(cls) -> ChineseTokenizer:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._jieba: object | None = None
        self._init_jieba()

    def _init_jieba(self) -> None:
        try:
            import jieba

            jieba.setLogLevel(logging.WARNING)
            self._jieba = jieba
            logger.info("jieba 分词器已加载")
        except ImportError:
            logger.warning("jieba 未安装，中文分词将使用字符 N-gram 回退方案")

    @property
    def available(self) -> bool:
        """jieba 分词器是否可用。"""
        return self._jieba is not None

    def cut(self, text: str) -> list[str]:
        """精确模式分词，返回词语列表。"""
        if not self._jieba:
            return []
        return [w for w in self._jieba.cut(text) if w.strip()]  # type: ignore[union-attr]

    def cut_for_search(self, text: str) -> list[str]:
        """搜索引擎模式分词，粒度更细，适合 BM25 检索。"""
        if not self._jieba:
            return []
        return [w for w in self._jieba.cut_for_search(text) if w.strip()]  # type: ignore[union-attr]

    def add_word(self, word: str, freq: int | None = None, tag: str | None = None) -> None:
        """向词典添加自定义词汇，提升特定领域分词准确率。

        Args:
            word: 要添加的词汇。
            freq: 词频（可选，越高越不容易被切分）。
            tag: 词性标注（可选）。
        """
        if self._jieba:
            self._jieba.add_word(word, freq=freq, tag=tag)  # type: ignore[union-attr]


def _get_tokenizer() -> ChineseTokenizer:
    return ChineseTokenizer()


@lru_cache(maxsize=4096)
def _cached_jieba_cut(text: str) -> tuple[str, ...]:
    """缓存 jieba 分词结果，避免重复切分相同文本。"""
    tokenizer = _get_tokenizer()
    if tokenizer.available:
        return tuple(tokenizer.cut_for_search(text))
    return ()


def tokenize(text: str, *, keep_bigrams: bool = True) -> list[str]:
    """对文本进行分词，返回 BM25 用 token 列表。

    jieba 可用时：搜索引擎模式分词 + 英文词提取，可选保留字符二元组提升召回。
    jieba 不可用时：回退到原有字符级 unigram + bigram + 英文词方案。

    Args:
        text: 待分词文本。
        keep_bigrams: jieba 模式下是否额外保留字符二元组（默认 True 以提升召回率）。

    Returns:
        小写去重后的 token 列表。
    """
    if not text:
        return []

    tokens: list[str] = []
    lower = text.lower()
    tokenizer = _get_tokenizer()

    # 始终提取英文/数字词
    for m in _WORD_RE.finditer(lower):
        tokens.append(m.group())

    if tokenizer.available:
        jieba_tokens = list(_cached_jieba_cut(lower))
        tokens.extend(jieba_tokens)

        if keep_bigrams:
            for tok in jieba_tokens:
                if _CJK_RE.search(tok) and len(tok) >= 2:
                    tokens.extend(tok[i : i + 2] for i in range(len(tok) - 1))
    else:
        # 回退：字符级 unigram + bigram（与原有行为一致）
        for m in _CJK_RE.finditer(lower):
            s = m.group()
            tokens.extend(s)
            tokens.extend(s[i : i + 2] for i in range(len(s) - 1))

    # 去重：保持首次出现顺序
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        stripped = t.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            result.append(stripped)
    return result
