"""Tests for Streamlit dashboard API integration helpers."""

import httpx
import pytest

from models import DiagnosticStatus
from ui import (
    ApiConnectionError,
    DEFAULT_API_BASE_URL,
    fetch_diagnostic,
    fetch_health,
    fetch_scenarios,
    resolve_api_base_url,
)


def test_api_base_url_uses_local_default_and_environment(monkeypatch):
    monkeypatch.delenv("VNSP_API_BASE_URL", raising=False)
    assert resolve_api_base_url() == DEFAULT_API_BASE_URL

    monkeypatch.setenv("VNSP_API_BASE_URL", "http://localhost:9000/")
    assert resolve_api_base_url() == "http://localhost:9000"


def test_fetch_health_validates_backend_response():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "ok"})
    )

    health = fetch_health("http://demo.test", transport=transport)

    assert health.status == "ok"


def test_fetch_scenarios_parses_backend_response():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "count": 1,
                "scenarios": [
                    {
                        "scenario_id": "REQ-1001",
                        "title": "Simulated mismatch",
                        "category": "credential_mismatch",
                    }
                ],
            },
        )
    )

    scenarios = fetch_scenarios("http://demo.test", transport=transport)

    assert scenarios.count == 1
    assert scenarios.scenarios[0].scenario_id == "REQ-1001"


@pytest.mark.parametrize(
    ("request_id", "category"),
    [
        ("REQ-1001", "credential_mismatch"),
        ("REQ-1002", "request_timeout"),
        ("REQ-9999", "unknown_error"),
    ],
)
def test_fetch_diagnostic_uses_backend_and_validates_result(request_id, category):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/diagnostics/{request_id}"
        return httpx.Response(
            200,
            json={
                "scenario_id": request_id,
                "status": "diagnosed",
                "category": category,
                "title": f"Simulated scenario {request_id}",
                "probable_root_cause": f"Fabricated cause for {request_id}.",
                "recommended_steps": [f"Inspect fabricated evidence for {request_id}."],
                "logs": [],
                "message": "Fabricated scenario analyzed successfully.",
            },
        )

    result = fetch_diagnostic(
        request_id, "http://demo.test", transport=httpx.MockTransport(handler)
    )

    assert result.status == DiagnosticStatus.DIAGNOSED
    assert result.scenario_id == request_id
    assert result.category.value == category
    assert result.probable_root_cause == f"Fabricated cause for {request_id}."


def test_fetch_diagnostic_preserves_structured_not_found_response():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            404,
            json={
                "scenario_id": "REQ-8888",
                "status": "not_found",
                "category": None,
                "title": None,
                "probable_root_cause": None,
                "recommended_steps": [],
                "logs": [],
                "message": "No fabricated scenario is available for REQ-8888.",
            },
        )
    )

    result = fetch_diagnostic("REQ-8888", "http://demo.test", transport=transport)

    assert result.status == DiagnosticStatus.NOT_FOUND
    assert result.scenario_id == "REQ-8888"


def test_connection_failure_is_user_friendly_and_hides_transport_details():
    def fail_connection(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("internal socket details", request=request)

    with pytest.raises(ApiConnectionError) as error:
        fetch_health(
            "http://demo.test", transport=httpx.MockTransport(fail_connection)
        )

    assert "internal socket details" not in str(error.value)
    assert "backend is running" in str(error.value)


def test_unexpected_payload_is_reported_without_pydantic_details():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"unexpected": "internal value"})
    )

    with pytest.raises(ApiConnectionError) as error:
        fetch_scenarios("http://demo.test", transport=transport)

    assert "internal value" not in str(error.value)
    assert "unexpected scenario list" in str(error.value)
