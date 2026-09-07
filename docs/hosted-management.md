# Hosted gateway-management contract

Status: accepted for VocaGateway MCP v0.2

Last reviewed: 2026-09-05

## Purpose

Streamable HTTP mode gives authenticated agents a safe management interface to
one explicitly configured VocaGateway. Voca clients continue sending audio
directly to the gateway; the hosted MCP does not proxy transcription and does
not accept local paths.

## Tools

| Tool | Scope | Gateway operation | Safety behavior |
| --- | --- | --- | --- |
| `get_gateway_status` | `gateway:read` | `GET /health`, `GET /health/ready` | Returns only destination and readiness/capability fields |
| `get_gateway_config` | `gateway:read` | `GET /v1/admin/config` | Omits model paths and other filesystem values |
| `list_models` | `gateway:read` | `GET /v1/admin/models` | Supports existing catalog filters; omits unapproved fields |
| `get_operational_status` | `gateway:read` | `GET /v1/admin/status` | Omits paths, commit details, dependency paths, and raw metric history |
| `download_model` | `gateway:manage` | `POST /v1/admin/models/{id}/download` | Requires exact gateway confirmation |
| `cancel_model_download` | `gateway:manage` | `POST /v1/admin/models/{id}/cancel` | Requires exact gateway confirmation |
| `select_model` | `gateway:manage` | `POST /v1/admin/models/{id}/select` | Requires exact gateway confirmation and an installed preflight state |
| `update_engine_config` | `gateway:manage` | `PUT /v1/admin/config` | Restricts values to the gateway schema and requires exact gateway confirmation |

`tools/list` is served from the MCP process and does not contact VocaGateway.
Every successful mutation returns the configured gateway URL and resulting
gateway state.

## Authentication and network boundary

The hosted server accepts a required operator-provisioned manage token and an
optional, distinct read-only token. Tokens are compared in constant time. The
manage token receives both scopes; the read-only token receives only
`gateway:read`.

The MCP and gateway tokens are separate. The gateway credential remains only in
the MCP process environment and is never returned to callers. Startup rejects an
MCP token that equals the gateway credential. Non-loopback deployments require
an HTTPS public endpoint, and hosted mode requires HTTPS for a non-loopback
gateway destination. Outbound gateway calls ignore ambient HTTP proxy settings.
DNS-rebinding protection, Host and Origin allowlists, and a one-MiB MCP request
limit are enabled.

The current pre-shared token mode is suitable for self-hosted clients that can
send a configured bearer token. It does not issue tokens or implement an
interactive browser OAuth flow.

## Out of scope

- Hosted audio transcription or filesystem access
- Durable transcript history, search, or export
- Device-token administration
- Arbitrary custom-model URLs
- Model deletion until VocaGateway provides an atomic inactive-state check
- Gateway process restart, shutdown, or software updates
- A Voca-operated relay or identity service
