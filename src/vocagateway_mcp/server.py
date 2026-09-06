"""Local stdio and hosted Streamable HTTP adapters for VocaGateway."""

from __future__ import annotations

import argparse
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .client import GatewayClient, GatewaySettings
from .hosting import (
    MANAGE_SCOPE,
    READ_SCOPE,
    HostedSettings,
    StaticTokenVerifier,
    validate_hosted_gateway,
)

EngineName = Literal[
    "auto",
    "vocamac",
    "handy",
    "whisper.cpp",
    "whisperkit",
    "faster-whisper",
    "moonshine",
    "sherpa-onnx",
    "mlx-audio",
]
ComputeDevice = Literal["auto", "cpu", "cuda"]
ComputeType = Literal["auto", "int8", "int8_float16", "float16", "float32"]
MaximumSize = Literal["100mb", "300mb", "800mb", "1500mb"]

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
MUTATION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


def create_server(client: GatewayClient) -> FastMCP:
    """Create the backward-compatible local stdio server."""

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    mcp = FastMCP("VocaGateway", lifespan=lifespan)

    @mcp.tool()
    async def get_gateway_status() -> dict:
        """Show the configured VocaGateway destination, readiness, and active engine."""
        return await client.status()

    @mcp.tool()
    async def list_models() -> list[dict]:
        """List available and installed VocaGateway models without changing any gateway state."""
        return await client.list_models()

    @mcp.tool()
    async def transcribe_file(audio_file_path: str, confirm_gateway_url: str) -> dict:
        """Transcribe a completed local audio file through the configured gateway.

        This sends audio to the configured VocaGateway. Call get_gateway_status first,
        show its gateway_url to the user, and pass that exact URL as confirm_gateway_url.
        """
        return await client.transcribe_file(
            audio_file_path, confirm_gateway_url=confirm_gateway_url
        )

    return mcp


def create_hosted_server(client: GatewayClient, settings: HostedSettings) -> FastMCP:
    """Create the authenticated, management-only Streamable HTTP server."""

    validate_hosted_gateway(
        settings,
        client.settings.normalized_url,
        client.settings.token,
    )

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    mcp = FastMCP(
        "VocaGateway",
        instructions=(
            "Manage the configured VocaGateway. Read current state before changing it, show "
            "the gateway URL and proposed change to the user, and obtain approval before "
            "calling a state-changing tool. This hosted server does not accept audio."
        ),
        host=settings.host,
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=1024 * 1024,
        token_verifier=StaticTokenVerifier(settings),
        auth=AuthSettings(
            issuer_url=settings.issuer_url,
            resource_server_url=settings.public_url,
            required_scopes=[READ_SCOPE],
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=list(settings.allowed_origins),
        ),
        lifespan=lifespan,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def mcp_health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def get_gateway_status() -> dict[str, object]:
        """Show the configured VocaGateway destination, readiness, and active engine."""
        _require_scope(READ_SCOPE)
        return await client.status()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def get_gateway_config() -> dict[str, object]:
        """Show the configured engine and compute settings without filesystem paths."""
        _require_scope(READ_SCOPE)
        return await client.get_config()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def list_models(
        installed_only: bool = False,
        language: list[str] | None = None,
        family: list[str] | None = None,
        engine: list[str] | None = None,
        maximum_size: MaximumSize | None = None,
        recommended_only: bool = False,
    ) -> list[dict[str, object]]:
        """Filter the model catalog and show install, active, capability, and progress state."""
        _require_scope(READ_SCOPE)
        return await client.list_models(
            installed_only=installed_only,
            language=language,
            family=family,
            engine=engine,
            maximum_size=maximum_size,
            recommended_only=recommended_only,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def get_operational_status() -> dict[str, object]:
        """Show redacted runtime, dependency, queue, latency, and readiness information."""
        _require_scope(READ_SCOPE)
        return await client.operational_status()

    @mcp.tool(annotations=MUTATION, structured_output=True)
    async def download_model(model_id: str, confirm_gateway_url: str) -> dict[str, object]:
        """Start a catalog model download after the user confirms the gateway URL."""
        _require_scope(MANAGE_SCOPE)
        return await client.download_model(
            model_id,
            confirm_gateway_url=confirm_gateway_url,
        )

    @mcp.tool(annotations=MUTATION, structured_output=True)
    async def cancel_model_download(model_id: str, confirm_gateway_url: str) -> dict[str, object]:
        """Cancel a model download after the user confirms the gateway URL."""
        _require_scope(MANAGE_SCOPE)
        return await client.cancel_model_download(
            model_id,
            confirm_gateway_url=confirm_gateway_url,
        )

    @mcp.tool(annotations=MUTATION, structured_output=True)
    async def select_model(model_id: str, confirm_gateway_url: str) -> dict[str, object]:
        """Activate an installed model after the user confirms the gateway URL."""
        _require_scope(MANAGE_SCOPE)
        return await client.select_model(
            model_id,
            confirm_gateway_url=confirm_gateway_url,
        )

    @mcp.tool(annotations=MUTATION, structured_output=True)
    async def update_engine_config(
        engine: EngineName,
        confirm_gateway_url: str,
        compute_device: ComputeDevice = "auto",
        compute_type: ComputeType = "auto",
        cpu_threads: Annotated[int, Field(ge=0, le=256)] = 0,
    ) -> dict[str, object]:
        """Change engine compute settings after the user confirms the gateway URL."""
        _require_scope(MANAGE_SCOPE)
        return await client.update_engine_config(
            engine=engine,
            compute_device=compute_device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            confirm_gateway_url=confirm_gateway_url,
        )

    return mcp


def _require_scope(scope: str) -> None:
    access_token = get_access_token()
    if access_token is None or scope not in access_token.scopes:
        raise PermissionError(f"This tool requires the {scope} authorization scope.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the VocaGateway MCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.environ.get("VOCAMCP_TRANSPORT", "stdio"),
    )
    args = parser.parse_args()
    gateway_settings = GatewaySettings.from_environment()
    if args.transport == "stdio":
        create_server(GatewayClient(gateway_settings)).run(transport="stdio")
        return
    hosted_settings = HostedSettings.from_environment()
    validate_hosted_gateway(
        hosted_settings,
        gateway_settings.normalized_url,
        gateway_settings.token,
    )
    create_hosted_server(GatewayClient(gateway_settings), hosted_settings).run(
        transport="streamable-http"
    )


if __name__ == "__main__":
    main()
