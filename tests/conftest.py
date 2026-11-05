"""The documented test workflow is offline; explicit MockTransports still work."""
import httpx
import pytest


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    async def offline(self, request):
        raise httpx.ConnectError("External HTTP disabled in offline tests", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", offline)
