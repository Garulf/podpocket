import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from podpocket import cli


def test_serve_starts_uvicorn_with_factory(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert calls == [
        (
            ("podpocket.app:create_app_from_env",),
            {
                "factory": True,
                "host": "0.0.0.0",
                "port": 9000,
                "proxy_headers": True,
                "forwarded_allow_ips": "*",
            },
        )
    ]


def test_unknown_command_exits():
    with pytest.raises(SystemExit):
        cli.main(["frobnicate"])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_server_boots_and_reports_health(tmp_path):
    port = free_port()
    env = {
        **os.environ,
        "PODPOCKET_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "PODPOCKET_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
        "PODPOCKET_CACHE_DIR": str(tmp_path / "cache"),
        "PODPOCKET_RUN_SCHEDULER": "false",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "podpocket.cli", "serve", "--port", str(port)], env=env
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            assert server.poll() is None, f"server exited with code {server.returncode}"
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except httpx.TransportError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        assert response.json() == {"status": "ok"}
    finally:
        server.terminate()
        server.wait(timeout=10)
