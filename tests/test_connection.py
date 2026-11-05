"""Credential checks use one real weather request, with no GeoAPI dependency."""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from quart import Quart

from qweather_client import QWeatherClient


@pytest.mark.asyncio
async def test_connection_uses_native_astrbot_web_response_and_request_context():
    from types import SimpleNamespace
    from fastapi import FastAPI, Request
    from astrbot.dashboard.api.plugins import _call_plugin_extension
    from main import QWeatherPlugin, PLUGIN_NAME

    routes = []
    context = SimpleNamespace(register_web_api=lambda *args: routes.append(args))
    plugin = QWeatherPlugin(context, {"api_host": "saved.qweatherapi.com", "api_key": "saved-key"})
    seen = []

    def upstream(request):
        seen.append(request)
        assert request.url.host == "override.qweatherapi.com"
        assert request.headers["X-QW-Api-Key"] == "override-key"
        return httpx.Response(200, json={"temperature": {"value": 12}})

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = FastAPI()
    app.state.core_lifecycle = SimpleNamespace(star_context=SimpleNamespace(registered_web_apis=routes))

    async def connection(request: Request):
        return await _call_plugin_extension(f"{PLUGIN_NAME}/test-connection", request, "tester")

    app.add_api_route("/test-connection", connection, methods=["POST"])
    try:
        with patch.object(QWeatherClient, "_get_client", return_value=upstream_client):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=True), base_url="http://testserver") as browser:
                response = await browser.post("/test-connection", json={"api_host": "override.qweatherapi.com", "api_key": "override-key"})
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert len(seen) == 1
        assert plugin.config["api_key"] == "saved-key"
    finally:
        await upstream_client.aclose()
        await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload,success,code", [
    (200, {"temperature": {"value": 16, "unit": "C"}, "condition": {"text": "晴"}}, True, "200"),
    (200, {"temperature": {"value": 0}}, True, "200"),
    (200, {"temperature": {"value": "-2.5"}}, True, "200"),
    (401, {"error": {"status": 401}}, False, "401"),
    (403, {"error": {"status": 403}}, False, "403"),
    (402, {}, False, "402"),
    (429, {}, False, "429"),
    (500, {}, False, "500"),
    (200, {}, False, "INVALID_RESPONSE"),
    (200, [], False, "INVALID_RESPONSE"),
    (200, {"temperature": {"value": "invalid"}}, False, "INVALID_RESPONSE"),
    (200, {"temperature": {"value": "NaN"}}, False, "INVALID_RESPONSE"),
    (200, {"code": "401"}, False, "401"),
    (403, {"temperature": {"value": 16}}, False, "403"),
])
async def test_connection_checks_one_weather_api(status, payload, success, code):
    from main import QWeatherPlugin

    seen = []

    def handle(req):
        seen.append(req)
        assert req.url.host == "example.qweatherapi.com"
        assert req.url.path == "/weather/v1/current/39.90/116.41"
        assert req.headers["X-QW-Api-Key"] == "private-test-key"
        assert dict(req.url.params) == {"lang": "zh", "localTime": "false"}
        return httpx.Response(status, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    plugin = QWeatherPlugin(object(), {
        "api_host": "example.qweatherapi.com", "api_key": "private-test-key",
        "default_city": "无需解析的地点",
    })
    plugin.weather_service.query = AsyncMock(side_effect=AssertionError("full weather query must not run"))
    app = Quart(__name__)
    try:
        with patch.object(QWeatherClient, "_get_client", return_value=client):
            # An unrelated HTTP stack must not be used for the connection check.
            with patch("aiohttp.ClientSession", side_effect=AssertionError("wrong HTTP stack"), create=True):
                async with app.test_request_context("/test-connection", method="POST", json={}):
                    response = await plugin.handle_test_connection()
                    data = json.loads(response.body)
        assert data["success"] is success
        assert data["code"] == code
        assert len(seen) == 1
        assert "private-test-key" not in json.dumps(data)
    finally:
        await client.aclose()
        await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("exception,code", [(httpx.ConnectError, "CONNECTION_FAILED"),
                                          (httpx.ReadTimeout, "TIMEOUT")])
async def test_network_failure_does_not_report_bad_key_or_leak_details(exception, code):
    client = QWeatherClient("example.qweatherapi.com", "private-key")

    def fail(request):
        raise exception("request failed with private-key", request=request)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    try:
        result = await client.test_connection()
        assert result["success"] is False
        assert result["code"] == code
        assert "private-key" not in json.dumps(result)
        assert result["latency_ms"] >= 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_connection_bypasses_cache_on_each_click_and_rejects_non_json():
    calls = []

    def upstream(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"temperature": {"value": 12}})
        return httpx.Response(200, text="<html>proxy error</html>")

    client = QWeatherClient("example.qweatherapi.com", "key")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    try:
        assert (await client.test_connection())["success"] is True
        assert (await client.test_connection())["code"] == "INVALID_RESPONSE"
        assert len(calls) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("host,key,code", [("", "key", "MISSING_API_HOST"),
                                         ("example.qweatherapi.com", "  ", "MISSING_API_KEY")])
async def test_missing_config_does_not_send_a_request(host, key, code):
    from main import QWeatherPlugin

    plugin = QWeatherPlugin(object(), {"api_host": host, "api_key": key})
    app = Quart(__name__)
    try:
        with patch.object(QWeatherClient, "_get_client", side_effect=AssertionError("no HTTP expected")), patch("aiohttp.ClientSession", side_effect=AssertionError("no HTTP expected")):
            async with app.test_request_context("/test-connection", method="POST", json={}):
                data = json.loads((await plugin.handle_test_connection()).body)
        assert data["success"] is False
        assert data["code"] == code
    finally:
        await plugin.terminate()
