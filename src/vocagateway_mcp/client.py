"""Small, transport-independent client for the VocaGateway HTTP API."""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


class GatewayError(RuntimeError):
    """A safe error for MCP callers; it never includes response contents or secrets."""


class DestinationConfirmationRequired(GatewayError):
    """Raised before an audio file is opened when the configured gateway is not confirmed."""


class GatewayChangeConfirmationRequired(GatewayError):
    """Raised before a gateway mutation when its target is not confirmed exactly."""


_HTTP_ERROR_HINTS = {
    400: "The gateway rejected the request. Check the tool arguments and gateway version.",
    401: "The gateway rejected the bearer token. Verify VOCAGATEWAY_TOKEN.",
    403: "The gateway refused access. Verify the token permissions and gateway policy.",
    404: ("The requested gateway endpoint was not found. Verify the gateway URL and version."),
    409: "The gateway state conflicts with this operation. Refresh its status and retry.",
    411: "The gateway requires a Content-Length header, but the upload did not provide one.",
    413: "The audio file exceeds the gateway upload limit.",
    415: "The gateway does not support this audio type. Use WAV, M4A, CAF, WebM, or OGG.",
    422: "The gateway rejected the supplied values. Check the tool arguments and target state.",
    429: "The gateway is busy or rate-limiting requests. Wait briefly and retry.",
    503: "The gateway is running but its speech engine is not ready. Check get_gateway_status.",
}

