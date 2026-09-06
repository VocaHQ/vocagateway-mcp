"""Validated Streamable HTTP settings and pre-shared bearer authentication."""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from mcp.server.auth.provider import AccessToken

READ_SCOPE = "gateway:read"
MANAGE_SCOPE = "gateway:manage"
_MINIMUM_TOKEN_LENGTH = 32


@dataclass(frozen=True, slots=True)
class HostedSettings:
    """Network and authentication settings for the hosted MCP transport."""

    host: str
    port: int
    public_url: str
    access_token: str
    read_only_token: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        host = self.host.strip()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if not host or any(character.isspace() for character in host):
            raise ValueError("VOCAMCP_HOST must be a hostname or IP address without whitespace.")
        if not 1 <= self.port <= 65535:
            raise ValueError("VOCAMCP_PORT must be between 1 and 65535.")
        public_url = _normalize_public_url(self.public_url)
        access_token = _validate_token(self.access_token, "VOCAMCP_ACCESS_TOKEN")
        read_only_token = (
            _validate_token(self.read_only_token, "VOCAMCP_READ_ONLY_TOKEN")
            if self.read_only_token is not None
            else None
        )
        if read_only_token is not None and secrets.compare_digest(read_only_token, access_token):
            raise ValueError("VOCAMCP_READ_ONLY_TOKEN must differ from VOCAMCP_ACCESS_TOKEN.")
        public_host = urlsplit(public_url).hostname or ""
        if (not _is_loopback_host(host) or not _is_loopback_host(public_host)) and urlsplit(
            public_url
        ).scheme != "https":
            raise ValueError("VOCAMCP_PUBLIC_URL must use https for a non-loopback deployment.")

        object.__setattr__(self, "host", host)
        object.__setattr__(self, "public_url", public_url)
        object.__setattr__(self, "access_token", access_token)
        object.__setattr__(self, "read_only_token", read_only_token)
        object.__setattr__(
            self,
            "allowed_hosts",
            self.allowed_hosts or _default_allowed_hosts(public_url),
        )
        object.__setattr__(self, "allowed_origins", tuple(self.allowed_origins))

    @classmethod
    def from_environment(cls) -> HostedSettings:
        host = os.environ.get("VOCAMCP_HOST", "127.0.0.1")
        raw_port = os.environ.get("VOCAMCP_PORT", "8000")
        try:
            port = int(raw_port)
        except ValueError as error:
            raise ValueError("VOCAMCP_PORT must be an integer.") from error
        public_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        public_url = os.environ.get("VOCAMCP_PUBLIC_URL", f"http://{public_host}:{port}/mcp")
        try:
            access_token = os.environ["VOCAMCP_ACCESS_TOKEN"]
        except KeyError as error:
            raise ValueError(
                "Set VOCAMCP_ACCESS_TOKEN before starting the Streamable HTTP server."
            ) from error
        return cls(
            host=host,
            port=port,
            public_url=public_url,
            access_token=access_token,
            read_only_token=os.environ.get("VOCAMCP_READ_ONLY_TOKEN"),
            allowed_hosts=_comma_separated("VOCAMCP_ALLOWED_HOSTS"),
            allowed_origins=_comma_separated("VOCAMCP_ALLOWED_ORIGINS"),
        )

    @property
    def issuer_url(self) -> str:
        parsed = urlsplit(self.public_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


class StaticTokenVerifier:
    """Constant-time verifier for operator-provisioned hosted MCP tokens."""

    def __init__(self, settings: HostedSettings) -> None:
        self.settings = settings

    async def verify_token(self, token: str) -> AccessToken | None:
        if secrets.compare_digest(token, self.settings.access_token):
            return AccessToken(
                token=token,
                client_id="vocamcp-operator",
                scopes=[READ_SCOPE, MANAGE_SCOPE],
                resource=self.settings.public_url,
            )
        if self.settings.read_only_token and secrets.compare_digest(
            token, self.settings.read_only_token
        ):
            return AccessToken(
                token=token,
                client_id="vocamcp-read-only",
                scopes=[READ_SCOPE],
                resource=self.settings.public_url,
            )
        return None


def _validate_token(value: str, field_name: str) -> str:
    if value != value.strip():
        raise ValueError(f"{field_name} must not have surrounding whitespace.")
    if len(value) < _MINIMUM_TOKEN_LENGTH:
        raise ValueError(f"{field_name} must contain at least {_MINIMUM_TOKEN_LENGTH} characters.")
    return value


def _normalize_public_url(value: str) -> str:
    if not value or value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("VOCAMCP_PUBLIC_URL must be a URL without surrounding whitespace.")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise ValueError(f"VOCAMCP_PUBLIC_URL has an invalid hostname or port: {error}.") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("VOCAMCP_PUBLIC_URL must be an absolute http(s) URL ending in /mcp.")
    if parsed.username or parsed.password:
        raise ValueError("VOCAMCP_PUBLIC_URL must not contain credentials.")
    if parsed.path.rstrip("/") != "/mcp" or parsed.query or parsed.fragment:
        raise ValueError("VOCAMCP_PUBLIC_URL must end in /mcp without a query or fragment.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "/mcp", "", ""))


def _default_allowed_hosts(public_url: str) -> tuple[str, ...]:
    authority = urlsplit(public_url).netloc
    return (authority,)


def _comma_separated(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False
