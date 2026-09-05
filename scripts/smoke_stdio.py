#!/usr/bin/env python3
"""Exercise an installed vocagateway-mcp executable over stdio without a gateway."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

EXPECTED_TOOLS = {"get_gateway_status", "list_models", "transcribe_file"}


def send_message(stream: TextIO, message: dict) -> None:
    stream.write(json.dumps(message) + "\n")
    stream.flush()


def read_response(stream: TextIO, request_id: int, *, timeout: float = 10.0) -> dict:
    """Wait for one JSON-RPC response without closing stdin and racing server shutdown."""
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not selector.select(remaining):
                break
            line = stream.readline()
            if not line:
                break
            response = json.loads(line)
            if response.get("id") == request_id:
                return response
    finally:
        selector.close()
    raise TimeoutError(f"MCP server did not return JSON-RPC response {request_id}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_stdio.py /path/to/vocagateway-mcp")

    executable = Path(sys.argv[1]).resolve(strict=True)
    environment = os.environ.copy()
    environment.update(
        {
            "VOCAGATEWAY_URL": "https://gateway.example.test",
            "VOCAGATEWAY_TOKEN": "ci-token-with-at-least-thirty-two-characters",
        }
    )
    process = subprocess.Popen(
        [str(executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=environment,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        send_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ci-smoke", "version": "1"},
                },
            },
        )
        initialize = read_response(process.stdout, 1)

        send_message(
            process.stdin,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        send_message(
            process.stdin,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        listed = read_response(process.stdout, 2)
    finally:
        process.stdin.close()

    try:
        return_code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        raise
    if return_code:
        raise subprocess.CalledProcessError(
            return_code,
            process.args,
            stderr=process.stderr.read(),
        )

    assert initialize["result"]["serverInfo"]["name"] == "VocaGateway"
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert names == EXPECTED_TOOLS, f"unexpected tool set: {sorted(names)}"
    print(f"stdio MCP smoke passed: {', '.join(sorted(names))}")


if __name__ == "__main__":
    main()
