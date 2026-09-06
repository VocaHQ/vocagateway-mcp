#!/usr/bin/env python3
"""Exercise an installed hosted MCP executable over Streamable HTTP."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

MANAGE_TOKEN = "ci-manage-token-with-at-least-thirty-two-characters"
READ_TOKEN = "ci-read-token-with-at-least-thirty-two-characters"
EXPECTED_TOOLS = {
    "get_gateway_status",
    "get_gateway_config",
    "list_models",
    "get_operational_status",
    "download_model",
    "cancel_model_download",
    "select_model",
    "update_engine_config",
}


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_http.py /path/to/vocagateway-mcp")

    executable = Path(sys.argv[1]).resolve(strict=True)
    port = available_port()
    origin = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    environment.update(
        {
            "VOCAGATEWAY_URL": "https://gateway.example.test",
            "VOCAGATEWAY_TOKEN": "ci-gateway-token-with-at-least-thirty-two-characters",
            "VOCAMCP_HOST": "127.0.0.1",
            "VOCAMCP_PORT": str(port),
            "VOCAMCP_PUBLIC_URL": f"{origin}/mcp",
            "VOCAMCP_ACCESS_TOKEN": MANAGE_TOKEN,
            "VOCAMCP_READ_ONLY_TOKEN": READ_TOKEN,
        }
    )
    process = subprocess.Popen(
        [str(executable), "--transport", "streamable-http"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        with httpx.Client(base_url=origin, timeout=2.0) as client:
            wait_until_ready(client, process)
            unauthenticated = client.post(
                "/mcp",
                headers={"accept": "application/json, text/event-stream"},
                json=initialize_request(),
            )
            assert unauthenticated.status_code == 401

            headers = {
                "authorization": f"Bearer {READ_TOKEN}",
                "accept": "application/json, text/event-stream",
            }
            initialized = client.post("/mcp", headers=headers, json=initialize_request())
            initialized.raise_for_status()
            listed = client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            listed.raise_for_status()
            names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            assert names == EXPECTED_TOOLS, f"unexpected hosted tool set: {sorted(names)}"
            assert "transcribe_file" not in names
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    if process.returncode not in {0, -15}:
        assert process.stderr is not None
        raise subprocess.CalledProcessError(
            process.returncode or 1,
            process.args,
            stderr=process.stderr.read(),
        )
    print(f"Streamable HTTP MCP smoke passed: {', '.join(sorted(names))}")


def wait_until_ready(
    client: httpx.Client, process: subprocess.Popen, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            assert process.stderr is not None
            raise RuntimeError(f"hosted MCP exited during startup: {process.stderr.read()}")
        try:
            response = client.get("/health")
            if response.status_code == 200:
                assert response.json() == {"status": "ok"}
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise TimeoutError("hosted MCP did not become ready")


def initialize_request() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ci-http-smoke", "version": "1"},
        },
    }


if __name__ == "__main__":
    main()
