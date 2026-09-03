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
async def test_transcription_uploads_file_and_returns_only_text(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert b"audio bytes" in request.content
        return httpx.Response(200, json={"text": "private transcript"})

    result = await GatewayClient(SETTINGS, transport=transport_for(handler)).transcribe_file(
        audio, confirm_gateway_url="https://gateway.example.test"
    )

    assert result == {"gateway_url": "https://gateway.example.test", "text": "private transcript"}


@pytest.mark.asyncio
async def test_gateway_errors_do_not_echo_response_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"token was {TOKEN}")

    with pytest.raises(GatewayError, match="HTTP 401") as error:
        await GatewayClient(SETTINGS, transport=transport_for(handler)).list_models()

    assert TOKEN not in str(error.value)
