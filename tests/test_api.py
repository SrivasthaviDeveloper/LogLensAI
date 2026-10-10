"""HTTP-level tests for the FastAPI application."""

import asyncio

import httpx
import pytest

import main
from infrastructure import InfrastructureManager
from models import (
    DiagnosticResult,
    DiagnosticStatus,
    ErrorCategory,
    HealthResponse,
    ScenarioListResponse,
)


@pytest.fixture
def client():
    return InProcessASGIClient(main.app)


class InProcessASGIClient:
    """Synchronous test facade over HTTPX ASGITransport; it opens no sockets."""

    def __init__(self, app) -> None:
        self._app = app

    def get(self, path: str, **kwargs):
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self._request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self._request("PATCH", path, **kwargs)

    def _request(self, method: str, path: str, **kwargs):
        async def send_request():
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send_request())


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


def test_simulation_and_investigation_endpoints_correlate_generated_evidence(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main,
        "infrastructure_manager",
        InfrastructureManager(data_root=tmp_path / "mock_infrastructure"),
    )

    inventory = client.get("/infrastructure/servers")
    assert inventory.status_code == 200
    assert len(inventory.json()["servers"]) == 4
    assert client.get("/infrastructure/services").json()["services"]
    assert client.get("/infrastructure/servers/backend-server-01/services").json()["services"][0]["name"] == "circuit-service"
    service_details = client.get("/infrastructure/services/redis-cache")
    assert service_details.status_code == 200
    assert service_details.json()["server_id"] == "data-server-01"

    simulated = client.post("/simulation/scenarios/redis_timeout", json={"seed": 101})
    assert simulated.status_code == 200
    simulation_body = simulated.json()
    assert simulation_body["http_status_code"] == 504
    assert simulation_body["request_id"] == "SIM-REDIS-0000000101"

    investigation = client.post(
        "/investigate",
        json={
            "application_name": "VNSP",
            "error_description": "Redis operation timeout",
            "request_id": simulation_body["request_id"],
        },
    )
    assert investigation.status_code == 200
    report = investigation.json()
    assert report["status"] == "found"
    assert report["error_category"] == "request_timeout"
    assert {record["service_name"] for record in report["evidence"]} >= {
        "api-gateway",
        "circuit-service",
        "redis-cache",
    }
    assert all(record["source_file"].endswith(".jsonl") for record in report["evidence"])
    service_logs = client.get("/infrastructure/services/redis-cache/logs", params={"severity": "ERROR"})
    assert service_logs.status_code == 200 and service_logs.json()["count"] == 2
    server_logs = client.get("/infrastructure/servers/backend-server-01/logs")
    assert server_logs.status_code == 200
    assert all(record["server_id"] == "backend-server-01" for record in server_logs.json()["logs"])

    status_update = client.patch(
        "/infrastructure/services/auth-service/status", json={"status": "unavailable"}
    )
    assert status_update.status_code == 200
    assert status_update.json()["service"]["status"] == "unavailable"

    reset = client.post("/simulation/reset")
    assert reset.status_code == 200 and reset.json()["removed_log_files"] > 0
    assert client.get("/infrastructure/services/auth-service").json()["service"]["status"] == "healthy"


def test_simulation_missing_correlation_and_log_search_errors(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main,
        "infrastructure_manager",
        InfrastructureManager(data_root=tmp_path / "mock_infrastructure"),
    )
    simulated = client.post("/simulation/scenarios/missing_correlation")
    assert simulated.status_code == 200
    assert simulated.json()["request_id"] is None

    report = client.post(
        "/investigate",
        json={"application_name": "VNSP", "error_description": "timeout"},
    )
    assert report.status_code == 200
    assert report.json()["status"] == "not_found"
    assert report.json()["evidence"] == []
    assert report.json()["trace_id"] is None

    assert client.get("/logs/search").status_code == 400
    assert client.get("/infrastructure/servers/no-such-server/services").status_code == 404
    assert client.post("/simulation/scenarios/not-a-scenario").status_code == 422
