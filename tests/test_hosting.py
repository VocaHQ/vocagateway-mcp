from __future__ import annotations

import pytest

from vocagateway_mcp.hosting import MANAGE_SCOPE, READ_SCOPE, HostedSettings, StaticTokenVerifier

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