_MODEL_FIELDS = (
    "id",
    "engine",
    "label",
    "size_bytes",
    "languages",
    "quality",
    "family",
    "description",
    "source",
    "supports_streaming",
    "license_name",
    "commercial_use",
    "detects_language_automatically",
    "language_names",
    "language_codes",
    "state",
    "active",
    "recommended",
    "progress",
    "downloaded_bytes",
    "total_bytes",
)
_CONFIG_FIELDS = (
    "engine",
    "available_engines",
    "compute_device",
    "compute_type",
    "cpu_threads",
)
_METRIC_FIELDS = (
    "uptime_seconds",
    "queue_depth",
    "active_transcriptions",
    "concurrency_limit",
    "successful_transcriptions",
    "failed_transcriptions",
    "rejected_transcriptions",
    "average_latency_ms",
    "last_latency_ms",
    "normalization_ms",
    "model_load_ms",
    "inference_ms",
    "audio_duration_ms",
    "real_time_factor",
    "peak_memory_mb",
)
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:+-]+$")


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
    """HTTP API client shared by the stdio and hosted MCP transports."""

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=self.settings.normalized_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            transport=transport,
        )

    async def __aenter__(self) -> GatewayClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release pooled connections owned by this gateway client."""
        await self._http.aclose()

    async def status(self) -> dict[str, Any]:
        health = await self._request_json("GET", "/health", authenticated=False)
        readiness = await self._request_json(
            "GET", "/health/ready", authenticated=False, allowed_statuses={503}
        )
        if not isinstance(health, dict) or not isinstance(readiness, dict):
            raise GatewayError("VocaGateway returned an invalid status response.")
        return {
            "gateway_url": self.settings.normalized_url,
            "engine": health.get("engine"),
            "engine_ready": health.get("engine_ready"),
            "streaming_supported": health.get("streaming_supported"),
            "languages": health.get("languages", []),
            "readiness": readiness.get("status"),
            "warmup_state": readiness.get("warmup_state"),
        }

    async def get_config(self) -> dict[str, Any]:
        payload = await self._request_json("GET", "/v1/admin/config", authenticated=True)
        config = _require_mapping(payload, "configuration")
        return {
            "gateway_url": self.settings.normalized_url,
            **_only_fields(config, _CONFIG_FIELDS),
        }

    async def list_models(
        self,
        *,
        installed_only: bool = False,
        language: list[str] | None = None,
        family: list[str] | None = None,
        engine: list[str] | None = None,
        maximum_size: str | None = None,
        recommended_only: bool = False,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = []
        if installed_only:
            params.append(("installed_only", "true"))
        params.extend(("language", value) for value in language or [])
        params.extend(("family", value) for value in family or [])
        params.extend(("engine", value) for value in engine or [])
        if maximum_size:
            params.append(("max_size", maximum_size))
        if recommended_only:
            params.append(("recommended_only", "true"))
        payload = await self._request_json(
            "GET", "/v1/admin/models", authenticated=True, params=params
        )
        if not isinstance(payload, list):
            raise GatewayError("VocaGateway returned an invalid model-list response.")
        return [_only_fields(entry, _MODEL_FIELDS) for entry in payload if isinstance(entry, dict)]

    async def operational_status(self) -> dict[str, Any]:
        payload = await self._request_json("GET", "/v1/admin/status", authenticated=True)
        status = _require_mapping(payload, "operational-status")
        dependencies = status.get("dependencies")
        safe_dependencies = [
            _only_fields(entry, ("name", "available", "install_hint"))
            for entry in dependencies or []
            if isinstance(entry, dict)
        ]
        system = status.get("system")
        safe_system = (
            _only_fields(
                system,
                (
                    "os",
                    "arch",
                    "chip",
                    "ram_gb",
                    "is_apple_silicon",
                    "logical_cpus",
                    "effective_cpus",
                    "containerized",
                    "accelerators",
                ),
            )
            if isinstance(system, dict)
            else None
        )
        metrics = status.get("metrics")
        safe_metrics = _only_fields(metrics, _METRIC_FIELDS) if isinstance(metrics, dict) else None
        return {
            "gateway_url": self.settings.normalized_url,
            "status": status.get("status"),
            "version": status.get("version"),
            "engine": _safe_engine(status.get("engine")),
            "system": safe_system,
            "dependencies": safe_dependencies,
            "setup": _safe_setup(status.get("setup")),
            "metrics": safe_metrics,
            "readiness": _safe_readiness(status.get("readiness")),
        }

    async def download_model(self, model_id: str, *, confirm_gateway_url: str) -> dict[str, Any]:
        model_id = _validate_model_id(model_id)
        self._confirm_gateway_change(confirm_gateway_url)
        payload = await self._request_json(
            "POST",
            f"/v1/admin/models/{_model_path(model_id)}/download",
            authenticated=True,
        )
        result = _require_mapping(payload, "model-download")
        return {
            "gateway_url": self.settings.normalized_url,
            **_only_fields(result, ("model_id", "status")),
        }

    async def cancel_model_download(
        self, model_id: str, *, confirm_gateway_url: str
    ) -> dict[str, Any]:
        model_id = _validate_model_id(model_id)
        self._confirm_gateway_change(confirm_gateway_url)
        payload = await self._request_json(
            "POST",
            f"/v1/admin/models/{_model_path(model_id)}/cancel",
            authenticated=True,
        )
        result = _require_mapping(payload, "model-download cancellation")
        return {
            "gateway_url": self.settings.normalized_url,
            **_only_fields(result, ("model_id", "status")),
        }

    async def select_model(self, model_id: str, *, confirm_gateway_url: str) -> dict[str, Any]:
        model_id = _validate_model_id(model_id)
        self._confirm_gateway_change(confirm_gateway_url)
        model = await self._find_model(model_id)
        if model.get("state") != "installed":
            raise GatewayError(
                f"Model {model_id} is not installed. Download it before selecting it."
            )
        payload = await self._request_json(
            "POST",
            f"/v1/admin/models/{_model_path(model_id)}/select",
            authenticated=True,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        result = _require_mapping(payload, "model-selection")
        return {
            "gateway_url": self.settings.normalized_url,
            "model_id": model_id,
            "engine": _safe_engine(result.get("engine")),
        }

    async def delete_model(
        self,
        model_id: str,
        *,
        confirm_model_id: str,
        confirm_gateway_url: str,
    ) -> dict[str, Any]:
        model_id = _validate_model_id(model_id)
        if confirm_model_id != model_id:
            raise GatewayChangeConfirmationRequired(
                "Model was not deleted. confirm_model_id must exactly match model_id."
            )
        self._confirm_gateway_change(confirm_gateway_url)
        model = await self._find_model(model_id)
        if model.get("active") is True:
            raise GatewayError("The active model cannot be deleted. Select another model first.")
        if model.get("state") == "downloading":
            raise GatewayError("A downloading model cannot be deleted. Cancel its download first.")
        if model.get("state") != "installed":
            raise GatewayError(f"Model {model_id} is not installed.")
        payload = await self._request_json(
            "DELETE",
            f"/v1/admin/models/{_model_path(model_id)}",
            authenticated=True,
        )
        result = _require_mapping(payload, "model-deletion")
        return {
            "gateway_url": self.settings.normalized_url,
            "model_id": model_id,
            "deleted": result.get("deleted"),
        }

    async def update_engine_config(
        self,
        *,
        engine: str,
        compute_device: str,
        compute_type: str,
        cpu_threads: int,
        confirm_gateway_url: str,
    ) -> dict[str, Any]:
        self._confirm_gateway_change(confirm_gateway_url)
        payload = await self._request_json(
            "PUT",
            "/v1/admin/config",
            authenticated=True,
            timeout=httpx.Timeout(120.0, connect=10.0),
            json={
                "engine": engine,
                "compute_device": compute_device,
                "compute_type": compute_type,
                "cpu_threads": cpu_threads,
            },
        )
        result = _require_mapping(payload, "configuration-update")
        return {
            "gateway_url": self.settings.normalized_url,
            "engine": _safe_engine(result.get("engine")),
        }

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
            with path.open("rb") as audio_file:
                payload = await self._request_json(
                    "POST",
                    "/v1/audio/transcriptions",
                    authenticated=True,
                    timeout=httpx.Timeout(120.0, connect=10.0),
                    files={"file": (path.name, audio_file, mime_type)},
                    data={"response_format": "json"},
                )
        except OSError as error:
            raise GatewayError(f"Could not read the audio file: {path}") from error
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise GatewayError("VocaGateway returned an invalid transcription response.")
        return {"gateway_url": self.settings.normalized_url, "text": text}

    async def _find_model(self, model_id: str) -> dict[str, Any]:
        models = await self.list_models()
        for model in models:
            if model.get("id") == model_id:
                return model
        raise GatewayError(f"Unknown model: {model_id}")

    def _confirm_gateway_change(self, confirm_gateway_url: str) -> None:
        try:
            confirmed_url = _normalize_gateway_url(confirm_gateway_url, "confirm_gateway_url")
        except ValueError as error:
            raise GatewayChangeConfirmationRequired(
                f"Gateway change was not sent. {error} Expected: {self.settings.normalized_url}"
            ) from error
        if confirmed_url != self.settings.normalized_url:
            raise GatewayChangeConfirmationRequired(
                "Gateway change was not sent. Gateway destination mismatch: "
                f"confirmed {confirmed_url}, configured {self.settings.normalized_url}."
            )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool,
        allowed_statuses: set[int] | None = None,
        timeout: httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> Any:
        """Make one gateway request and convert failures to safe, actionable errors."""
        headers = {"Authorization": f"Bearer {self.settings.token}"} if authenticated else {}
        request_options = dict(kwargs)
        if timeout is not None:
            request_options["timeout"] = timeout
        try:
            response = await self._http.request(
                method,
                path,
                headers=headers,
                **request_options,
            )
        except httpx.HTTPError as error:
            raise self._transport_error(error) from error

        if response.is_error and response.status_code not in (allowed_statuses or set()):
            hint = _HTTP_ERROR_HINTS.get(
                response.status_code,
                "The gateway returned an error. Check its status and logs.",
            )
            raise GatewayError(f"VocaGateway returned HTTP {response.status_code}. {hint}")
        try:
            return response.json()
        except ValueError as error:
            raise GatewayError("VocaGateway returned an invalid JSON response.") from error

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


def _only_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: payload[field] for field in fields if field in payload}


def _require_mapping(payload: Any, response_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GatewayError(f"VocaGateway returned an invalid {response_name} response.")
    return payload


def _validate_model_id(model_id: str) -> str:
    if not model_id or model_id != model_id.strip():
        raise GatewayError("model_id must be non-empty and have no surrounding whitespace.")
    if len(model_id) > 300 or _MODEL_ID_PATTERN.fullmatch(model_id) is None:
        raise GatewayError("model_id is invalid.")
    return model_id


def _model_path(model_id: str) -> str:
    return quote(model_id, safe="")


def _safe_engine(payload: Any) -> dict[str, Any] | None:
    return _only_fields(payload, ("id", "name", "ready")) if isinstance(payload, dict) else None


def _safe_setup(payload: Any) -> dict[str, Any] | None:
    fields = (
        "token_configured",
        "ffmpeg_available",
        "engine_binary_available",
        "model_installed",
        "engine_ready",
    )
    return _only_fields(payload, fields) if isinstance(payload, dict) else None


def _safe_readiness(payload: Any) -> dict[str, Any] | None:
    fields = ("probe_age_seconds", "warmup_state", "warmed_bytes")
    return _only_fields(payload, fields) if isinstance(payload, dict) else None


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
