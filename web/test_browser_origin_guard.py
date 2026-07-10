import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from core.app_factory import create_base_app  # noqa: E402


def _client(
    base_url: str = "https://omarchy.tail0eeded.ts.net:8443",
    client_address: tuple[str, int] = ("198.51.100.20", 50000),
) -> TestClient:
    app = create_base_app()

    @app.get("/api/settings")
    async def get_settings():
        return {"ok": True}

    @app.post("/api/settings")
    async def save_settings():
        return {"ok": True}

    return TestClient(app, base_url=base_url, client=client_address)


def test_cross_site_fetch_metadata_rejects_unsafe_request():
    with _client() as client:
        response = client.post(
            "/api/settings",
            content='{"publish_hook":"deploy"}',
            headers={
                "content-type": "text/plain",
                "origin": "https://attacker.example",
                "sec-fetch-site": "cross-site",
            },
        )

    assert response.status_code == 403
    assert response.json() == {"error": "Cross-site browser request blocked"}


def test_cross_site_origin_rejects_unsafe_request_without_fetch_metadata():
    with _client() as client:
        response = client.post(
            "/api/settings",
            content='{"publish_hook":"deploy"}',
            headers={
                "content-type": "text/plain",
                "origin": "https://attacker.example",
            },
        )

    assert response.status_code == 403
    assert response.json() == {"error": "Cross-site browser request blocked"}


def test_same_origin_unsafe_request_is_accepted():
    with _client() as client:
        response = client.post(
            "/api/settings",
            json={"thumb_quality": 82},
            headers={
                "origin": "https://omarchy.tail0eeded.ts.net:8443",
                "sec-fetch-site": "same-origin",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_unsafe_request_without_browser_origin_metadata_is_accepted():
    with _client() as client:
        response = client.post("/api/settings", json={"thumb_quality": 82})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_safe_get_request_is_unaffected_by_cross_site_metadata():
    with _client() as client:
        response = client.get(
            "/api/settings",
            headers={
                "origin": "https://attacker.example",
                "sec-fetch-site": "cross-site",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_forwarded_tailscale_origin_is_accepted():
    with _client(
        base_url="http://127.0.0.1:8000",
        client_address=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/settings",
            json={"thumb_quality": 82},
            headers={
                "origin": "https://omarchy.tail0eeded.ts.net:8443",
                "sec-fetch-site": "same-origin",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "omarchy.tail0eeded.ts.net:8443",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_remote_client_cannot_spoof_forwarded_origin():
    forwarded_headers = (
        {
            "x-forwarded-proto": "https",
            "x-forwarded-host": "attacker.example",
        },
        {"forwarded": "for=198.51.100.20;proto=https;host=attacker.example"},
    )

    with _client() as client:
        for headers in forwarded_headers:
            response = client.post(
                "/api/settings",
                json={"publish_hook": "deploy"},
                headers={"origin": "https://attacker.example", **headers},
            )

            assert response.status_code == 403
            assert response.json() == {"error": "Cross-site browser request blocked"}
