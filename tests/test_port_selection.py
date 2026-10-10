"""端口选择与设置接口。"""

import socket
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.config import config
from src.main import app
from src.services.port_selection import choose_start_port, port_available, valid_port


def test_port_validation_and_occupied_socket():
    assert valid_port("8787") == 8787
    assert valid_port("0") is None
    assert valid_port("65536") is None
    assert valid_port("87x") is None
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        assert not port_available("127.0.0.1", bound.getsockname()[1])


def test_interactive_selection_retries_occupied_port_and_saves(monkeypatch):
    inputs = iter(["2", "bad", "2", "8800", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    monkeypatch.setattr("src.services.port_selection.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("src.services.port_selection.sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("src.services.port_selection.next_available_port", lambda *_: 8801)
    monkeypatch.setattr("src.services.port_selection.port_available", lambda _host, port: port == 8800)
    monkeypatch.setattr("src.services.port_selection.env_file_writable", lambda: True)
    original_port = config.port
    try:
        with patch("src.services.port_selection.write_env_updates") as write:
            assert choose_start_port("127.0.0.1", 8787) == 8800
    finally:
        config.port = original_port
    write.assert_called_once_with({"PORT": "8800"})


def test_config_reports_running_port_and_rejects_occupied_port():
    client = TestClient(app)
    app.state.bound_host = "127.0.0.1"
    app.state.bound_port = 8787
    try:
        data = client.get("/api/v1/config").json()["data"]["server"]
        assert data["running_port"] == 8787
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            occupied = bound.getsockname()[1]
            check = client.get("/api/v1/config/ports/check", params={"port": occupied})
            assert check.json()["data"]["available"] is False
            update = client.patch("/api/v1/config", json={"server": {"port": occupied}})
            assert update.status_code == 400
    finally:
        del app.state.bound_host
        del app.state.bound_port
