"""stdio MCP adapter. The API client can be reused by a hosted transport later."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .client import GatewayClient, GatewaySettings


def create_server(client: GatewayClient) -> FastMCP:
    mcp = FastMCP("VocaGateway")

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


def main() -> None:
    create_server(GatewayClient(GatewaySettings.from_environment())).run(transport="stdio")


if __name__ == "__main__":
    main()
