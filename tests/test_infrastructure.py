"""Tests for the local mock infrastructure, log store, and scenario engine."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from diagnostics import classify_error_family, explain_error
from infrastructure import InfrastructureManager, InfrastructureError, UnknownServiceError
from investigation import investigate_incident
from models import (
    EvidenceStatus,
    InvestigationRequest,
    LogLevel,
    SimulatedServiceStatus,
    SimulationScenarioName,
)
from simulation import BASE_TIMESTAMP, generate_scenario


@pytest.fixture
def manager(tmp_path):
    return InfrastructureManager(data_root=tmp_path / "mock_infrastructure")


@pytest.mark.parametrize(
    ("scenario", "status", "category"),
    [
        (SimulationScenarioName.AUTHENTICATION_FAILURE, 401, "credential_mismatch"),
        (SimulationScenarioName.REDIS_TIMEOUT, 504, "request_timeout"),
        (SimulationScenarioName.DATABASE_FAILURE, 500, "database_failure"),
        (SimulationScenarioName.DOWNSTREAM_UNAVAILABLE, 503, "service_unavailable"),
        (SimulationScenarioName.SUCCESSFUL_REQUEST, 200, "success"),
        (SimulationScenarioName.CONNECTION_REFUSED, 502, "service_unavailable"),
    ],
)
def test_failure_and_success_scenarios_generate_deterministic_correlated_logs(
    manager, scenario, status, category
):
    first = generate_scenario(scenario, manager, seed=71)
    second = generate_scenario(scenario, manager, seed=71)

    assert first.request_id == second.request_id
    assert first.trace_id == second.trace_id
    assert first.http_status_code == status
    assert first.generated_log_count == len(first.logs)
    assert all(record.scenario_name == scenario for record in first.logs)
    correlated_records = [record for record in first.logs if record.request_id is not None]
    assert correlated_records
    assert {record.request_id for record in correlated_records} == {first.request_id}
    assert {record.trace_id for record in correlated_records} == {first.trace_id}

    report = investigate_incident(
        InvestigationRequest(
            application_name="VNSP",
            error_description=scenario.value.replace("_", " "),
            request_id=first.request_id,
        ),
        manager,
    )
    assert report.status == EvidenceStatus.FOUND
    assert report.error_category == category
    assert len(report.evidence) == len(correlated_records)
    assert all(record.source_file.endswith(".jsonl") for record in report.evidence)
    assert report.timeline == report.evidence


def test_infrastructure_contains_expected_servers_services_and_reserved_hosts(manager):
    servers = manager.list_servers()
    services = manager.list_services()

    assert {server.server_id for server in servers} == {
        "app-server-01",
        "app-server-02",
        "backend-server-01",
        "data-server-01",
    }
    assert {service.name for service in services} == {
        "api-gateway",
        "auth-service",
        "circuit-service",
        "payment-service",
        "notification-service",
        "redis-cache",
        "database-service",
    }
    assert all(server.host.endswith(".example.invalid") for server in servers)
    assert all(service.host.endswith(".example.invalid") for service in services)
    assert {service.name for service in manager.list_services("backend-server-01")} == {"circuit-service"}


def test_authentication_failure_stops_before_downstream_services(manager):
    result = generate_scenario(SimulationScenarioName.AUTHENTICATION_FAILURE, manager)

    assert result.http_status_code == 401
    assert {record.service_name for record in result.logs} == {"api-gateway", "auth-service"}
    assert not any("payment-service" in record.message for record in result.logs)
    report = investigate_incident(
        InvestigationRequest(
            application_name="VNSP",
            error_description="credential mismatch",
            request_id=result.request_id,
        ),
        manager,
    )
    assert report.confirmed_services == ["api-gateway", "auth-service"]
    assert report.hypothesis and "Hypothesis" in report.hypothesis


def test_redis_timeout_records_configuration_retries_and_degraded_status(manager):
    result = generate_scenario(SimulationScenarioName.REDIS_TIMEOUT, manager)
    redis_events = [record for record in result.logs if record.service_name == "redis-cache"]
    retry_events = [record for record in result.logs if record.event_name == "retry_scheduled"]
    gateway_response = next(record for record in result.logs if record.service_name == "api-gateway" and record.http_status_code)

    assert len(redis_events) == 4
    assert all(record.duration_ms == 800 for record in redis_events if record.event_name == "operation_timeout")
    assert len(retry_events) == 1
    assert gateway_response.http_status_code == 504
    assert manager.get_service("redis-cache")[1].status == SimulatedServiceStatus.DEGRADED


def test_downstream_unavailable_marks_only_dependency_and_logs_caller_retries(manager):
    result = generate_scenario(SimulationScenarioName.DOWNSTREAM_UNAVAILABLE, manager)
    service_status = manager.get_service("notification-service")[1].status

    assert service_status == SimulatedServiceStatus.UNAVAILABLE
    assert result.http_status_code == 503
    assert all(record.service_name != "notification-service" for record in result.logs)
    assert sum(record.event_name == "dependency_attempt_failed" for record in result.logs) == 1
    assert sum(record.event_name == "retry_attempt_failed" for record in result.logs) == 1


def test_successful_request_records_recovery_without_assigning_it_transaction_ids(manager):
    result = generate_scenario(SimulationScenarioName.SUCCESSFUL_REQUEST, manager)
    recovery = next(record for record in result.logs if record.event_name == "service_recovered")

    assert result.http_status_code == 200
    assert recovery.request_id is None
    assert recovery.trace_id is None
    assert manager.get_service("redis-cache")[1].status == SimulatedServiceStatus.HEALTHY


def test_missing_request_id_and_unknown_id_return_not_found_without_invented_evidence(manager):
    missing = generate_scenario(SimulationScenarioName.MISSING_CORRELATION, manager)
    assert missing.request_id is None and missing.trace_id is None
    assert missing.logs[0].request_id is None and missing.logs[0].trace_id is None

    report = investigate_incident(
        InvestigationRequest(application_name="VNSP", error_description="request timeout"),
        manager,
    )
    assert report.status == EvidenceStatus.NOT_FOUND
    assert report.request_id is None
    assert report.evidence == []
    assert report.trace_id is None
    assert "Request ID was not provided" in report.missing_evidence[0]

    unknown = investigate_incident(
        InvestigationRequest(
            application_name="VNSP",
            error_description="request timeout",
            request_id="SIM-NOT-FOUND-1234",
        ),
        manager,
    )
    assert unknown.status == EvidenceStatus.NOT_FOUND
    assert unknown.request_id == "SIM-NOT-FOUND-1234"
    assert unknown.evidence == []
    assert unknown.trace_id is None


def test_missing_response_scenario_records_a_gap_without_claiming_root_cause(manager):
    generated = generate_scenario(SimulationScenarioName.MISSING_RESPONSE, manager, seed=98)
    assert generated.http_status_code is None
    assert generated.request_id
    assert all(record.http_status_code is None for record in generated.logs)
    report = investigate_incident(
        InvestigationRequest(
            application_name="VNSP",
            error_description="response not observed",
            request_id=generated.request_id,
        ),
        manager,
    )
    assert report.status == EvidenceStatus.FOUND
    assert report.hypothesis is None
    assert any("No HTTP response status" in item for item in report.missing_evidence)


def test_log_search_filters_by_request_trace_server_service_time_severity_and_category(manager):
    simulation = generate_scenario(SimulationScenarioName.REDIS_TIMEOUT, manager)
    all_records = manager.search_logs(request_id=simulation.request_id)
    redis_records = manager.search_logs(request_id=simulation.request_id, service_name="redis-cache")
    server_records = manager.search_logs(request_id=simulation.request_id, server_id="backend-server-01")
    error_records = manager.search_logs(
        request_id=simulation.request_id,
        severity=LogLevel.ERROR,
        error_category="timeout",
    )
    trace_records = manager.search_logs(trace_id=simulation.trace_id)
    environment_records = manager.search_logs(
        request_id=simulation.request_id,
        environment="simulation",
    )
    first_record = all_records[0]
    time_records = manager.search_logs(
        request_id=simulation.request_id,
        start_time=first_record.timestamp,
        end_time=first_record.timestamp,
    )

    assert redis_records and all(record.service_name == "redis-cache" for record in redis_records)
    assert server_records and all(record.server_id == "backend-server-01" for record in server_records)
    assert error_records and all(record.level == LogLevel.ERROR for record in error_records)
    assert trace_records == all_records
    assert environment_records == all_records
    assert time_records == [first_record]


def test_different_seeds_do_not_reuse_request_or_trace_ids(manager):
    first = generate_scenario(SimulationScenarioName.SUCCESSFUL_REQUEST, manager, seed=42)
    second = generate_scenario(SimulationScenarioName.SUCCESSFUL_REQUEST, manager, seed=10042)

    assert first.request_id != second.request_id
    assert first.trace_id != second.trace_id
    assert len(manager.search_logs(request_id=first.request_id)) == first.generated_log_count - 1


def test_service_status_can_be_restored_and_reset_removes_only_generated_files(manager):
    generate_scenario(SimulationScenarioName.DOWNSTREAM_UNAVAILABLE, manager)
    generated_file = manager.data_root / "app-server-01" / "api-gateway.jsonl"
    assert generated_file.exists()

    manager.set_service_status("notification-service", SimulatedServiceStatus.HEALTHY)
    assert manager.get_service("notification-service")[1].status == SimulatedServiceStatus.HEALTHY
    removed = manager.reset_logs()
    manager.reset_service_statuses()

    assert removed > 0
    assert not generated_file.exists()
    assert manager.get_service("notification-service")[1].status == SimulatedServiceStatus.HEALTHY


def test_search_rejects_missing_filters_unknown_services_and_naive_times(manager):
    with pytest.raises(InfrastructureError):
        manager.search_logs()
    with pytest.raises(UnknownServiceError):
        manager.search_logs(request_id="SIM-UNKNOWN-0001", service_name="not-a-service")
    with pytest.raises(InfrastructureError, match="timezone"):
        manager.search_logs(request_id="SIM-UNKNOWN-0001", start_time=BASE_TIMESTAMP.replace(tzinfo=None))


def test_catalog_rejects_paths_that_escape_the_dedicated_log_directory(tmp_path):
    config_path = tmp_path / "unsafe-config.json"
    raw_config = json.loads(
        (Path(__file__).parent.parent / "config" / "mock_infrastructure.json").read_text(encoding="utf-8")
    )
    raw_config["servers"][0]["services"][0]["log_file"] = "../outside.jsonl"
    config_path.write_text(json.dumps(raw_config), encoding="utf-8")

    with pytest.raises(InfrastructureError, match="safe project-relative"):
        InfrastructureManager(config_path=config_path, data_root=tmp_path / "data")


def test_investigation_request_rejects_blank_text_and_timezone_free_ranges():
    with pytest.raises(ValidationError):
        InvestigationRequest(application_name="  ", error_description="timeout")
    with pytest.raises(ValidationError):
        InvestigationRequest(
            application_name="VNSP",
            error_description="timeout",
            start_time=datetime(2026, 1, 1),
        )
    with pytest.raises(ValidationError):
        InvestigationRequest(
            application_name="VNSP",
            error_description="timeout",
            start_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_existing_text_error_rules_are_preserved():
    assert classify_error_family("401 unauthorized") == "authentication"
    assert classify_error_family("Redis deadline exceeded") == "timeout"
    assert classify_error_family("HTTP 503") == "server_error"
    assert classify_error_family("uncategorized") == "unknown"
    explanation, checks = explain_error("Redis timeout")
    assert "time limit" in explanation
    assert checks
