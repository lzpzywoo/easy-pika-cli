from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from pikpak_downloader.ai_api import create_app
from pikpak_downloader.config import AppConfig


@pytest.fixture
async def api_client(app_config: AppConfig):
    app = create_app(app_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-api-key"}


@pytest.mark.asyncio
async def test_health_no_auth(api_client: AsyncClient) -> None:
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_tools_requires_auth(api_client: AsyncClient) -> None:
    resp = await api_client.get("/v1/tools")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tools_with_auth(api_client: AsyncClient) -> None:
    resp = await api_client.get("/v1/tools", headers=_auth_headers())
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tools"]}
    assert "relay_magnet" in names
    assert "offline_add" in names


@pytest.mark.asyncio
async def test_parse_endpoint(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/v1/parse",
        headers=_auth_headers(),
        json={"text": "magnet:?xt=urn:btih:abc"},
    )
    assert resp.status_code == 200
    assert resp.json()["links"] == ["magnet:?xt=urn:btih:abc"]


@pytest.mark.asyncio
async def test_relay_missing_magnet(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/v1/relay",
        headers=_auth_headers(),
        json={},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_relay_success(api_client: AsyncClient) -> None:
    mock_client = MagicMock()
    mock_token_mgr = MagicMock()
    mock_token_mgr.refresh = AsyncMock()
    relay_result = MagicMock(
        task_id="t1",
        file_ids=["f1"],
        local_paths=[],
        cleaned=False,
    )

    with (
        patch(
            "pikpak_downloader.ai_api.load_session_async",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch("pikpak_downloader.ai_api.TokenManager", return_value=mock_token_mgr),
        patch(
            "pikpak_downloader.ai_api.relay_magnet",
            new_callable=AsyncMock,
            return_value=relay_result,
        ),
    ):
        resp = await api_client.post(
            "/v1/relay",
            headers=_auth_headers(),
            json={"magnet": "magnet:?xt=urn:btih:abc"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "t1"
    assert body["file_ids"] == ["f1"]


@pytest.mark.asyncio
async def test_offline_add_requires_url(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/v1/offline/add",
        headers=_auth_headers(),
        json={},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_models_endpoint(api_client: AsyncClient) -> None:
    resp = await api_client.get("/v1/models", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "easy-pika-cli"


@pytest.mark.asyncio
async def test_api_key_via_x_api_key_header(app_config: AppConfig) -> None:
    app = create_app(app_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/tools", headers={"X-API-Key": "test-api-key"})
    assert resp.status_code == 200
