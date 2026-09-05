#!/usr/bin/env python3
"""Exercise an installed vocagateway-mcp executable over stdio without a gateway."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED_TOOLS = {"get_gateway_status", "list_models", "transcribe_file"}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_stdio.py /path/to/vocagateway-mcp")

    executable = Path(sys.argv[1]).resolve(strict=True)
    messages = [
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
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"
    environment = os.environ.copy()
    environment.update(
        {
            "VOCAGATEWAY_URL": "https://gateway.example.test",
            "VOCAGATEWAY_TOKEN": "ci-token-with-at-least-thirty-two-characters",
        }
    )
    completed = subprocess.run(
        [str(executable)],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        env=environment,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    initialize = next(response for response in responses if response.get("id") == 1)
    listed = next(response for response in responses if response.get("id") == 2)

    assert initialize["result"]["serverInfo"]["name"] == "VocaGateway"
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert names == EXPECTED_TOOLS, f"unexpected tool set: {sorted(names)}"
    print(f"stdio MCP smoke passed: {', '.join(sorted(names))}")


if __name__ == "__main__":
    main()
