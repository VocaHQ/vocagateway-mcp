<div align="center">

# vocagateway-mcp

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Status: v0.2](https://img.shields.io/badge/status-v0.2-yellow)](#current-scope)
[![Privacy: self-hosted](https://img.shields.io/badge/privacy-self--hosted-success)](#privacy-and-security)

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/VocaHQ/vocagateway-mcp/pulls)
[![GitHub Issues](https://img.shields.io/github/issues/VocaHQ/vocagateway-mcp)](https://github.com/VocaHQ/vocagateway-mcp/issues)
[![Discord](https://img.shields.io/discord/1538633755877580810?logo=discord&logoColor=white&label=Discord)](https://discord.gg/t6muquAJbm)
[![VocaHQ](https://img.shields.io/badge/VocaHQ-vocahq.com-1a7f4e)](https://vocahq.com)

</div>

MCP tools for a [VocaGateway](https://github.com/VocaHQ/vocagateway) you run.
Local stdio mode can inspect the gateway and send a completed local audio file.
Authenticated Streamable HTTP mode lets remote agents observe and manage the
gateway without accepting audio or exposing gateway credentials.

This is not the speech engine and it is not on-device dictation. Audio leaves
the machine running the MCP server and goes only to the VocaGateway URL the user
configured. There is no Voca account, hosted Voca relay, or cloud transcription
service.

## Current scope

Two transport-specific modes are available:

- **Local stdio:** the original status, model-list, and completed-file
  transcription tools.
- **Streamable HTTP:** nine authenticated gateway observation and management
  tools. It does not expose transcription or local filesystem access.

### Local stdio tools

| Tool | Purpose | Gateway access |
| --- | --- | --- |
| `get_gateway_status` | Show the configured destination, readiness, engine, streaming capability, and languages | Public health endpoints |
| `list_models` | List available and installed models without changing gateway state | Bearer-authenticated |
| `transcribe_file` | Upload one completed local audio file and return its transcript | Bearer-authenticated |

`transcribe_file` requires `confirm_gateway_url` to match the configured
destination before it opens the file. This gives the MCP host a chance to show
where audio will be sent and prevents an unnoticed destination change.

### Hosted management tools

| Tool | Required scope | Purpose |
| --- | --- | --- |
| `get_gateway_status` | `gateway:read` | Read destination, readiness, and active engine |
| `get_gateway_config` | `gateway:read` | Read engine and compute configuration without filesystem paths |
| `list_models` | `gateway:read` | Filter the catalog and inspect install/progress/capability state |
| `get_operational_status` | `gateway:read` | Read redacted system, dependency, queue, latency, and readiness state |
| `download_model` | `gateway:manage` | Start a catalog model download |
| `cancel_model_download` | `gateway:manage` | Cancel an active model download |
| `select_model` | `gateway:manage` | Activate an installed model and wait for warmup |
| `delete_model` | `gateway:manage` | Delete an inactive installed model after exact confirmation |
| `update_engine_config` | `gateway:manage` | Change engine and compute settings |

Hosted mutations require `confirm_gateway_url` to match the configured gateway.
Deletion also requires `confirm_model_id` to match exactly and refuses to delete
an active or downloading model.

Not included: hosted transcription, live transcription streaming, transcript
history, token administration, custom model URLs, or a Voca cloud relay.

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

## Run the hosted management server

Generate separate, random MCP tokens; do not reuse the VocaGateway bearer token:

```sh
export VOCAGATEWAY_URL=https://gateway.example.com
export VOCAGATEWAY_TOKEN='private-gateway-service-token'

export VOCAMCP_PUBLIC_URL=https://mcp.example.com/mcp
export VOCAMCP_HOST=0.0.0.0
export VOCAMCP_PORT=8000
export VOCAMCP_ACCESS_TOKEN='random-manage-token-at-least-32-characters'
export VOCAMCP_READ_ONLY_TOKEN='different-read-token-at-least-32-characters'
export VOCAMCP_ALLOWED_HOSTS=mcp.example.com

uv run vocagateway-mcp --transport streamable-http
```

The endpoint is `/mcp`; unauthenticated process health is `/health`. The manage
token receives `gateway:read` and `gateway:manage`. The optional read-only token
receives only `gateway:read`. Every MCP request must send one as an
`Authorization: Bearer ...` header.

| Hosted variable | Required | Purpose |
| --- | --- | --- |
| `VOCAMCP_ACCESS_TOKEN` | Yes | Pre-shared manage token, at least 32 characters |
| `VOCAMCP_READ_ONLY_TOKEN` | No | Distinct pre-shared read-only token |
| `VOCAMCP_PUBLIC_URL` | Remote deployments | Absolute public endpoint ending in `/mcp` |
| `VOCAMCP_HOST` | No | Bind host; defaults to `127.0.0.1` |
| `VOCAMCP_PORT` | No | Bind port; defaults to `8000` |
| `VOCAMCP_ALLOWED_HOSTS` | No | Comma-separated HTTP Host allowlist; defaults to the public authority |
| `VOCAMCP_ALLOWED_ORIGINS` | Browser clients only | Comma-separated browser Origin allowlist |

Non-loopback deployments require an HTTPS public URL. Terminate TLS in a trusted
reverse proxy or private-network ingress and forward traffic to the MCP process.
The server enables DNS-rebinding protection and accepts request bodies up to one
MiB because hosted tools carry management JSON, not audio.

This release uses operator-provisioned bearer tokens. It publishes MCP protected
resource metadata but does not implement an interactive authorization server or
issue tokens. Configure the token manually in the MCP client. A deployment that
requires browser-based OAuth should integrate a real authorization server before
public availability.

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

The audio path is local to the machine running this stdio MCP server. Hosted
management mode intentionally does not register an audio or filesystem tool.

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
uv run python scripts/smoke_http.py .venv/bin/vocagateway-mcp
```

Tests use `httpx.MockTransport`; they do not require a live gateway, speech
engine, recording, or network connection. GitHub Actions repeats lint, format,
unit, clean-wheel install, stdio and authenticated Streamable HTTP protocol
smokes, and container-build checks.

## Container

The image can run either transport. Pass secrets at runtime, not at build time.
For stdio:

```sh
docker build --tag vocagateway-mcp:dev .
docker run --rm -i \
  -e VOCAGATEWAY_URL \
  -e VOCAGATEWAY_TOKEN \
  vocagateway-mcp:dev
```

For Streamable HTTP behind an HTTPS ingress:

```sh
docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -e VOCAGATEWAY_URL \
  -e VOCAGATEWAY_TOKEN \
  -e VOCAMCP_PUBLIC_URL \
  -e VOCAMCP_ACCESS_TOKEN \
  -e VOCAMCP_READ_ONLY_TOKEN \
  -e VOCAMCP_ALLOWED_HOSTS \
  -e VOCAMCP_HOST=0.0.0.0 \
  vocagateway-mcp:dev --transport streamable-http
```

For a gateway running on the macOS host, remember that `127.0.0.1` inside Docker
is the container itself. Use an explicitly configured host address such as
`host.docker.internal` only when that network path is intended and protected.

## Architecture

```text
MCP host
  ├── stdio → local vocagateway-mcp ── file transcription ─┐
  └── HTTPS → hosted vocagateway-mcp ── management only ───┤
                                                          ▼
                                              user-operated VocaGateway
                                                          │
                                                          ▼
                                               selected speech engine
```

`GatewayClient` contains configuration, validation, and redacted errors. It owns
one reusable `httpx.AsyncClient`, so status checks, model discovery, and uploads
share connection pooling and one request/error path. The FastMCP lifespan closes
that client cleanly when the server exits.

The FastMCP layers are transport-specific: local stdio retains file
transcription, while hosted Streamable HTTP exposes only management tools. Both
reuse the same gateway client and safe error behavior.

## Privacy and security

- The server does not log bearer tokens, audio bytes, or transcript content.
- Hosted MCP tokens and the gateway token are separate credentials.
- Hosted responses omit gateway filesystem paths and raw metric history.
- Read-only callers cannot execute model or engine mutations.
- Host and browser Origin allowlists are enforced before MCP tools run.
- Gateway response bodies are not echoed in HTTP errors.
- Audio is not opened until its destination URL is confirmed.
- URL credentials and empty tokens are rejected during configuration.
- Tests contain no real recordings, transcripts, tokens, or private hostnames.
- Remote MCP deployments require an HTTPS public URL and bearer authentication.

See [AGENTS.md](AGENTS.md) for contributor and coding-agent rules.

## Project origin

This repository was opened from
[VocaGateway issue #38](https://github.com/VocaHQ/vocagateway/issues/38). The
transcription tool uses the OpenAI-compatible
`POST /v1/audio/transcriptions` endpoint introduced by
[VocaGateway PR #37](https://github.com/VocaHQ/vocagateway/pull/37).

License: [AGPL-3.0](LICENSE).
