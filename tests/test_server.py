from __future__ import annotations

import httpx
import pytest

from vocagateway_mcp.client import GatewayClient, GatewaySettings
from vocagateway_mcp.hosting import HostedSettings
from vocagateway_mcp.server import create_hosted_server, create_server

GATEWAY_SETTINGS = GatewaySettings(
    url="https://gateway.example.test",
    token="gateway-token-with-at-least-thirty-two-characters",
)
HOSTED_SETTINGS = HostedSettings(
    host="127.0.0.1",
    port=8000,
    public_url="http://127.0.0.1:8000/mcp",
    access_token="manage-token-with-at-least-thirty-two-characters",
    read_only_token="read-token-with-at-least-thirty-two-characters",
)


@pytest.mark.asyncio
async def test_local_and_hosted_servers_register_transport_specific_tools() -> None:
    local_client = GatewayClient(GATEWAY_SETTINGS, transport=httpx.MockTransport(lambda _: None))
    hosted_client = GatewayClient(GATEWAY_SETTINGS, transport=httpx.MockTransport(lambda _: None))
    try:
        local_names = {tool.name for tool in await create_server(local_client).list_tools()}
        hosted_tools = await create_hosted_server(hosted_client, HOSTED_SETTINGS).list_tools()
        hosted_names = {tool.name for tool in hosted_tools}
    finally:
        await local_client.aclose()
        await hosted_client.aclose()

    assert local_names == {"get_gateway_status", "list_models", "transcribe_file"}
    assert hosted_names == {
        "get_gateway_status",
        "get_gateway_config",
        "list_models",
        "get_operational_status",
        "download_model",
        "cancel_model_download",
        "select_model",
        "delete_model",
        "update_engine_config",
    }
    assert "transcribe_file" not in hosted_names
    delete_tool = next(tool for tool in hosted_tools if tool.name == "delete_model")
    assert delete_tool.annotations is not None
    assert delete_tool.annotations.destructiveHint is True
    config_tool = next(tool for tool in hosted_tools if tool.name == "update_engine_config")
    assert config_tool.inputSchema["properties"]["cpu_threads"]["minimum"] == 0
    assert config_tool.inputSchema["properties"]["cpu_threads"]["maximum"] == 256


@pytest.mark.asyncio
async def test_hosted_http_requires_bearer_authentication() -> None:
    client = GatewayClient(GATEWAY_SETTINGS, transport=httpx.MockTransport(lambda _: None))
    mcp = create_hosted_server(client, HOSTED_SETTINGS)
    app = mcp.streamable_http_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as http:
            response = await http.post(
                "/mcp",
                headers={"accept": "application/json, text/event-stream"},
                json=_initialize_request(),
            )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert "resource_metadata" in response.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_hosted_http_accepts_read_only_token_and_lists_management_tools() -> None:
    client = GatewayClient(GATEWAY_SETTINGS, transport=httpx.MockTransport(lambda _: None))
    mcp = create_hosted_server(client, HOSTED_SETTINGS)
    app = mcp.streamable_http_app()
    headers = {
        "authorization": ("Bearer read-token-with-at-least-thirty-two-characters"),
        "accept": "application/json, text/event-stream",
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as http:
            initialized = await http.post("/mcp", headers=headers, json=_initialize_request())
            listed = await http.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )

    assert initialized.status_code == 200
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "select_model" in names
    assert "transcribe_file" not in names


@pytest.mark.asyncio
async def test_read_only_token_cannot_call_management_tool() -> None:
    client = GatewayClient(GATEWAY_SETTINGS, transport=httpx.MockTransport(lambda _: None))
    mcp = create_hosted_server(client, HOSTED_SETTINGS)
    app = mcp.streamable_http_app()
    headers = {
        "authorization": "Bearer read-token-with-at-least-thirty-two-characters",
        "accept": "application/json, text/event-stream",
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as http:
            response = await http.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "download_model",
                        "arguments": {
                            "model_id": "small",
                            "confirm_gateway_url": "https://gateway.example.test",
                        },
                    },
                },
            )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "gateway:manage" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_hosted_health_route_is_public_and_contains_no_gateway_details() -> None:
    client = GatewayClient(GATEWAY_SETTINGS, transport=httpx.MockTransport(lambda _: None))
    mcp = create_hosted_server(client, HOSTED_SETTINGS)
    app = mcp.streamable_http_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as http:
            response = await http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_manage_token_can_call_confirmed_management_tool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/admin/models/small/download"
        return httpx.Response(200, json={"model_id": "small", "status": "downloading"})

    client = GatewayClient(GATEWAY_SETTINGS, transport=httpx.MockTransport(handler))
    mcp = create_hosted_server(client, HOSTED_SETTINGS)
    app = mcp.streamable_http_app()
    headers = {
        "authorization": "Bearer manage-token-with-at-least-thirty-two-characters",
        "accept": "application/json, text/event-stream",
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as http:
            response = await http.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "download_model",
                        "arguments": {
                            "model_id": "small",
                            "confirm_gateway_url": "https://gateway.example.test",
                        },
                    },
                },
            )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["status"] == "downloading"


def _initialize_request() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "hosted-test", "version": "1"},
        },
    }
