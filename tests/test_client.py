from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from vocagateway_mcp.client import (
    DestinationConfirmationRequired,
    GatewayClient,
    GatewayError,
    GatewaySettings,
)

TOKEN = "test-token-that-is-long-enough-for-a-gateway"
SETTINGS = GatewaySettings(url="https://gateway.example.test/", token=TOKEN)


def transport_for(handler):
    return httpx.MockTransport(handler)


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.close_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"engine": "whisper.cpp", "engine_ready": True})
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/v1/admin/models":
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected path: {request.url.path}")

    async def aclose(self) -> None:
        self.close_count += 1


def test_settings_normalize_gateway_url_and_token() -> None:
    settings = GatewaySettings(
        url="HTTP://Gateway.Example.Test:8765/",
        token=f"  {TOKEN}  ",
    )

    assert settings.normalized_url == "http://gateway.example.test:8765"
    assert settings.token == TOKEN


@pytest.mark.asyncio
async def test_gateway_client_does_not_inherit_ambient_proxy_settings() -> None:
    client = GatewayClient(SETTINGS, transport=httpx.MockTransport(lambda _: None))
    try:
        assert client._http._trust_env is False
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ('"http://127.0.0.1:8765"', "quote characters"),
        ("127.0.0.1:8765", "absolute http"),
        ("http://127.0.0.1:not-a-port", "invalid hostname or port"),
        ("http://user:secret@127.0.0.1:8765", "must not contain credentials"),
        ("http://127.0.0.1:8765/gateway", "gateway root"),
        ("http://127.0.0.1:8765?token=secret", "query string or fragment"),
    ],
)
def test_settings_reject_malformed_or_ambiguous_gateway_urls(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        GatewaySettings(url=url, token=TOKEN)


@pytest.mark.asyncio
async def test_status_reports_destination_and_unready_engine() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"engine": "whisper.cpp", "engine_ready": False})
        assert request.url.path == "/health/ready"
        return httpx.Response(503, json={"status": "not_ready", "warmup_state": "pending"})

    result = await GatewayClient(SETTINGS, transport=transport_for(handler)).status()

    assert result == {
        "gateway_url": "https://gateway.example.test",
        "engine": "whisper.cpp",
        "engine_ready": False,
        "streaming_supported": None,
        "languages": [],
        "readiness": "not_ready",
        "warmup_state": "pending",
    }


@pytest.mark.asyncio
async def test_client_reuses_one_transport_and_closes_it_once() -> None:
    transport = CountingTransport()

    async with GatewayClient(SETTINGS, transport=transport) as client:
        await client.status()
        await client.list_models()
        assert transport.close_count == 0

    assert [request.url.path for request in transport.requests] == [
        "/health",
        "/health/ready",
        "/v1/admin/models",
    ]
    assert "authorization" not in transport.requests[0].headers
    assert "authorization" not in transport.requests[1].headers
    assert transport.requests[2].headers["authorization"] == f"Bearer {TOKEN}"
    assert transport.close_count == 1


@pytest.mark.asyncio
async def test_list_models_uses_bearer_auth_and_strips_unneeded_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/admin/models"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "small",
                    "label": "Small",
                    "engine": "whisperkit",
                    "state": "installed",
                    "active": True,
                    "languages": "English",
                    "source_url": "https://example.test/ignored",
                }
            ],
        )

    result = await GatewayClient(SETTINGS, transport=transport_for(handler)).list_models()

    assert result == [
        {
            "id": "small",
            "label": "Small",
            "engine": "whisperkit",
            "state": "installed",
            "active": True,
            "languages": "English",
        }
    ]


@pytest.mark.asyncio
async def test_transcription_does_not_open_audio_until_destination_is_confirmed(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"not opened")

    with pytest.raises(DestinationConfirmationRequired, match="Audio was not read or sent"):
        await GatewayClient(SETTINGS).transcribe_file(
            audio, confirm_gateway_url="https://other.test"
        )


@pytest.mark.asyncio
async def test_transcription_explains_quoted_confirmation_url(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"not opened")

    with pytest.raises(DestinationConfirmationRequired, match="quote characters") as error:
        await GatewayClient(SETTINGS).transcribe_file(
            audio,
            confirm_gateway_url='"https://gateway.example.test"',
        )

    assert "Expected: https://gateway.example.test" in str(error.value)


@pytest.mark.asyncio
async def test_transcription_explains_quoted_audio_path(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"not opened")

    with pytest.raises(GatewayError, match="absolute path without quotes"):
        await GatewayClient(SETTINGS).transcribe_file(
            f'"{audio}"',
            confirm_gateway_url="https://gateway.example.test",
        )


@pytest.mark.asyncio
async def test_transcription_uploads_file_and_returns_only_text(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert b"Content-Type: audio/wav" in request.content
        assert b"audio bytes" in request.content
        return httpx.Response(200, json={"text": "private transcript"})

    result = await GatewayClient(SETTINGS, transport=transport_for(handler)).transcribe_file(
        audio, confirm_gateway_url="https://gateway.example.test"
    )

    assert result == {"gateway_url": "https://gateway.example.test", "text": "private transcript"}


@pytest.mark.asyncio
async def test_transcription_explains_connection_failure(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed", request=request)

    with pytest.raises(GatewayError, match="Verify the gateway is running") as error:
        await GatewayClient(SETTINGS, transport=transport_for(handler)).transcribe_file(
            audio,
            confirm_gateway_url="https://gateway.example.test",
        )

    assert "All connection attempts failed" not in str(error.value)


@pytest.mark.asyncio
async def test_transcription_explains_timeout(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("low-level timeout", request=request)

    with pytest.raises(GatewayError, match="timed out. Verify it is ready") as error:
        await GatewayClient(SETTINGS, transport=transport_for(handler)).transcribe_file(
            audio,
            confirm_gateway_url="https://gateway.example.test",
        )

    assert "low-level timeout" not in str(error.value)


@pytest.mark.asyncio
async def test_transcription_explains_gateway_not_ready(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio bytes")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "sensitive details"}})

    with pytest.raises(GatewayError, match="speech engine is not ready") as error:
        await GatewayClient(SETTINGS, transport=transport_for(handler)).transcribe_file(
            audio,
            confirm_gateway_url="https://gateway.example.test",
        )

    assert "sensitive details" not in str(error.value)


@pytest.mark.asyncio
async def test_gateway_errors_do_not_echo_response_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"token was {TOKEN}")

    with pytest.raises(GatewayError, match="Verify VOCAGATEWAY_TOKEN") as error:
        await GatewayClient(SETTINGS, transport=transport_for(handler)).list_models()

    assert TOKEN not in str(error.value)
