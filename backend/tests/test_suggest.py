"""Address-suggest tests — Photon type-ahead, fully mocked.

Covers: label building prefers the street address over the bare name,
short queries are rejected without network, HTTP failures degrade to [],
and the /geocode/suggest endpoint wires through.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.geo import PhotonSuggester, _suggestion_label


def _photon_payload():
    return {
        "features": [
            {
                "geometry": {"coordinates": [-118.25, 34.05]},
                "properties": {
                    "name": "Los Angeles",
                    "street": "5th Street",
                    "housenumber": "350",
                    "city": "Los Angeles",
                    "state": "California",
                    "postcode": "90013",
                    "country": "United States",
                },
            },
            {
                "geometry": {"coordinates": [-118.24, 34.06]},
                "properties": {
                    "name": "Downtown Los Angeles",
                    "city": "Los Angeles",
                    "state": "California",
                    "country": "United States",
                },
            },
        ]
    }


def _client_with(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_label_prefers_street_address():
    props = _photon_payload()["features"][0]["properties"]
    assert _suggestion_label(props) == (
        "350 5th Street, Los Angeles, California 90013, United States"
    )


def test_label_falls_back_to_name():
    props = _photon_payload()["features"][1]["properties"]
    assert _suggestion_label(props) == (
        "Downtown Los Angeles, Los Angeles, California, United States"
    )


@pytest.mark.asyncio
async def test_suggest_returns_labeled_points():
    def handler(request):
        assert request.url.params["q"] == "los ang"
        return httpx.Response(200, json=_photon_payload())

    suggester = PhotonSuggester(_client_with(handler))
    out = await suggester.suggest("los ang")
    assert len(out) == 2
    assert out[0].label.startswith("350 5th Street")
    assert out[0].lat == pytest.approx(34.05)
    assert out[0].lon == pytest.approx(-118.25)


@pytest.mark.asyncio
async def test_suggest_short_query_skips_network():
    def handler(request):  # pragma: no cover — must not be called
        raise AssertionError("no request should fire")

    suggester = PhotonSuggester(_client_with(handler))
    assert await suggester.suggest("x") == []


@pytest.mark.asyncio
async def test_suggest_http_failure_returns_empty():
    def handler(request):
        return httpx.Response(500, text="boom")

    suggester = PhotonSuggester(_client_with(handler))
    assert await suggester.suggest("los angeles") == []


def test_endpoint_wires_through(monkeypatch):
    async def fake_suggest(self, query, limit=5):
        assert query == "garden grove"
        return []

    monkeypatch.setattr(PhotonSuggester, "suggest", fake_suggest)
    client = TestClient(app)
    resp = client.get("/geocode/suggest", params={"q": "garden grove"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_endpoint_rejects_short_query():
    client = TestClient(app)
    assert client.get("/geocode/suggest", params={"q": "x"}).status_code == 422
