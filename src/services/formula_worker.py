"""PaddleOCR 公式识别子进程入口。

Windows 下 PyTorch 与 PaddlePaddle GPU 可能携带不同版本的 cuDNN DLL；
把 Paddle 推理放到独立进程可避免同一地址空间内的 DLL 符号冲突。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _json_safe(tolist())
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except (TypeError, ValueError):
            pass
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--enable-mkldnn", action="store_true")
    args = parser.parse_args()

    # PaddleOCR/ModelScope 会用 find_spec 探测并主动导入 Torch，即便公式流水线并不需要。
    # 在专用子进程中让可选依赖探测不到它，确保只加载 Paddle 的 CUDA/cuDNN。
    real_find_spec = importlib.util.find_spec

    def find_spec_without_torch(name: str, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            return None
        return real_find_spec(name, *args, **kwargs)

    importlib.util.find_spec = find_spec_without_torch
    from paddleocr import FormulaRecognitionPipeline

    from src.services.parsing_adapters.enrichment.formula import _paddle_result_payloads
    from src.services.inference_device import resolve_paddle_device

    pipeline = FormulaRecognitionPipeline(
        device=resolve_paddle_device(args.device),
        formula_recognition_model_name=args.model,
        enable_mkldnn=args.enable_mkldnn,
    )
    payloads: list[dict] = []
    for result in pipeline.predict(args.input):
        payloads.extend(_paddle_result_payloads(result))
    Path(args.output).write_text(
        json.dumps(_json_safe(payloads), ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
