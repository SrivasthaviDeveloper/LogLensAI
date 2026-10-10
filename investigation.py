"""Evidence-first investigation over generated local microservice logs."""

from __future__ import annotations

from infrastructure import InfrastructureManager
from diagnostics import classify_error_family, explain_error
from models import (
    EvidenceStatus,
    InvestigationRequest,
    InvestigationRequestDetails,
    InvestigationResult,
    RequestResponseDetail,
    SimulatedLogRecord,
)


def _candidate_services(error_description: str, available: set[str]) -> list[str]:
    """Suggest services using transparent text rules, not an ML classifier."""

    text = error_description.casefold()
    family = classify_error_family(text)
    if any(token in text for token in ("auth", "credential", "token", "401")) or family == "authentication":
        candidates = ["api-gateway", "auth-service"]
    elif any(token in text for token in ("redis", "cache", "circuit")):
        candidates = ["api-gateway", "circuit-service", "redis-cache"]
    elif any(token in text for token in ("database", "db", "sql", "payment")):
        candidates = ["api-gateway", "payment-service", "database-service"]
    elif any(token in text for token in ("notification", "downstream", "unavailable", "503")):
        candidates = ["api-gateway", "payment-service", "notification-service"]
    elif family == "timeout":
        candidates = ["api-gateway", "circuit-service", "redis-cache", "payment-service"]
    elif family == "server_error":
        candidates = ["api-gateway", "payment-service", "database-service", "notification-service"]
    else:
        candidates = ["api-gateway"]
    return [name for name in candidates if name in available]


def _evidence_category(records: list[SimulatedLogRecord]) -> str | None:
    for record in records:
        category = record.exception_category
        if category == "incomplete":
            return "insufficient_evidence"
        if category == "authentication":
            return "credential_mismatch"
        if category == "timeout":
            return "request_timeout"
        if category == "database":
            return "database_failure"
        if category == "availability":
            return "service_unavailable"
    if any(record.http_status_code is not None and 200 <= record.http_status_code < 300 for record in records):
        return "success"
    return None


def _text_category(error_description: str) -> str | None:
    family = classify_error_family(error_description)
    return {
        "authentication": "credential_mismatch",
        "timeout": "request_timeout",
        "server_error": "unknown_error",
        "unknown": None,
    }[family]


def _checks(category: str | None, baseline: list[str]) -> list[str]:
    checks_by_category = {
        "credential_mismatch": [
            "Compare the fictional caller and auth-service audience values in the generated records.",
            "Confirm no downstream service was called after authentication was rejected.",
        ],
        "request_timeout": [
            "Compare the simulated dependency timeout with the event durations and final HTTP status.",
            "Review the retry event and determine whether the operation completed before retrying in a real system.",
        ],
        "database_failure": [
            "Inspect the database-service error and payment-service response records sharing this request ID.",
            "Check whether the recorded failure is a dependency connection issue or an application-level error.",
        ],
        "service_unavailable": [
            "Check the simulated dependency status and count the caller's recorded retry attempts.",
            "Confirm whether the caller deferred work or returned an error after retries were exhausted.",
        ],
    }
    return checks_by_category.get(category, baseline)


