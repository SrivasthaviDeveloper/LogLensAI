"""HTTP-level tests for the FastAPI application."""

from fastapi.testclient import TestClient
import pytest

import main
from models import (
    DiagnosticResult,
    DiagnosticStatus,
    ErrorCategory,
    HealthResponse,
    ScenarioListResponse,
)


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def test_health_endpoint_returns_validated_response(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert HealthResponse.model_validate(response.json()).status == "ok"


def test_scenarios_endpoint_lists_all_demo_scenarios(client):
    response = client.get("/scenarios")

    assert response.status_code == 200
    body = ScenarioListResponse.model_validate(response.json())
    assert body.count == 3
    assert {scenario.scenario_id for scenario in body.scenarios} == {
        "REQ-1001",
        "REQ-1002",
        "REQ-9999",
    }


@pytest.mark.parametrize(
    ("request_id", "category"),
    [
        ("REQ-1001", ErrorCategory.CREDENTIAL_MISMATCH),
        ("REQ-1002", ErrorCategory.REQUEST_TIMEOUT),
        ("REQ-9999", ErrorCategory.UNKNOWN_ERROR),
    ],
)
def test_diagnostic_endpoint_returns_validated_success(client, request_id, category):
    response = client.get(f"/diagnostics/{request_id}")

    assert response.status_code == 200
    result = DiagnosticResult.model_validate(response.json())
    assert result.status == DiagnosticStatus.DIAGNOSED
    assert result.scenario_id == request_id
    assert result.category == category
    assert result.probable_root_cause
    assert result.recommended_steps
    assert result.logs


def test_unknown_scenario_returns_consistent_not_found_response(client):
    response = client.get("/diagnostics/REQ-8888")

    assert response.status_code == 404
    result = DiagnosticResult.model_validate(response.json())
    assert result.status == DiagnosticStatus.NOT_FOUND
    assert result.scenario_id == "REQ-8888"
    assert result.category is None
    assert result.logs == []


def test_malformed_identifier_returns_bad_request(client):
    response = client.get(f"/diagnostics/{'R' * 33}")

    assert response.status_code == 400
    result = DiagnosticResult.model_validate(response.json())
    assert result.status == DiagnosticStatus.INVALID_REQUEST
    assert result.scenario_id == "INVALID"


def test_unexpected_exception_is_sanitized_and_returns_server_error(client, monkeypatch):
    def raise_internal_error(_request_id: str):
        raise RuntimeError("private internal details")

    monkeypatch.setattr(main, "diagnose_scenario", raise_internal_error)
    response = client.get("/diagnostics/REQ-1001")

    assert response.status_code == 500
    result = DiagnosticResult.model_validate(response.json())
    assert result.status == DiagnosticStatus.ANALYSIS_ERROR
    assert "private internal details" not in response.text
    assert "traceback" not in response.text.lower()
