<div align="center">

# vocagateway-mcp

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Status: under development](https://img.shields.io/badge/status-under%20development-yellow)](#vocagateway-mcp)
[![Privacy: self-hosted, not on-device](https://img.shields.io/badge/privacy-self--hosted%20%7C%20not%20on--device-success)](#vocagateway-mcp)

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/VocaHQ/vocagateway-mcp/pulls)
[![GitHub Issues](https://img.shields.io/github/issues/VocaHQ/vocagateway-mcp)](https://github.com/VocaHQ/vocagateway-mcp/issues)
[![Discord](https://img.shields.io/discord/1538633755877580810?logo=discord&logoColor=white&label=Discord)](https://discord.gg/t6muquAJbm)
[![Follow on X](https://img.shields.io/badge/Follow%20%40vocahq-000000?style=flat&logo=x&logoColor=white)](https://x.com/vocahq)
[![VocaHQ](https://img.shields.io/badge/VocaHQ-vocahq.com-1a7f4e)](https://vocahq.com)

</div>

MCP server for a self-hosted [VocaGateway](https://github.com/VocaHQ/vocagateway).

This talks to a gateway you run. It is not the gateway, and it is not on-device dictation. Audio leaves the machine running the MCP server and goes to the gateway you pointed it at. There is no Voca account and no hosted Voca relay.

## Development

The initial implementation is a local stdio MCP server. It is intentionally a
thin client over the gateway HTTP API so the same core can later power a
self-hosted Streamable HTTP transport.

```sh
uv sync --all-groups
uv run ruff check .
uv run pytest
```

Configure the destination and its existing bearer token in the MCP host
environment. Do not put either value in source control:

```sh
export VOCAGATEWAY_URL=https://gateway.example.com
export VOCAGATEWAY_TOKEN='your-existing-gateway-token'
uv run vocagateway-mcp
```

`transcribe_file` requires `confirm_gateway_url` to exactly match the configured
destination before it opens an audio file. The server does not log bearer tokens,
audio bytes, or transcript content.

### Interactive Inspector test

With a local VocaGateway running at `127.0.0.1:8765`, launch the Inspector with
the included wrapper. It loads the existing local gateway token without printing
it and avoids relying on an editable-package console-script wrapper:

```sh
npx @modelcontextprotocol/inspector bash \
  /absolute/path/to/vocagateway-mcp/scripts/inspect-local.sh
```

Connect, then call `get_gateway_status`, `list_models`, and `transcribe_file`.

Container images use the same stdio entry point:

```sh
docker build --tag vocagateway-mcp:dev .
docker run --rm -i \
  -e VOCAGATEWAY_URL \
  -e VOCAGATEWAY_TOKEN \
  vocagateway-mcp:dev
```

v1 covers:

- Gateway health/readiness and which engine is active
- Installed/available models
- Transcribe a local audio file through that gateway

Use the existing bearer token, and show the gateway destination before any audio is sent. Once [vocagateway#37](https://github.com/VocaHQ/vocagateway/pull/37) is on main, prefer `POST /v1/audio/transcriptions` for that transcribe call.

Leave out of v1: streaming, token admin, model download/delete/select, and any cloud relay.

License is [AGPL-3.0](LICENSE).

Opened from [vocagateway#38](https://github.com/VocaHQ/vocagateway/issues/38).
