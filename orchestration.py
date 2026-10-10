"""Background job lifecycle and evidence-first investigation orchestration."""

from __future__ import annotations

import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from classifier import ClassifierError, TfidfIncidentClassifier
from diagnostics import classify_error_family, explain_error
from infrastructure import InfrastructureManager
from log_providers import create_log_provider
from models import (
    ClassifierCandidate,
    IncidentLogEvent,
    InvestigationJobResponse,
    InvestigationLifecycleStatus,
    InvestigationReport,
    InvestigationRequest,
    RankedServiceCandidate,
)


ProgressCallback = Callable[[str, str, int], None]
REQUEST_ID_PATTERN = re.compile(r"\b(?:REQ|SIM|TRACE)-[A-Z0-9-]{4,64}\b", re.IGNORECASE)
ERROR_TERMS = {"error", "failed", "failure", "timeout", "timed", "exception", "unavailable", "refused", "rejected"}


class InvestigationOrchestrator:
    """Runs classification, allowlisted discovery, retrieval, and correlation."""

    def __init__(self, manager: InfrastructureManager, classifier: TfidfIncidentClassifier | None = None) -> None:
        self.manager = manager
        try:
            self.classifier = classifier or TfidfIncidentClassifier.load_or_train()
        except ClassifierError:
            # Preserve API availability and clearly label the rule-only fallback.
            self.classifier = None

    def run(self, investigation_id: str, request: InvestigationRequest, update: ProgressCallback) -> InvestigationReport:
        update("classifying", "Identifying the application and error pattern with the local TF-IDF model...", 15)
        classified = self._classify(request)

        update("discovering", "Finding configured servers, services, and dependencies...", 30)
        services, reasons = self._discover(classified, request)

        provider = create_log_provider(self.manager)
        update("connecting_log_source", f"Preparing the configured {provider.source_label}...", 42)
        events = provider.retrieve(
            services,
            request,
            on_connected=lambda message: update("retrieving_logs", message, 55),
        )

        update("correlating", "Correlating request, trace, and response records...", 68)
        selected_events, request_id, trace_id, correlation_gaps = self._correlate(events, request, classified)

        update("analyzing", "Analyzing observed errors and evidence gaps...", 82)
        report = self._build_report(
            investigation_id,
            request,
            classified,
            services,
            reasons,
            provider.source_label,
            selected_events,
            request_id,
            trace_id,
            correlation_gaps,
        )
        update("preparing_report", "Preparing the evidence-linked investigation report...", 95)
        return report

    def _classify(self, request: InvestigationRequest) -> dict:
        if self.classifier is not None:
            return self.classifier.classify(request)
        family = classify_error_family(request.error_description)
        category = {
            "authentication": "credential_mismatch",
            "timeout": "request_timeout",
            "server_error": "unknown_error",
            "unknown": "unknown",
        }[family]
        return {
            "status": "model_unavailable_rule_fallback",
            "model_version": "unavailable; keyword rules only",
            "category": category,
            "confidence": 0.0,
            "candidates": [],
            "training_data_label": "not used",
        }

    def _discover(self, classified: dict, request: InvestigationRequest) -> tuple[list[str], dict[str, str]]:
        available = {service.name for service in self.manager.list_services()}
        ranked = classified.get("candidates", [])
        names = [item["service_name"] for item in ranked if item.get("service_name") in available]
        reasons = {item["service_name"]: f"TF-IDF similarity {item['score']:.3f} for {item['category']} history" for item in ranked}
        if not names:
            fallback = {
                "credential_mismatch": ["api-gateway", "auth-service"],
                "request_timeout": ["api-gateway", "circuit-service", "redis-cache", "payment-service"],
                "database_failure": ["api-gateway", "payment-service", "database-service"],
                "service_unavailable": ["api-gateway", "payment-service", "notification-service"],
                "unknown_error": ["api-gateway"],
                "unknown": ["api-gateway"],
            }.get(classified.get("category"), ["api-gateway"])
            names = [name for name in fallback if name in available]
            reasons.update({name: "Transparent rule-based fallback because model candidates are unavailable." for name in names})
        if "api-gateway" in available and "api-gateway" not in names:
            names.append("api-gateway")
            reasons["api-gateway"] = "Configured entry point for simulated VNSP request flows."
        if request.service_name:
            if request.service_name not in available:
                return [], {}
            names = [request.service_name] + [name for name in names if name != request.service_name]
            reasons[request.service_name] = "User-provided service hint, verified against the local service catalog."
        if request.server_name:
            server = request.server_name.casefold()
            matching_server_ids = {
                item.server_id.casefold()
                for item in self.manager.list_servers()
                if server in {item.server_id.casefold(), item.host.casefold()}
            }
            names = [
                name for name in names
                if self.manager.get_service(name)[0].casefold() in matching_server_ids
                or self.manager.get_service(name)[1].host.casefold() == server
            ]
        # Include configured downstream dependencies so a dependency failure can be verified.
        for name in list(names[:4]):
            for dependency in self.manager.get_service(name)[1].dependencies:
                if dependency not in names:
                    names.append(dependency)
                    reasons[dependency] = f"Configured dependency of candidate service {name}."
        return list(dict.fromkeys(names)), reasons

    def _correlate(
        self,
        events: list[IncidentLogEvent],
        request: InvestigationRequest,
        classified: dict,
    ) -> tuple[list[IncidentLogEvent], str | None, str | None, list[str]]:
        explicit_id = request.request_id
        if explicit_id is None:
            match = REQUEST_ID_PATTERN.search(" ".join((request.error_description, request.additional_context or "")))
            explicit_id = match.group(0) if match else None
        if explicit_id:
            matched = [event for event in events if explicit_id in (event.request_id, event.correlation_id)]
            trace_ids = {event.trace_id for event in matched if event.trace_id}
            gaps = [] if matched else [f"Request/correlation ID {explicit_id} was not found in the selected log sources."]
            return matched, explicit_id, next(iter(trace_ids)) if len(trace_ids) == 1 else None, gaps

        # Without an ID, only group logs with explicit shared IDs; choose the strongest
        # error-text match. This is a time/similarity association, never asserted as correlation.
        groups: dict[str, list[IncidentLogEvent]] = {}
        uncorrelated: list[IncidentLogEvent] = []
        for event in events:
            key = event.request_id or event.correlation_id
            if key:
                groups.setdefault(key, []).append(event)
            else:
                uncorrelated.append(event)
        query = set(word.casefold() for word in re.findall(r"[A-Za-z0-9_-]+", request.error_description))
        query.update(word.casefold() for word in re.findall(r"[A-Za-z0-9_-]+", request.error_code or ""))
        expected_category = classified.get("category")

        def score_group(records: list[IncidentLogEvent]) -> tuple[int, float]:
            content = " ".join(f"{event.error_code or ''} {event.message} {event.exception_category or ''}" for event in records).casefold()
            lexical = sum(1 for word in query if len(word) > 2 and word in content)
            category_match = any(expected_category and event.exception_category and expected_category in event.exception_category for event in records)
            failure = any(event.level.value in {"ERROR", "CRITICAL"} or (event.http_status_code and event.http_status_code >= 400) for event in records)
            return lexical + int(category_match) * 2 + int(failure), sum(event.duration_ms or 0 for event in records) / max(len(records), 1)

        ranked_groups = sorted(groups.items(), key=lambda item: score_group(item[1]), reverse=True)
        if not ranked_groups:
            return uncorrelated, None, None, ["No request ID, correlation ID, or trace ID was available to confirm cross-service correlation."]
        best_id, best_events = ranked_groups[0]
        best_score, _ = score_group(best_events)
        if best_score <= 0:
            return [], None, None, ["No log group matched the supplied error text with sufficient evidence."]
        trace_ids = {event.trace_id for event in best_events if event.trace_id}
        return sorted(best_events, key=lambda event: event.timestamp), best_id, next(iter(trace_ids)) if len(trace_ids) == 1 else None, ["Request ID was inferred from matching logs; association is based on the closest error-text match, not a user-supplied ID."]

    def _build_report(
        self,
        investigation_id: str,
        request: InvestigationRequest,
        classified: dict,
        services: list[str],
        reasons: dict[str, str],
        source_label: str,
        events: list[IncidentLogEvent],
        request_id: str | None,
        trace_id: str | None,
        correlation_gaps: list[str],
    ) -> InvestigationReport:
        evidence = sorted(events, key=lambda event: event.timestamp)
        evidence_services = {event.service_name for event in evidence}
        components = []
        candidate_scores = {item["service_name"]: item["score"] for item in classified.get("candidates", [])}
        for name in services:
            try:
                server_id, _ = self.manager.get_service(name)
            except ValueError:
                continue
            components.append(RankedServiceCandidate(
                service_name=name,
                server_id=server_id,
                score=round(candidate_scores.get(name, 0.0), 4),
                confirmed_by_evidence=name in evidence_services,
                selection_reason=reasons.get(name, "Selected from the configured service catalog."),
            ))

        failures = [
            event for event in evidence
            if event.level.value in {"ERROR", "CRITICAL"}
            or (event.http_status_code is not None and event.http_status_code >= 400)
        ]
        failure = failures[0] if failures else None
        category = classified.get("category", "unknown")
        interpretation, baseline_checks = explain_error(request.error_description)
        successful_response = any(
            event.http_status_code is not None and 200 <= event.http_status_code < 400
            for event in evidence
        )
        if evidence and not failure and successful_response:
            # Observed request/response evidence takes precedence over a merely
            # similar historical classifier example.
            category = "no_error_observed"
            interpretation = (
                "The matching log records contain a successful HTTP response and no error-level event. "
                "The submitted failure was not reproduced in the retrieved evidence."
            )
            baseline_checks = [
                "Confirm the incident timestamp and request ID; the retrieved request completed successfully.",
                "Search for a separate failed attempt or downstream request using the same trace or correlation ID.",
            ]
        gaps = list(correlation_gaps)
        if not evidence:
            gaps.append("No matching log evidence was retrieved. Provide the exact request ID and event time, or generate a local demo scenario first.")
        if not any(event.http_status_code is not None for event in evidence):
            gaps.append("No response status was found; request/response completion cannot be confirmed.")
        if evidence and not trace_id:
            gaps.append("No single consistent trace ID was present across the retrieved records.")
        if classified.get("status") == "insufficient_evidence":
            gaps.append("The fictional training set did not contain a sufficiently similar incident; classifier output is unknown.")
        confirmed = list(dict.fromkeys(event.service_name for event in evidence))
        if not evidence:
            status = "partial_success"
        elif gaps:
            status = "partial_success"
        else:
            status = "completed"
        hypothesis = None
        if failure:
            hypothesis = (
                f"Hypothesis from observed {failure.error_code or failure.level.value} event in "
                f"{failure.service_name}: {failure.message}"
            )
        recommendations = list(baseline_checks)
        if failure:
            recommendations.insert(0, "Verify the first error event against adjacent request and dependency records before treating this hypothesis as confirmed.")
        if not evidence:
            recommendations.insert(0, "Generate a known mock incident or provide a matching request/correlation ID and event time.")
        classifier_candidates = [ClassifierCandidate.model_validate(item) for item in classified.get("candidates", [])]
        error_message = " ".join(filter(None, (request.action_name, request.error_description, request.error_code)))
        return InvestigationReport(
            investigation_id=investigation_id,
            application_name=request.application_name,
            error_message=error_message,
            environment=request.environment,
            investigated_at=datetime.now(timezone.utc),
            status=status,
            source_label=source_label,
            classifier_status=classified.get("status", "insufficient_evidence"),
            classifier_model=classified.get("model_version", "unavailable"),
            classification_confidence=float(classified.get("confidence", 0.0)),
            error_category=category,
            ranked_candidates=classifier_candidates,
            likely_components=components,
            confirmed_components=confirmed,
            request_id=request_id,
            trace_id=trace_id,
            evidence=evidence,
            request_response_trace=evidence,
            failure_point=failure.service_name if failure else None,
            error_interpretation=interpretation,
            root_cause_hypothesis=hypothesis,
            recommended_next_steps=list(dict.fromkeys(recommendations)),
            evidence_gaps=list(dict.fromkeys(gaps)),
            message=("Evidence retrieved; findings remain hypotheses." if evidence else "Investigation completed with insufficient matching evidence."),
        )


