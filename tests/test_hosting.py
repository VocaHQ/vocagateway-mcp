from __future__ import annotations

import pytest

from vocagateway_mcp.hosting import (
    MANAGE_SCOPE,
    READ_SCOPE,
    HostedSettings,
    StaticTokenVerifier,
    validate_hosted_gateway,
)

MANAGE_TOKEN = "manage-token-with-at-least-thirty-two-characters"
READ_TOKEN = "read-token-with-at-least-thirty-two-characters"


def settings(**overrides) -> HostedSettings:
    values = {
        "host": "127.0.0.1",
        "port": 8000,
        "public_url": "http://127.0.0.1:8000/mcp",
        "access_token": MANAGE_TOKEN,
        "read_only_token": READ_TOKEN,
    }
    values.update(overrides)
    return HostedSettings(**values)


def test_hosted_settings_default_to_public_authority_allowlist() -> None:
    result = settings()

    assert result.allowed_hosts == ("127.0.0.1:8000",)
    assert result.allowed_origins == ()
    assert result.issuer_url == "http://127.0.0.1:8000/"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"host": "0.0.0.0"}, "must use https"),
        (
            {"public_url": "http://mcp.example.test/mcp"},
            "must use https",
        ),
        ({"public_url": "https://mcp.example.test/not-mcp"}, "must end in /mcp"),
        ({"access_token": "short"}, "at least 32"),
        ({"access_token": "é" * 32}, "printable ASCII"),
        ({"access_token": "a" * 16 + "\n" + "b" * 16}, "without whitespace"),
        ({"access_token": "a" * 513}, "at most 512"),
        ({"read_only_token": MANAGE_TOKEN}, "must differ"),
        ({"port": 70_000}, "between 1 and 65535"),
    ],
)
def test_hosted_settings_reject_unsafe_values(overrides: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        settings(**overrides)


def test_hosted_settings_accept_secure_remote_deployment() -> None:
    result = settings(
        host="0.0.0.0",
        port=443,
        public_url="https://mcp.example.test/mcp",
        allowed_hosts=("mcp.example.test",),
        allowed_origins=("https://app.example.test",),
    )

    assert result.allowed_hosts == ("mcp.example.test",)
    assert result.allowed_origins == ("https://app.example.test",)


def test_hosted_settings_build_valid_ipv6_loopback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOCAMCP_HOST", "::1")
    monkeypatch.setenv("VOCAMCP_ACCESS_TOKEN", MANAGE_TOKEN)

    result = HostedSettings.from_environment()

    assert result.public_url == "http://[::1]:8000/mcp"


@pytest.mark.asyncio
async def test_static_token_verifier_assigns_read_and_manage_scopes() -> None:
    verifier = StaticTokenVerifier(settings())

    manage = await verifier.verify_token(MANAGE_TOKEN)
    read = await verifier.verify_token(READ_TOKEN)

    assert manage is not None
    assert manage.scopes == [READ_SCOPE, MANAGE_SCOPE]
    assert read is not None
    assert read.scopes == [READ_SCOPE]
    assert await verifier.verify_token("invalid-token") is None
    assert await verifier.verify_token("é" * 32) is None
    assert await verifier.verify_token("a" * 513) is None


@pytest.mark.parametrize("mcp_token_name", ["access_token", "read_only_token"])
def test_hosted_gateway_rejects_reused_gateway_token(mcp_token_name: str) -> None:
    gateway_token = "gateway-token-with-at-least-thirty-two-characters"
    hosted = settings(**{mcp_token_name: gateway_token})

    with pytest.raises(ValueError, match=f"{mcp_token_name.upper()}"):
        validate_hosted_gateway(hosted, "https://gateway.example.test", gateway_token)


@pytest.mark.parametrize(
    "gateway_url",
    [
        "http://gateway.example.test",
        "http://192.0.2.10:8765",
    ],
)
def test_hosted_gateway_requires_https_for_non_loopback(gateway_url: str) -> None:
    with pytest.raises(ValueError, match="VOCAGATEWAY_URL must use https"):
        validate_hosted_gateway(settings(), gateway_url, "separate-gateway-token")


@pytest.mark.parametrize(
    "gateway_url",
    [
        "http://localhost:8765",
        "http://127.0.0.1:8765",
        "http://[::1]:8765",
        "https://gateway.example.test",
    ],
)
def test_hosted_gateway_accepts_secure_or_loopback_destination(gateway_url: str) -> None:
    validate_hosted_gateway(settings(), gateway_url, "separate-gateway-token")
