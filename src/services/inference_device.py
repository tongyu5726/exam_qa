"""按各推理框架的实际能力选择设备；不安装或修改任何依赖。"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)


def validate_device(value: str, *, paddle: bool = False) -> str:
    device = value.strip().lower() or "auto"
    pattern = r"auto|cpu|gpu(?::\d+)?" if paddle else r"auto|cpu|mps|cuda(?::\d+)?"
    if not re.fullmatch(pattern, device):
        choices = "auto / cpu / gpu / gpu:N" if paddle else "auto / cpu / cuda / cuda:N / mps"
        raise ValueError(f"推理设备无效: {value!r}，可选 {choices}")
    return device


def resolve_torch_device(requested: str = "auto") -> str:
    device = validate_device(requested)
    if device == "cpu":
        return device
    import torch

    try:
        if device == "auto":
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                return "cpu"
        if device.startswith("cuda"):
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA 不可用")
            index = int(device.split(":")[1]) if ":" in device else torch.cuda.current_device()
            if index >= torch.cuda.device_count():
                raise RuntimeError("CUDA 不可用或设备编号超出范围")
        elif not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            raise RuntimeError("MPS 不可用")
        # 检查驱动/运行库能否实际分配张量，不能只看机器是否装有显卡。
        torch.empty(1, device=device)
    except (RuntimeError, AssertionError, OSError) as exc:
        logger.warning("推理设备 %s 不可用，回退 CPU: %s", requested, exc)
        return "cpu"
    return device


def resolve_paddle_device(requested: str = "auto") -> str:
    device = validate_device(requested, paddle=True)
    if device == "cpu":
        return device
    # 仅在 Paddle 推理进程中调用，不能用 Torch 的 CUDA 状态判断 Paddle。
    import paddle

    try:
        index = int(device.split(":")[1]) if ":" in device else 0
        if not paddle.is_compiled_with_cuda() or index >= paddle.device.cuda.device_count():
            raise RuntimeError("Paddle GPU 运行时或设备不可用")
        paddle.set_device(f"gpu:{index}")
        paddle.empty([1])
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning("公式推理设备 %s 不可用，回退 CPU: %s", requested, exc)
        paddle.set_device("cpu")
        return "cpu"
    return f"gpu:{index}"


def _accelerator_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(word in message for word in (
        "cuda", "cudnn", "cublas", "out of memory", "mps", "device-side",
    ))


def load_torch_model(factory: Callable[..., Any], name: str, requested: str) -> Any:
    device = resolve_torch_device(requested)
    logger.info("加载本地模型: %s，device=%s", name, device)
    try:
        return factory(name, device=device)
    except RuntimeError as exc:
        if device == "cpu" or not _accelerator_error(exc):
            raise
        logger.warning("GPU 模型加载失败，使用 CPU 重试: %s", exc)
        return factory(name, device="cpu")


def run_torch_inference(model: Any, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except RuntimeError as exc:
        device = str(model.device)
        if not device.startswith(("cuda", "mps")) or not _accelerator_error(exc):
            raise
        logger.warning("GPU 推理失败，迁移到 CPU 重试: %s", exc)
        model.to("cpu")
        return operation()