class InvestigationJobStore:
    """Bounded in-process background job manager with observable stages."""

    MAX_ACTIVE = 16
    MAX_HISTORY = 500

    def __init__(self, orchestrator: InvestigationOrchestrator, *, workers: int = 4) -> None:
        self.orchestrator = orchestrator
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="investigation")
        self._jobs: dict[str, dict] = {}
        self._lock = threading.RLock()

    def submit(self, request: InvestigationRequest) -> InvestigationJobResponse:
        with self._lock:
            self._prune_history()
            active = sum(job["status"] in {InvestigationLifecycleStatus.QUEUED, InvestigationLifecycleStatus.RUNNING} for job in self._jobs.values())
            if active >= self.MAX_ACTIVE or len(self._jobs) >= self.MAX_HISTORY:
                raise RuntimeError("Investigation capacity is full. Retry after an active job completes.")
            investigation_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            self._jobs[investigation_id] = {
                "status": InvestigationLifecycleStatus.QUEUED,
                "stage": "queued",
                "message": "Investigation accepted and waiting for a worker.",
                "progress": 0,
                "started_at": now,
                "updated_at": now,
                "completed_at": None,
                "error": None,
                "report": None,
            }
            self.executor.submit(self._run, investigation_id, request)
            return self.get_status(investigation_id)

    def _prune_history(self) -> None:
        completed = sorted(
            (
                (job["updated_at"], identifier)
                for identifier, job in self._jobs.items()
                if job["status"] not in {InvestigationLifecycleStatus.QUEUED, InvestigationLifecycleStatus.RUNNING}
            ),
            key=lambda item: item[0],
        )
        while len(self._jobs) >= self.MAX_HISTORY and completed:
            _, identifier = completed.pop(0)
            self._jobs.pop(identifier, None)

    def get_status(self, investigation_id: str) -> InvestigationJobResponse:
        with self._lock:
            job = self._jobs.get(investigation_id)
            if job is None:
                raise KeyError(investigation_id)
            return InvestigationJobResponse(
                investigation_id=investigation_id,
                **{key: job[key] for key in ("status", "stage", "message", "progress", "started_at", "updated_at", "completed_at", "error")},
            )

    def get_report(self, investigation_id: str) -> InvestigationReport | None:
        with self._lock:
            job = self._jobs.get(investigation_id)
            if job is None:
                raise KeyError(investigation_id)
            return job["report"]

    def _run(self, investigation_id: str, request: InvestigationRequest) -> None:
        self._update(investigation_id, InvestigationLifecycleStatus.RUNNING, "validating_input", "Validated request is being prepared for analysis.", 5)
        try:
            report = self.orchestrator.run(investigation_id, request, lambda stage, message, progress: self._update(
                investigation_id, InvestigationLifecycleStatus.RUNNING, stage, message, progress
            ))
            final_status = InvestigationLifecycleStatus.PARTIAL_SUCCESS if report.status == "partial_success" else InvestigationLifecycleStatus.COMPLETED
            with self._lock:
                job = self._jobs[investigation_id]
                now = datetime.now(timezone.utc)
                job.update(status=final_status, stage="completed", message="Investigation completed.", progress=100, updated_at=now, completed_at=now, report=report)
        except Exception:
            self._update(investigation_id, InvestigationLifecycleStatus.FAILED, "failed", "Investigation could not be completed. Check the configured log source and retry.", 100, error="The log source or analysis step failed; sensitive details were omitted.", completed=True)

    def _update(
        self,
        investigation_id: str,
        status: InvestigationLifecycleStatus,
        stage: str,
        message: str,
        progress: int,
        *,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        with self._lock:
            now = datetime.now(timezone.utc)
            self._jobs[investigation_id].update(
                status=status, stage=stage, message=message, progress=progress,
                updated_at=now, error=error, completed_at=now if completed else None,
            )
