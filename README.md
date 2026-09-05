<div align="center">

# vocagateway-mcp

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Status: local v0.1](https://img.shields.io/badge/status-local%20v0.1-yellow)](#current-scope)
[![Privacy: self-hosted](https://img.shields.io/badge/privacy-self--hosted-success)](#privacy-and-security)

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/VocaHQ/vocagateway-mcp/pulls)
[![GitHub Issues](https://img.shields.io/github/issues/VocaHQ/vocagateway-mcp)](https://github.com/VocaHQ/vocagateway-mcp/issues)
[![Discord](https://img.shields.io/discord/1538633755877580810?logo=discord&logoColor=white&label=Discord)](https://discord.gg/t6muquAJbm)
[![VocaHQ](https://img.shields.io/badge/VocaHQ-vocahq.com-1a7f4e)](https://vocahq.com)

</div>

MCP tools for a [VocaGateway](https://github.com/VocaHQ/vocagateway) you run.
The server lets an MCP client inspect gateway readiness and models, then send a
completed local audio file to that explicitly configured gateway for
transcription.

This is not the speech engine and it is not on-device dictation. Audio leaves
the machine running the MCP server and goes only to the VocaGateway URL the user
configured. There is no Voca account, hosted Voca relay, or cloud transcription
service.

## Current scope

The v0.1 milestone is a **local stdio MCP server**. A self-hosted Streamable HTTP
transport may reuse the same client core later, but is not implemented yet.

| Tool | Purpose | Gateway access |
| --- | --- | --- |
| `get_gateway_status` | Show the configured destination, readiness, engine, streaming capability, and languages | Public health endpoints |
| `list_models` | List available and installed models without changing gateway state | Bearer-authenticated |
| `transcribe_file` | Upload one completed local audio file and return its transcript | Bearer-authenticated |

`transcribe_file` requires `confirm_gateway_url` to match the configured
destination before it opens the file. This gives the MCP host a chance to show
where audio will be sent and prevents an unnoticed destination change.

Not included in v0.1: live transcription streaming, token administration, model
download/delete/select, transcript history, arbitrary URL ingestion, or a Voca
cloud relay.

## Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A reachable VocaGateway with an existing bearer token
- Node.js/`npx` only for the optional MCP Inspector
- Docker only for the container build

## Install

```sh
git clone https://github.com/VocaHQ/vocagateway-mcp.git
cd vocagateway-mcp
uv sync --locked --all-groups
```

Configure the gateway destination and token in the MCP host environment. Do not
put either value in source control:

```sh
export VOCAGATEWAY_URL=http://127.0.0.1:8765
export VOCAGATEWAY_TOKEN='your-existing-gateway-token'
uv run vocagateway-mcp
```

An stdio server waits silently for an MCP host on stdin. Use `Ctrl+C` to stop a
manual launch.

### Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `VOCAGATEWAY_URL` | Yes | Absolute `http` or `https` gateway URL; credentials in the URL are rejected |
| `VOCAGATEWAY_TOKEN` | Yes | Existing gateway bearer token; never included in returned errors |

## Test with MCP Inspector

With a local VocaGateway running at `127.0.0.1:8765`, use the included wrapper:

```sh
npx @modelcontextprotocol/inspector bash \
  /absolute/path/to/vocagateway-mcp/scripts/inspect-local.sh
```

The wrapper defaults to the loopback gateway and reads
`~/.config/vocagateway/token` without printing it. Explicit environment values
still take precedence.

In the Inspector:

1. Connect and open **Tools**.
2. Call `get_gateway_status`; confirm the displayed `gateway_url`.
3. Call `list_models`.
4. Call `transcribe_file` with raw form values—do not include quote characters:

```json
{
  "audio_file_path": "/absolute/path/to/recording.wav",
  "confirm_gateway_url": "http://127.0.0.1:8765"
}
```

The audio path is local to the machine running this stdio MCP server. A hosted
server will need a different, remote-safe audio input contract.

### Error guidance

The MCP returns remediation-oriented errors without echoing gateway response
bodies:

| Error | What to check |
| --- | --- |
| `confirm_gateway_url contains quote characters` | In Inspector form mode, paste the raw URL without `"` characters |
| `Gateway destination mismatch` | Call `get_gateway_status` and confirm that exact `gateway_url` |
| `Could not connect to VocaGateway` | Start the gateway and verify its hostname and port |
| `HTTP 401` | Verify `VOCAGATEWAY_TOKEN` matches the running gateway |
| `HTTP 404` | Verify the base URL and upgrade to a gateway with `/v1/audio/transcriptions` |
| `HTTP 413` / `415` / `422` | Check the upload size, supported audio type, duration, and whether the file is decodable |
| `HTTP 503` | Call `get_gateway_status`; the selected speech engine is not ready |

Configured and confirmed gateway URLs must be absolute `http` or `https` URLs
pointing at the gateway root. Credentials, paths, query strings, fragments,
whitespace, invalid ports, and surrounding quotes are rejected explicitly.

## Development and verification

```sh
uv sync --locked --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Build a wheel, then exercise the current environment's MCP initialization and
tool discovery:

```sh
uv build --wheel
uv run python scripts/smoke_stdio.py .venv/bin/vocagateway-mcp
```

Tests use `httpx.MockTransport`; they do not require a live gateway, speech
engine, recording, or network connection. GitHub Actions repeats lint, format,
unit, clean-wheel install, stdio protocol smoke, and container-build checks.

## Container

The image currently exposes the same stdio server. Pass secrets at runtime, not
at build time:

```sh
docker build --tag vocagateway-mcp:dev .
docker run --rm -i \
  -e VOCAGATEWAY_URL \
  -e VOCAGATEWAY_TOKEN \
  vocagateway-mcp:dev
```

For a gateway running on the macOS host, remember that `127.0.0.1` inside Docker
is the container itself. Use an explicitly configured host address such as
`host.docker.internal` only when that network path is intended and protected.

## Architecture

```text
MCP host
  └── stdio → vocagateway-mcp
                 └── HTTP + bearer token → user-operated VocaGateway
                                                └── selected local speech engine
```

`GatewayClient` contains configuration, HTTP, validation, and redacted errors.
The FastMCP layer is deliberately thin so a future authenticated Streamable HTTP
transport can reuse the same behavior without duplicating gateway logic.

## Privacy and security

- The server does not log bearer tokens, audio bytes, or transcript content.
- Gateway response bodies are not echoed in HTTP errors.
- Audio is not opened until its destination URL is confirmed.
- URL credentials and empty tokens are rejected during configuration.
- Tests contain no real recordings, transcripts, tokens, or private hostnames.
- A local HTTP deployment should bind to `127.0.0.1`; a future remote MCP mode
  must require authentication, TLS or a private network, and Origin/Host checks.

See [AGENTS.md](AGENTS.md) for contributor and coding-agent rules.

## Project origin

This repository was opened from
[VocaGateway issue #38](https://github.com/VocaHQ/vocagateway/issues/38). The
transcription tool uses the OpenAI-compatible
`POST /v1/audio/transcriptions` endpoint introduced by
[VocaGateway PR #37](https://github.com/VocaHQ/vocagateway/pull/37).

License: [AGPL-3.0](LICENSE).
