"""Small, transport-independent client for the VocaGateway HTTP API."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class GatewayError(RuntimeError):
    """A safe error for MCP callers; it never includes response contents or secrets."""


class DestinationConfirmationRequired(GatewayError):
    """Raised before an audio file is opened when the configured gateway is not confirmed."""


_HTTP_ERROR_HINTS = {
    400: "The gateway rejected the request. Check the audio options and gateway version.",
    401: "The gateway rejected the bearer token. Verify VOCAGATEWAY_TOKEN.",
    403: "The gateway refused access. Verify the token permissions and gateway policy.",
    404: ("The requested gateway endpoint was not found. Verify the gateway URL and version."),
    411: "The gateway requires a Content-Length header, but the upload did not provide one.",
    413: "The audio file exceeds the gateway upload limit.",
    415: "The gateway does not support this audio type. Use WAV, M4A, CAF, WebM, or OGG.",
    422: (
        "The gateway could not process the audio. Check that it is non-empty, decodable, "
        "and within the duration limit."
    ),
    429: "The gateway is busy or rate-limiting requests. Wait briefly and retry.",
    503: "The gateway is running but its speech engine is not ready. Check get_gateway_status.",
}


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    """Connection details supplied explicitly by the MCP host environment."""

    url: str
    token: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _normalize_gateway_url(self.url, "VOCAGATEWAY_URL"))
        if not self.token.strip():
            raise ValueError("VOCAGATEWAY_TOKEN must not be empty.")
        object.__setattr__(self, "token", self.token.strip())

    @property
    def normalized_url(self) -> str:
        return self.url

    @classmethod
    def from_environment(cls) -> GatewaySettings:
        try:
            return cls(
                url=os.environ["VOCAGATEWAY_URL"],
                token=os.environ["VOCAGATEWAY_TOKEN"],
            )
        except KeyError as error:
            raise ValueError(
                "Set VOCAGATEWAY_URL and VOCAGATEWAY_TOKEN before starting vocagateway-mcp."
            ) from error


class GatewayClient:
    """HTTP API client shared by the stdio and future hosted MCP transports."""

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport

    async def status(self) -> dict[str, Any]:
        health = await self._get_json("/health", authenticated=False)
        readiness = await self._get_json("/health/ready", authenticated=False, allow_503=True)
        return {
            "gateway_url": self.settings.normalized_url,
            "engine": health.get("engine"),
            "engine_ready": health.get("engine_ready"),
            "streaming_supported": health.get("streaming_supported"),
            "languages": health.get("languages", []),
            "readiness": readiness.get("status"),
            "warmup_state": readiness.get("warmup_state"),
        }

    async def list_models(self) -> list[dict[str, Any]]:
        payload = await self._get_json("/v1/admin/models", authenticated=True)
        if not isinstance(payload, list):
            raise GatewayError("VocaGateway returned an invalid model-list response.")
        return [
            {
                "id": entry.get("id"),
                "label": entry.get("label"),
                "engine": entry.get("engine"),
                "state": entry.get("state"),
                "active": entry.get("active"),
                "languages": entry.get("languages"),
            }
            for entry in payload
            if isinstance(entry, dict)
        ]

    async def transcribe_file(
        self, file_path: str | Path, *, confirm_gateway_url: str
    ) -> dict[str, str]:
        """Send one completed local file only after its destination is confirmed exactly."""
        try:
            confirmed_url = _normalize_gateway_url(confirm_gateway_url, "confirm_gateway_url")
        except ValueError as error:
            raise DestinationConfirmationRequired(
                f"Audio was not read or sent. {error} Expected: {self.settings.normalized_url}"
            ) from error
        if confirmed_url != self.settings.normalized_url:
            raise DestinationConfirmationRequired(
                "Audio was not read or sent. Gateway destination mismatch: "
                f"confirmed {confirmed_url}, configured {self.settings.normalized_url}."
            )

        path = _validate_audio_path(file_path)
        mime_type = _audio_mime_type(path)
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.normalized_url,
                headers={"Authorization": f"Bearer {self.settings.token}"},
                timeout=httpx.Timeout(120.0, connect=10.0),
                transport=self._transport,
            ) as client:
                with path.open("rb") as audio_file:
                    response = await client.post(
                        "/v1/audio/transcriptions",
                        files={"file": (path.name, audio_file, mime_type)},
                        data={"response_format": "json"},
                    )
        except httpx.HTTPError as error:
            raise self._transport_error(error) from error
        except OSError as error:
            raise GatewayError(f"Could not read the audio file: {path}") from error
        payload = self._response_json(response)
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise GatewayError("VocaGateway returned an invalid transcription response.")
        return {"gateway_url": self.settings.normalized_url, "text": text}

    async def _get_json(self, path: str, *, authenticated: bool, allow_503: bool = False) -> Any:
        headers = {"Authorization": f"Bearer {self.settings.token}"} if authenticated else {}
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.normalized_url,
                headers=headers,
                timeout=httpx.Timeout(15.0, connect=5.0),
                transport=self._transport,
            ) as client:
                response = await client.get(path)
        except httpx.HTTPError as error:
            raise self._transport_error(error) from error
        if response.status_code == 503 and allow_503:
            return self._json_only(response)
        return self._response_json(response)

    @staticmethod
    def _json_only(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as error:
            raise GatewayError("VocaGateway returned an invalid JSON response.") from error

    def _response_json(self, response: httpx.Response) -> Any:
        if response.is_error:
            hint = _HTTP_ERROR_HINTS.get(
                response.status_code,
                "The gateway returned an error. Check its status and logs.",
            )
            raise GatewayError(f"VocaGateway returned HTTP {response.status_code}. {hint}")
        return self._json_only(response)

    def _transport_error(self, error: httpx.HTTPError) -> GatewayError:
        destination = self.settings.normalized_url
        if isinstance(error, httpx.TimeoutException):
            return GatewayError(
                f"VocaGateway at {destination} timed out. Verify it is ready and retry."
            )
        if isinstance(error, httpx.ConnectError):
            return GatewayError(
                f"Could not connect to VocaGateway at {destination}. "
                "Verify the gateway is running and the hostname and port are correct."
            )
        return GatewayError(
            f"Network request to VocaGateway at {destination} failed. "
            "Check the URL, TLS configuration, and gateway logs."
        )


def _normalize_gateway_url(value: str, field_name: str) -> str:
    """Validate and normalize a VocaGateway base URL without accepting ambiguity."""
    if not value:
        raise ValueError(f"{field_name} must not be empty.")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have leading or trailing whitespace.")
    if value.startswith(("'", '"')) or value.endswith(("'", '"')):
        raise ValueError(
            f"{field_name} contains quote characters. Enter the URL without surrounding quotes."
        )
    if any(character.isspace() for character in value):
        raise ValueError(f"{field_name} must not contain whitespace.")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} has an invalid hostname or port: {error}.") from error
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            f"{field_name} must be an absolute http(s) URL, for example http://127.0.0.1:8765."
        )
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not contain a query string or fragment.")
    if parsed.path not in {"", "/"}:
        raise ValueError(f"{field_name} must point to the gateway root, without a path.")

    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = f"{host}:{port}" if port is not None else host
    return urlunsplit((scheme, authority, "", "", ""))


def _validate_audio_path(file_path: str | Path) -> Path:
    """Return one unambiguous existing local audio path."""
    raw_path = os.fspath(file_path)
    if raw_path.startswith(("'", '"')) or raw_path.endswith(("'", '"')):
        raise GatewayError(
            "audio_file_path contains quote characters. Enter the absolute path without quotes."
        )
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise GatewayError("audio_file_path must be an absolute path.")
    path = candidate.resolve()
    if not path.exists():
        raise GatewayError(f"Audio file not found: {path}")
    if not path.is_file():
        raise GatewayError(f"Audio path is not a regular file: {path}")
    return path


def _audio_mime_type(path: Path) -> str:
    """Use VocaGateway's accepted MIME labels, including macOS's WAV alias."""
    detected = mimetypes.guess_type(path.name)[0]
    aliases = {
        "audio/x-wav": "audio/wav",
        "audio/x-m4a": "audio/m4a",
    }
    return aliases.get(detected or "", detected or "application/octet-stream")
