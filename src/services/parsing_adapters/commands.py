from __future__ import annotations

import subprocess


def _run_capture(args: list[str], *, timeout: float | None = None, encoding: str = "utf-8"):
    """subprocess.run 的 Windows 安全版：stdout/stderr 写临时文件而非管道。

    子进程若再派生子进程（COM dllhost / MinerU 子进程）会继承管道句柄，
    导致 communicate() 在超时后仍永久阻塞；临时文件无句柄继承问题。
    """
    import tempfile as _tf

    with _tf.TemporaryFile() as _out, _tf.TemporaryFile() as _err:
        proc = subprocess.run(args, stdout=_out, stderr=_err, timeout=timeout)
        _out.seek(0)
        _err.seek(0)
        stdout = _out.read().decode(encoding, errors="replace")
        stderr = _err.read().decode(encoding, errors="replace")
    return proc, stdout, stderr


def _command_error_tail(stdout: str, stderr: str, limit: int = 1200) -> str:
    """外部 CLI 的最后几行通常才包含 traceback / root cause，避免日志只留下启动信息。"""
    output = (stderr or stdout).strip()
    return output[-limit:] if output else "<无错误输出>"
