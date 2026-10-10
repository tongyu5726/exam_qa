"""交互式启动端口选择与绑定预检查。"""

from __future__ import annotations

import socket
import sys

from src.config import config
from src.services.env_store import env_file_writable, write_env_updates


def valid_port(value: str) -> int | None:
    if not value.isdecimal():
        return None
    port = int(value)
    return port if 1 <= port <= 65535 else None


def port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return True
    except OSError:
        return False


def next_available_port(host: str, start: int) -> int | None:
    for port in range(max(1, start), min(65535, start + 99) + 1):
        if port_available(host, port):
            return port
    return None


def choose_start_port(host: str, default_port: int) -> int | None:
    """TTY 中选择本次端口；无交互输入时沿用已有配置。"""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return default_port

    while True:
        suggestion = next_available_port(host, default_port + 1)
        status = "可用" if port_available(host, default_port) else "已占用"
        sys.stdout.write(
            f"\n溯知 · 启动端口\n"
            f"  1. 使用配置端口 {default_port}（{status}）\n"
            f"  2. 输入其他端口\n"
            + (f"  3. 使用空闲端口 {suggestion}\n" if suggestion else "")
            + "  0. 退出\n"
        )
        try:
            choice = input("请选择 [1]: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            return None
        if choice == "0":
            return None
        if choice == "1":
            port = default_port
        elif choice == "3" and suggestion:
            port = suggestion
        elif choice == "2":
            try:
                port = valid_port(input("端口号 (1-65535): ").strip())
            except (EOFError, KeyboardInterrupt):
                sys.stdout.write("\n")
                return None
            if port is None:
                sys.stdout.write("请输入 1-65535 的整数。\n")
                continue
        else:
            sys.stdout.write("无效选项。\n")
            continue
        if not port_available(host, port):
            sys.stdout.write(f"端口 {port} 已被占用或系统保留，请重选。\n")
            continue
        if port != default_port:
            try:
                save = input("设为以后启动的默认端口？[Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                save = "n"
            if save in ("", "y", "yes"):
                if env_file_writable():
                    write_env_updates({"PORT": str(port)})
                    config.port = port
                    sys.stdout.write("已保存到 .env。\n")
                else:
                    sys.stdout.write(".env 不可写，本次仍使用所选端口。\n")
        return port
