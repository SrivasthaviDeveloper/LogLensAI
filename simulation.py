"""Deterministic service-to-service request scenarios for local demonstration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from infrastructure import InfrastructureManager
from models import (
    LogLevel,
    SimulatedLogRecord,
    SimulatedServiceStatus,
    SimulationResult,
    SimulationScenarioName,
)


BASE_TIMESTAMP = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class EventSpec:
    offset_ms: int
    service_name: str
    level: LogLevel
    event_name: str
    message: str
    http_method: str | None = None
    endpoint: str | None = None
    http_status_code: int | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    exception_category: str | None = None
    dependency_name: str | None = None
    correlated: bool = True


@dataclass(frozen=True)
class ScenarioSpec:
    outcome: str
    http_status_code: int | None
    category: str | None
    hypothesis: str
    events: tuple[EventSpec, ...]


SCENARIOS: dict[SimulationScenarioName, ScenarioSpec] = {
    SimulationScenarioName.AUTHENTICATION_FAILURE: ScenarioSpec(
        outcome="authentication_rejected",
        http_status_code=401,
        category="credential_mismatch",
        hypothesis="The simulated token audience did not match the auth service's expected demo audience.",
        events=(
            EventSpec(0, "api-gateway", LogLevel.INFO, "request_received", "GET /circuits/CIR-DEMO-42 received.", "GET", "/circuits/CIR-DEMO-42"),
            EventSpec(18, "auth-service", LogLevel.WARNING, "authentication_rejected", "Fictional demo token audience did not match the configured audience.", "POST", "/internal/authorize", 401, 18, "AUTH_AUDIENCE_MISMATCH", "authentication"),
            EventSpec(22, "api-gateway", LogLevel.WARNING, "response_sent", "Request rejected by authentication; no downstream service was called.", "GET", "/circuits/CIR-DEMO-42", 401, 22, "AUTH_AUDIENCE_MISMATCH", "authentication", "auth-service"),
        ),
    ),
    SimulationScenarioName.REDIS_TIMEOUT: ScenarioSpec(
        outcome="dependency_timeout",
        http_status_code=504,
        category="request_timeout",
        hypothesis="Both simulated Redis attempts exceeded the configured 800 ms operation timeout.",
        events=(
            EventSpec(0, "api-gateway", LogLevel.INFO, "request_received", "GET /circuits/CIR-DEMO-42 received.", "GET", "/circuits/CIR-DEMO-42"),
            EventSpec(12, "auth-service", LogLevel.INFO, "authentication_succeeded", "Fictional demo caller authorized."),
            EventSpec(25, "circuit-service", LogLevel.INFO, "request_processing", "Circuit status lookup started; cache timeout is configured to 800 ms."),
            EventSpec(40, "redis-cache", LogLevel.WARNING, "operation_started", "GET circuit:CIR-DEMO-42 started; simulated timeout is 800 ms.", dependency_name="redis-cache"),
            EventSpec(840, "redis-cache", LogLevel.ERROR, "operation_timeout", "GET circuit:CIR-DEMO-42 timed out after 800 ms.", duration_ms=800, error_code="REDIS_OPERATION_TIMEOUT", exception_category="timeout", dependency_name="redis-cache"),
            EventSpec(850, "circuit-service", LogLevel.WARNING, "retry_scheduled", "Retry 1 of 1 scheduled after the cache timeout.", duration_ms=800, error_code="REDIS_OPERATION_TIMEOUT", exception_category="timeout", dependency_name="redis-cache"),
            EventSpec(870, "redis-cache", LogLevel.INFO, "operation_started", "Retry 1: GET circuit:CIR-DEMO-42 started; simulated timeout is 800 ms.", dependency_name="redis-cache"),
            EventSpec(1670, "redis-cache", LogLevel.ERROR, "operation_timeout", "Retry 1 timed out after 800 ms; no cache value was returned.", duration_ms=800, error_code="REDIS_OPERATION_TIMEOUT", exception_category="timeout", dependency_name="redis-cache"),
            EventSpec(1685, "circuit-service", LogLevel.ERROR, "dependency_failed", "Circuit status lookup failed after one retry.", duration_ms=1660, error_code="REDIS_OPERATION_TIMEOUT", exception_category="timeout", dependency_name="redis-cache"),
            EventSpec(1700, "api-gateway", LogLevel.ERROR, "response_sent", "Gateway returned a simulated dependency timeout.", "GET", "/circuits/CIR-DEMO-42", 504, 1700, "UPSTREAM_TIMEOUT", "timeout", "circuit-service"),
        ),
    ),
    SimulationScenarioName.DATABASE_FAILURE: ScenarioSpec(
        outcome="database_operation_failed",
        http_status_code=500,
        category="database_failure",
        hypothesis="The database service recorded a simulated connection reset during the payment lookup.",
        events=(
            EventSpec(0, "api-gateway", LogLevel.INFO, "request_received", "POST /payments/PAY-DEMO-42 received.", "POST", "/payments/PAY-DEMO-42"),
            EventSpec(20, "payment-service", LogLevel.INFO, "payment_lookup_started", "Lookup started for fictional payment PAY-DEMO-42."),
            EventSpec(55, "database-service", LogLevel.ERROR, "query_failed", "Simulated connection reset while reading the demo payment record.", duration_ms=35, error_code="DB_CONNECTION_RESET", exception_category="database", dependency_name="database-service"),
            EventSpec(61, "payment-service", LogLevel.ERROR, "dependency_failed", "Payment lookup failed because the simulated database operation failed.", error_code="DB_CONNECTION_RESET", exception_category="database", dependency_name="database-service"),
            EventSpec(70, "api-gateway", LogLevel.ERROR, "response_sent", "Gateway returned a simulated internal service error.", "POST", "/payments/PAY-DEMO-42", 500, 70, "PAYMENT_LOOKUP_FAILED", "database", "payment-service"),
        ),
    ),
    SimulationScenarioName.DOWNSTREAM_UNAVAILABLE: ScenarioSpec(
        outcome="notification_dependency_unavailable",
        http_status_code=503,
        category="service_unavailable",
        hypothesis="The payment service observed the notification dependency as unavailable on both configured attempts.",
        events=(
            EventSpec(0, "api-gateway", LogLevel.INFO, "request_received", "POST /payments/PAY-DEMO-42/confirm received.", "POST", "/payments/PAY-DEMO-42/confirm"),
            EventSpec(18, "payment-service", LogLevel.INFO, "payment_processed", "Fictional payment was accepted; confirmation notification is pending."),
            EventSpec(25, "payment-service", LogLevel.WARNING, "dependency_attempt_failed", "Notification attempt 1 of 2 failed: simulated service status is unavailable.", error_code="DEPENDENCY_UNAVAILABLE", exception_category="availability", dependency_name="notification-service"),
            EventSpec(125, "payment-service", LogLevel.WARNING, "retry_attempt_failed", "Notification retry 2 of 2 failed: simulated service remains unavailable.", duration_ms=100, error_code="DEPENDENCY_UNAVAILABLE", exception_category="availability", dependency_name="notification-service"),
            EventSpec(130, "payment-service", LogLevel.ERROR, "notification_deferred", "Confirmation notification was deferred after retries were exhausted.", error_code="DEPENDENCY_UNAVAILABLE", exception_category="availability", dependency_name="notification-service"),
            EventSpec(145, "api-gateway", LogLevel.WARNING, "response_sent", "Gateway returned a simulated partial-service response.", "POST", "/payments/PAY-DEMO-42/confirm", 503, 145, "DEPENDENCY_UNAVAILABLE", "availability", "payment-service"),
        ),
    ),
    SimulationScenarioName.SUCCESSFUL_REQUEST: ScenarioSpec(
        outcome="success",
        http_status_code=200,
        category="success",
        hypothesis="The correlated records show a successful request path through gateway, auth, circuit, and cache services.",
        events=(
            EventSpec(0, "redis-cache", LogLevel.INFO, "service_recovered", "Simulated Redis health returned to healthy before this request.", correlated=False),
            EventSpec(5, "api-gateway", LogLevel.INFO, "request_received", "GET /circuits/CIR-DEMO-42 received.", "GET", "/circuits/CIR-DEMO-42"),
            EventSpec(15, "auth-service", LogLevel.INFO, "authentication_succeeded", "Fictional demo caller authorized."),
            EventSpec(28, "circuit-service", LogLevel.INFO, "request_processing", "Circuit status lookup started."),
            EventSpec(42, "redis-cache", LogLevel.DEBUG, "cache_hit", "Cache hit for fictional circuit CIR-DEMO-42.", dependency_name="redis-cache"),
            EventSpec(55, "api-gateway", LogLevel.INFO, "response_sent", "Gateway returned a successful simulated response.", "GET", "/circuits/CIR-DEMO-42", 200, 50),
        ),
    ),
    SimulationScenarioName.MISSING_CORRELATION: ScenarioSpec(
        outcome="correlation_id_missing",
        http_status_code=400,
        category=None,
        hypothesis="No root-cause hypothesis is available because the simulated request has no correlation ID.",
        events=(
            EventSpec(0, "api-gateway", LogLevel.WARNING, "correlation_id_missing", "Inbound demo request arrived without a request ID or trace ID.", "GET", "/circuits/CIR-DEMO-42", 400, error_code="MISSING_CORRELATION_ID", exception_category="correlation"),
        ),
    ),
    SimulationScenarioName.CONNECTION_REFUSED: ScenarioSpec(
        outcome="upstream_connection_refused",
        http_status_code=502,
        category="service_unavailable",
        hypothesis="The gateway recorded that its simulated connection to payment-service was refused; no payment-service request event was recorded.",
        events=(
            EventSpec(0, "api-gateway", LogLevel.INFO, "request_received", "POST /payments/PAY-DEMO-42 received.", "POST", "/payments/PAY-DEMO-42"),
            EventSpec(24, "api-gateway", LogLevel.ERROR, "upstream_connection_refused", "Connection to the configured payment-service endpoint was refused before the request reached that service.", "POST", "/payments/PAY-DEMO-42", 502, 24, "CONNECTION_REFUSED", "availability", "payment-service"),
            EventSpec(26, "api-gateway", LogLevel.ERROR, "response_sent", "Gateway returned a simulated bad-gateway response.", "POST", "/payments/PAY-DEMO-42", 502, 26, "UPSTREAM_CONNECTION_REFUSED", "availability", "payment-service"),
        ),
    ),
    SimulationScenarioName.MISSING_RESPONSE: ScenarioSpec(
        outcome="response_not_observed",
        http_status_code=None,
        category="insufficient_evidence",
        hypothesis="No response event was recorded, so the final request outcome and root cause cannot be confirmed.",
        events=(
            EventSpec(0, "api-gateway", LogLevel.INFO, "request_received", "GET /circuits/CIR-DEMO-42 received.", "GET", "/circuits/CIR-DEMO-42"),
            EventSpec(16, "auth-service", LogLevel.INFO, "authentication_succeeded", "Fictional demo caller authorized."),
            EventSpec(29, "circuit-service", LogLevel.INFO, "request_processing", "Circuit status lookup started."),
            EventSpec(55, "redis-cache", LogLevel.DEBUG, "cache_miss", "No cache entry was available; backend lookup was requested.", dependency_name="redis-cache"),
            EventSpec(90, "circuit-service", LogLevel.WARNING, "response_not_observed", "No response record was observed before the simulation capture ended.", error_code="RESPONSE_NOT_OBSERVED", exception_category="incomplete"),
        ),
    ),
}


def scenario_descriptions() -> dict[SimulationScenarioName, str]:
    """Return human-readable labels for the deterministic scenario choices."""

    return {
        SimulationScenarioName.AUTHENTICATION_FAILURE: "Authentication failure",
        SimulationScenarioName.REDIS_TIMEOUT: "Redis timeout",
        SimulationScenarioName.DATABASE_FAILURE: "Database failure",
        SimulationScenarioName.DOWNSTREAM_UNAVAILABLE: "Downstream service unavailable",
        SimulationScenarioName.SUCCESSFUL_REQUEST: "Successful request",
        SimulationScenarioName.MISSING_CORRELATION: "Missing correlation evidence",
        SimulationScenarioName.CONNECTION_REFUSED: "Connection refused",
        SimulationScenarioName.MISSING_RESPONSE: "Missing response evidence",
    }


def generate_scenario(
    scenario_name: SimulationScenarioName,
    manager: InfrastructureManager,
    *,
    seed: int = 42,
) -> SimulationResult:
    """Generate a fixed, correlated request lifecycle and persist its JSONL logs."""

    spec = SCENARIOS[scenario_name]
    if scenario_name == SimulationScenarioName.DOWNSTREAM_UNAVAILABLE:
        manager.set_service_status("notification-service", SimulatedServiceStatus.UNAVAILABLE)
    elif scenario_name == SimulationScenarioName.REDIS_TIMEOUT:
        manager.set_service_status("redis-cache", SimulatedServiceStatus.DEGRADED)
    elif scenario_name == SimulationScenarioName.SUCCESSFUL_REQUEST:
        manager.set_service_status("redis-cache", SimulatedServiceStatus.HEALTHY)

    suffix = f"{seed:010d}"
    code = {
        SimulationScenarioName.AUTHENTICATION_FAILURE: "AUTH",
        SimulationScenarioName.REDIS_TIMEOUT: "REDIS",
        SimulationScenarioName.DATABASE_FAILURE: "DB",
        SimulationScenarioName.DOWNSTREAM_UNAVAILABLE: "DOWN",
        SimulationScenarioName.SUCCESSFUL_REQUEST: "OK",
        SimulationScenarioName.MISSING_CORRELATION: "MISSING",
        SimulationScenarioName.CONNECTION_REFUSED: "REFUSED",
        SimulationScenarioName.MISSING_RESPONSE: "NORESP",
    }[scenario_name]
    request_id = None if scenario_name == SimulationScenarioName.MISSING_CORRELATION else f"SIM-{code}-{suffix}"
    trace_id = None if request_id is None else f"TRACE-{code}-{suffix}"
    records: list[SimulatedLogRecord] = []

    for event in spec.events:
        server_id, service = manager.get_service(event.service_name)
        timestamp = BASE_TIMESTAMP + timedelta(milliseconds=seed + event.offset_ms)
        records.append(
            SimulatedLogRecord(
                timestamp=timestamp,
                level=event.level,
                server_id=server_id,
                service_name=event.service_name,
                request_id=request_id if event.correlated else None,
                trace_id=trace_id if event.correlated else None,
                event_name=event.event_name,
                message=event.message,
                http_method=event.http_method,
                endpoint=event.endpoint,
                http_status_code=event.http_status_code,
                duration_ms=event.duration_ms,
                error_code=event.error_code,
                exception_category=event.exception_category,
                dependency_name=event.dependency_name,
                scenario_name=scenario_name,
                source_file=service.log_file,
            )
        )

    manager.write_logs(records)
    return SimulationResult(
        scenario=scenario_name,
        request_id=request_id,
        trace_id=trace_id,
        outcome=spec.outcome,
        http_status_code=spec.http_status_code,
        generated_log_count=len(records),
        logs=records,
        message=(
            "Logs were generated from a deterministic local fixture."
            if request_id is not None
            else "A simulated request without correlation identifiers was recorded; no request ID was created."
        ),
    )