def investigate_incident(
    request: InvestigationRequest,
    manager: InfrastructureManager,
) -> InvestigationResult:
    """Correlate a validated user report with local records; never invent evidence."""

    configured_application = manager.config.application_name
    services = manager.list_services()
    available_names = {service.name for service in services}
    candidates = _candidate_services(request.error_description, available_names)
    request_details = InvestigationRequestDetails(**request.model_dump())

    if request.application_name.casefold() != configured_application.casefold():
        return _not_found(
            request_details,
            candidates,
            "The requested application is not present in the local simulation catalog.",
            [f"No simulation configuration is available for application '{request.application_name}'."],
        )

    if request.service_name is not None:
        manager.get_service(request.service_name)

    if request.request_id is None:
        return _not_found(
            request_details,
            candidates,
            "No request ID was supplied, so matching log evidence cannot be correlated.",
            ["Request ID was not provided; no request or trace identifiers were created by investigation."],
            error_category=_text_category(request.error_description),
            classification_basis="error_text" if _text_category(request.error_description) else "none",
        )

    records = manager.search_logs(
        request_id=request.request_id,
        service_name=request.service_name,
        start_time=request.start_time,
        end_time=request.end_time,
        environment=request.environment,
    )
    if not records:
        missing = [f"No generated log record matched request ID '{request.request_id}' and the selected filters."]
        if request.start_time or request.end_time or request.service_name or request.environment:
            missing.append("The time, service, or environment filters may exclude records for this request.")
        return _not_found(
            request_details,
            candidates,
            "No matching simulated log evidence was found.",
            missing,
            request_id=request.request_id,
            error_category=_text_category(request.error_description),
            classification_basis="error_text" if _text_category(request.error_description) else "none",
        )

    records = sorted(records, key=lambda record: record.timestamp)
    category = _evidence_category(records) or _text_category(request.error_description)
    classification_basis = "log_evidence" if _evidence_category(records) else ("error_text" if category else "none")
    confirmed = list(dict.fromkeys(record.service_name for record in records))
    trace_ids = {record.trace_id for record in records if record.trace_id}
    trace_id = next(iter(trace_ids)) if len(trace_ids) == 1 else None
    errors = list(dict.fromkeys(
        f"{record.error_code}: {record.message}"
        for record in records
        if record.error_code is not None
    ))
    missing_evidence: list[str] = []
    if not trace_id:
        missing_evidence.append("Matching events do not contain one consistent trace ID.")
    if not any(record.http_status_code is not None for record in records):
        missing_evidence.append("No HTTP response status was recorded for this request.")

    if errors and category != "insufficient_evidence":
        observed_service = next((record.service_name for record in records if record.error_code), "unknown service")
        observed_event = next(record for record in records if record.error_code)
        hypothesis = (
            f"Hypothesis based on the recorded {observed_event.error_code} event in {observed_service}: "
            f"{observed_event.message} This is an interpretation of simulated evidence, not proof of a real root cause."
        )
    elif category == "success":
        hypothesis = None
    else:
        hypothesis = None

    explanation, baseline_checks = explain_error(request.error_description)
    if category == "success":
        recommendations = [
            "No error-specific follow-up is indicated by the correlated records.",
            "Confirm the returned HTTP status and downstream event sequence match the expected demo workflow.",
        ]
    else:
        recommendations = _checks(category, baseline_checks)
        if errors:
            recommendations.insert(0, "Treat the matching error records as evidence; verify the hypothesis against adjacent service records.")
        else:
            recommendations.insert(0, explanation)

    return InvestigationResult(
        status=EvidenceStatus.FOUND,
        input=request_details,
        candidate_services=candidates,
        confirmed_services=confirmed,
        request_id=request.request_id,
        trace_id=trace_id,
        error_category=category,
        classification_basis=classification_basis,
        evidence=records,
        timeline=records,
        request_response_details=[
            RequestResponseDetail(
                timestamp=record.timestamp,
                server_id=record.server_id,
                service_name=record.service_name,
                http_method=record.http_method,
                endpoint=record.endpoint,
                http_status_code=record.http_status_code,
                duration_ms=record.duration_ms,
                event_name=record.event_name,
                source_file=record.source_file,
            )
            for record in records
            if record.http_method is not None or record.http_status_code is not None
        ],
        observed_errors=errors,
        hypothesis=hypothesis,
        recommended_checks=recommendations,
        missing_evidence=missing_evidence,
        message="Matching generated log evidence was found; findings are based only on the listed records.",
    )


def _not_found(
    request: InvestigationRequestDetails,
    candidates: list[str],
    message: str,
    missing: list[str],
    *,
    request_id: str | None = None,
    error_category: str | None = None,
    classification_basis: str = "none",
) -> InvestigationResult:
    return InvestigationResult(
        status=EvidenceStatus.NOT_FOUND,
        input=request,
        candidate_services=candidates,
        confirmed_services=[],
        request_id=request_id,
        trace_id=None,
        error_category=error_category,
        classification_basis=classification_basis,
        evidence=[],
        timeline=[],
        request_response_details=[],
        observed_errors=[],
        hypothesis=None,
        recommended_checks=[],
        missing_evidence=missing,
        message=message,
    )
