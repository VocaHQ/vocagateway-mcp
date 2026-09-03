"""Small, transport-independent client for the VocaGateway HTTP API."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class GatewayError(RuntimeError):
    """A safe error for MCP callers; it never includes response contents or secrets."""


class DestinationConfirmationRequired(GatewayError):
    """Raised before an audio file is opened when the configured gateway is not confirmed."""


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    """Connection details supplied explicitly by the MCP host environment."""

    url: str
    token: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("VOCAGATEWAY_URL must be an absolute http(s) URL.")
        if parsed.username or parsed.password:
            raise ValueError("VOCAGATEWAY_URL must not contain credentials.")
        if not self.token.strip():
            raise ValueError("VOCAGATEWAY_TOKEN must not be empty.")

    @property
    def normalized_url(self) -> str:
        return self.url.rstrip("/")

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
        if confirm_gateway_url.rstrip("/") != self.settings.normalized_url:
            raise DestinationConfirmationRequired(
                "Audio was not read or sent. Confirm the configured gateway URL exactly: "
                f"{self.settings.normalized_url}"
            )

        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise GatewayError("audio_file_path must be an existing regular file.")
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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
        except OSError as error:
            raise GatewayError("The selected audio file could not be read.") from error
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
            raise GatewayError("Could not reach the configured VocaGateway.") from error
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
            raise GatewayError(f"VocaGateway request failed with HTTP {response.status_code}.")
        return self._json_only(response)
