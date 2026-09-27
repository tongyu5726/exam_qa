"""模拟无显卡、GPU 运行时故障与显存不足，不下载模型。"""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services import inference_device as devices


def fake_torch(monkeypatch, *, cuda=False, mps=False):
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda, device_count=lambda: int(cuda), current_device=lambda: 0),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        empty=Mock(),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.mark.parametrize("requested,cuda,mps,expected", [
    ("auto", False, False, "cpu"), ("auto", True, False, "cuda"),
    ("auto", False, True, "mps"), ("cpu", True, False, "cpu"),
    ("cuda", False, False, "cpu"), ("cuda:0", True, False, "cuda:0"),
    ("cuda:8", True, False, "cpu"), ("mps", False, False, "cpu"),
])
def test_torch_hardware_selection(monkeypatch, requested, cuda, mps, expected):
    fake_torch(monkeypatch, cuda=cuda, mps=mps)
    assert devices.resolve_torch_device(requested) == expected


def test_broken_cuda_runtime_falls_back(monkeypatch):
    torch = fake_torch(monkeypatch, cuda=True)
    torch.empty.side_effect = RuntimeError("CUDA driver version is insufficient")
    assert devices.resolve_torch_device("auto") == "cpu"


def test_gpu_model_load_oom_retries_cpu(monkeypatch):
    fake_torch(monkeypatch, cuda=True)
    cpu_model = object()
    factory = Mock(side_effect=[RuntimeError("CUDA out of memory"), cpu_model])
    assert devices.load_torch_model(factory, "model", "auto") is cpu_model
    assert [call.kwargs["device"] for call in factory.call_args_list] == ["cuda", "cpu"]


def test_gpu_inference_oom_moves_model_to_cpu():
    model = SimpleNamespace(device="cuda:0", to=Mock())
    operation = Mock(side_effect=[RuntimeError("CUDA out of memory"), [0.5]])
    assert devices.run_torch_inference(model, operation) == [0.5]
    model.to.assert_called_once_with("cpu")
    assert operation.call_count == 2


def test_unrelated_error_is_not_hidden(monkeypatch):
    fake_torch(monkeypatch, cuda=True)
    factory = Mock(side_effect=RuntimeError("invalid model configuration"))
    with pytest.raises(RuntimeError, match="invalid model"):
        devices.load_torch_model(factory, "model", "auto")
    assert factory.call_count == 1


@pytest.mark.parametrize("compiled,count,requested,expected", [
    (False, 0, "auto", "cpu"), (True, 1, "auto", "gpu:0"),
    (False, 0, "gpu", "cpu"), (True, 1, "gpu:3", "cpu"),
    (True, 1, "cpu", "cpu"),
])
def test_paddle_checks_its_own_runtime(monkeypatch, compiled, count, requested, expected):
    fake_torch(monkeypatch, cuda=True)
    paddle = SimpleNamespace(
        is_compiled_with_cuda=lambda: compiled,
        device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: count)),
        set_device=Mock(), empty=Mock(),
    )
    monkeypatch.setitem(sys.modules, "paddle", paddle)
    assert devices.resolve_paddle_device(requested) == expected


def test_embedding_and_reranker_receive_configured_device(monkeypatch):
    from src.config import EmbeddingConfig, LLMConfig
    from src.services import embedding, rerank

    fake_torch(monkeypatch, cuda=True)
    encoder = Mock()
    encoder.get_embedding_dimension.return_value = 3
    sentence_factory, cross_factory = Mock(return_value=encoder), Mock(return_value=encoder)
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=sentence_factory, CrossEncoder=cross_factory,
    ))
    monkeypatch.setattr("src.services.http_client.reset_hf_http_session", lambda: None)
    client = embedding.create_embedding_client(EmbeddingConfig(device="cpu"), LLMConfig())
    client.warmup()
    sentence_factory.assert_called_once_with(client.model, device="cpu")
    monkeypatch.setattr(rerank.config.retrieval, "rerank_device", "cuda:0")
    rerank.clear_reranker()
    try:
        rerank._load("reranker")
        cross_factory.assert_called_once_with("reranker", device="cuda:0")
    finally:
        rerank.clear_reranker()


def test_windows_auto_formula_uses_isolated_worker(monkeypatch):
    from src.services.parsing_adapters.enrichment import formula

    monkeypatch.setattr(formula, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(formula.config.parsing, "formula_recognition_device", "auto")
    worker = Mock(return_value=[{"rec_formula": "x^2"}])
    monkeypatch.setattr(formula, "_run_formula_worker", worker)
    assert formula._run_formula_pipeline("test.pdf") == [{"rec_formula": "x^2"}]
    worker.assert_called_once_with("test.pdf")
