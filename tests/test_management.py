from __future__ import annotations

import json

import httpx
import pytest

from vocagateway_mcp.client import (
    GatewayChangeConfirmationRequired,
    GatewayClient,
    GatewayError,
    GatewaySettings,
)

TOKEN = "test-gateway-token-that-is-long-enough"
SETTINGS = GatewaySettings(url="https://gateway.example.test", token=TOKEN)


def transport_for(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_get_config_returns_only_management_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/admin/config"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(
            200,
            json={
                "engine": "whisperkit",
                "available_engines": ["whisperkit"],
                "compute_device": "auto",
                "private_path": "/Users/private/models",
            },
        )

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        result = await client.get_config()

    assert result == {
        "gateway_url": "https://gateway.example.test",
        "engine": "whisperkit",
        "available_engines": ["whisperkit"],
        "compute_device": "auto",
    }


@pytest.mark.asyncio
async def test_list_models_passes_filters_and_returns_safe_catalog_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/admin/models"
        assert request.url.params.get("installed_only") == "true"
        assert request.url.params.get_list("language") == ["en", "fr"]
        assert request.url.params.get_list("family") == ["Whisper"]
        assert request.url.params.get_list("engine") == ["whisperkit"]
        assert request.url.params.get("max_size") == "800mb"
        assert request.url.params.get("recommended_only") == "true"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "small",
                    "label": "Small",
                    "engine": "whisperkit",
                    "size_bytes": 461_000_000,
                    "state": "installed",
                    "active": True,
                    "supports_streaming": False,
                    "private_path": "/private/model.bin",
                }
            ],
        )

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        result = await client.list_models(
            installed_only=True,
            language=["en", "fr"],
            family=["Whisper"],
            engine=["whisperkit"],
            maximum_size="800mb",
            recommended_only=True,
        )

    assert result == [
        {
            "id": "small",
            "engine": "whisperkit",
            "label": "Small",
            "size_bytes": 461_000_000,
            "supports_streaming": False,
            "state": "installed",
            "active": True,
        }
    ]


@pytest.mark.asyncio
async def test_operational_status_removes_paths_and_metric_history() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "version": "1.2.3",
                "engine": {"id": "whisperkit", "name": "Tiny", "ready": True},
                "system": {
                    "os": "Darwin",
                    "arch": "arm64",
                    "ram_gb": 16,
                    "cpu_features": ["private-detail"],
                },
                "dependencies": [
                    {
                        "name": "FFmpeg",
                        "available": True,
                        "path": "/opt/homebrew/bin/ffmpeg",
                        "install_hint": None,
                    }
                ],
                "paths": {"data_dir": "/Users/private"},
                "setup": {"engine_ready": True},
                "metrics": {
                    "uptime_seconds": 60,
                    "successful_transcriptions": 2,
                    "history": [{"uptime_seconds": 30}],
                },
                "readiness": {"warmup_state": "complete"},
            },
        )

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        result = await client.operational_status()

    assert "paths" not in result
    assert "cpu_features" not in result["system"]
    assert "path" not in result["dependencies"][0]
    assert "history" not in result["metrics"]
    assert result["metrics"]["successful_transcriptions"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "suffix", "status"),
    [
        ("download_model", "/download", "downloading"),
        ("cancel_model_download", "/cancel", "cancelling"),
    ],
)
async def test_download_mutations_confirm_destination_and_use_expected_endpoint(
    method_name: str, suffix: str, status: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/v1/admin/models/small{suffix}"
        return httpx.Response(200, json={"model_id": "small", "status": status})

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        method = getattr(client, method_name)
        with pytest.raises(GatewayChangeConfirmationRequired, match="change was not sent"):
            await method("small", confirm_gateway_url="https://other.example.test")
        result = await method("small", confirm_gateway_url=SETTINGS.normalized_url)

    assert len(requests) == 1
    assert result == {
        "gateway_url": SETTINGS.normalized_url,
        "model_id": "small",
        "status": status,
    }


@pytest.mark.asyncio
async def test_select_model_requires_installed_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json=[{"id": "small", "state": "not_installed", "active": False}],
        )

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        with pytest.raises(GatewayError, match="Download it before selecting it"):
            await client.select_model("small", confirm_gateway_url=SETTINGS.normalized_url)


@pytest.mark.asyncio
async def test_select_model_preflights_and_returns_resulting_engine() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "small", "state": "installed", "active": False}],
            )
        assert request.url.path == "/v1/admin/models/small/select"
        return httpx.Response(
            200,
            json={"engine": {"id": "whisperkit", "name": "Small", "ready": True}},
        )

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        result = await client.select_model("small", confirm_gateway_url=SETTINGS.normalized_url)

    assert methods == ["GET", "POST"]
    assert result["model_id"] == "small"
    assert result["engine"]["ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "message"),
    [
        ({"id": "small", "state": "installed", "active": True}, "active model"),
        ({"id": "small", "state": "downloading", "active": False}, "downloading model"),
        ({"id": "small", "state": "not_installed", "active": False}, "not installed"),
    ],
)
async def test_delete_model_rejects_unsafe_state(model: dict, message: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=[model])

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        with pytest.raises(GatewayError, match=message):
            await client.delete_model(
                "small",
                confirm_model_id="small",
                confirm_gateway_url=SETTINGS.normalized_url,
            )


@pytest.mark.asyncio
async def test_delete_model_requires_exact_id_before_network_access() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be used")

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        with pytest.raises(GatewayChangeConfirmationRequired, match="confirm_model_id"):
            await client.delete_model(
                "small",
                confirm_model_id="other",
                confirm_gateway_url=SETTINGS.normalized_url,
            )


@pytest.mark.asyncio
async def test_model_mutation_rejects_path_like_id_before_network_access() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be used")

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        with pytest.raises(GatewayError, match="model_id is invalid"):
            await client.download_model(
                "../private-model",
                confirm_gateway_url=SETTINGS.normalized_url,
            )


@pytest.mark.asyncio
async def test_delete_model_preflights_then_deletes() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "small", "state": "installed", "active": False}],
            )
        assert request.method == "DELETE"
        return httpx.Response(200, json={"deleted": True})

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        result = await client.delete_model(
            "small",
            confirm_model_id="small",
            confirm_gateway_url=SETTINGS.normalized_url,
        )

    assert methods == ["GET", "DELETE"]
    assert result == {
        "gateway_url": SETTINGS.normalized_url,
        "model_id": "small",
        "deleted": True,
    }


@pytest.mark.asyncio
async def test_update_engine_config_sends_only_validated_configuration() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/v1/admin/config"
        assert json.loads(request.content) == {
            "engine": "faster-whisper",
            "compute_device": "cuda",
            "compute_type": "float16",
            "cpu_threads": 0,
        }
        return httpx.Response(
            200,
            json={"engine": {"id": "faster-whisper", "name": "Small", "ready": True}},
        )

    async with GatewayClient(SETTINGS, transport=transport_for(handler)) as client:
        result = await client.update_engine_config(
            engine="faster-whisper",
            compute_device="cuda",
            compute_type="float16",
            cpu_threads=0,
            confirm_gateway_url=SETTINGS.normalized_url,
        )

    assert result["gateway_url"] == SETTINGS.normalized_url
    assert result["engine"]["ready"] is True
