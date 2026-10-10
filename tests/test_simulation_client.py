"""Tests for safe Streamlit-to-simulation API transport behavior."""

import httpx
import pytest

from simulation_client import SimulationApiError, request_simulation_api, simulation_api_url


def test_simulation_api_uses_same_base_url_environment_variable(monkeypatch):
    monkeypatch.setenv("VNSP_API_BASE_URL", "http://local.test:9000/")
    assert simulation_api_url() == "http://local.test:9000"


def test_simulation_api_request_encodes_payload_and_returns_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/simulation/scenarios/redis_timeout"
        assert request.read() == b'{"seed":17}'
        return httpx.Response(200, json={"request_id": "SIM-REDIS-0017"})

    response = request_simulation_api(
        "POST",
        "/simulation/scenarios/redis_timeout",
        base_url="http://demo.test",
        payload={"seed": 17},
        transport=httpx.MockTransport(handler),
    )
    assert response["request_id"] == "SIM-REDIS-0017"


@pytest.mark.parametrize("status_code", [400, 404, 422, 500])
def test_simulation_api_converts_http_errors_to_safe_message(status_code):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code, json={"detail": "internal path information"})
    )
    with pytest.raises(SimulationApiError) as error:
        request_simulation_api(
            "GET", "/infrastructure/servers", base_url="http://demo.test", transport=transport
        )
    assert f"HTTP {status_code}" in str(error.value)
    assert "internal path information" not in str(error.value)


def test_simulation_api_hides_transport_and_invalid_json_details():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private socket detail", request=request)

    with pytest.raises(SimulationApiError, match="Start the FastAPI backend") as error:
        request_simulation_api(
            "GET",
            "/infrastructure/servers",
            base_url="http://demo.test",
            transport=httpx.MockTransport(fail),
        )
    assert "private socket detail" not in str(error.value)

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json"))
    with pytest.raises(SimulationApiError, match="unreadable response"):
        request_simulation_api(
            "GET", "/infrastructure/servers", base_url="http://demo.test", transport=transport
        )
