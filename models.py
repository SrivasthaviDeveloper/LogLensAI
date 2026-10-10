"""Validated data models shared by the diagnostic engine and API."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from security import redact_sensitive_text


ScenarioIdentifier = Annotated[str, Field(min_length=1, max_length=32, strict=True)]


class ErrorCategory(StrEnum):
    CREDENTIAL_MISMATCH = "credential_mismatch"
    REQUEST_TIMEOUT = "request_timeout"
    UNKNOWN_ERROR = "unknown_error"


class DiagnosticStatus(StrEnum):
    DIAGNOSED = "diagnosed"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    ANALYSIS_ERROR = "analysis_error"


class DiagnosticRequest(BaseModel):
    """A user supplied scenario/request identifier."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: ScenarioIdentifier

    @field_validator("scenario_id")
    @classmethod
    def normalize_scenario_id(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("scenario_id must not be blank")
        return normalized


class DiagnosticLog(BaseModel):
    """One fabricated log entry included as diagnostic evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime
    service: str = Field(min_length=1)
    level: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ScenarioData(BaseModel):
    """Complete fabricated scenario definition used by the engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=r"^REQ-\d{4}$")
    title: str = Field(min_length=1)
    category: ErrorCategory
    probable_root_cause: str = Field(min_length=1)
    recommended_steps: list[str] = Field(min_length=1)
    logs: list[DiagnosticLog] = Field(min_length=1)


class DiagnosticResult(BaseModel):
    """Stable response returned for valid and invalid diagnostic requests."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    status: DiagnosticStatus
    category: ErrorCategory | None = None
    title: str | None = None
    probable_root_cause: str | None = None
    recommended_steps: list[str] = Field(default_factory=list)
    logs: list[DiagnosticLog] = Field(default_factory=list)
    message: str


class HealthResponse(BaseModel):
    """Liveness response for the local demo API."""

    status: Literal["ok"]


class ScenarioSummary(BaseModel):
    """Public summary for one available fabricated scenario."""

    scenario_id: str
    title: str
    category: ErrorCategory


class ScenarioListResponse(BaseModel):
    """Response containing the available simulated scenarios."""

    count: int = Field(ge=0)
    scenarios: list[ScenarioSummary]


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SimulatedServiceStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class SimulationScenarioName(StrEnum):
    AUTHENTICATION_FAILURE = "authentication_failure"
    REDIS_TIMEOUT = "redis_timeout"
    DATABASE_FAILURE = "database_failure"
    DOWNSTREAM_UNAVAILABLE = "downstream_unavailable"
    SUCCESSFUL_REQUEST = "successful_request"
    MISSING_CORRELATION = "missing_correlation"
    CONNECTION_REFUSED = "connection_refused"
    MISSING_RESPONSE = "missing_response"


class EvidenceStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"


class SimulatedService(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    log_file: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    status: SimulatedServiceStatus = SimulatedServiceStatus.HEALTHY
    environment: str = Field(min_length=1)
    health_check: str | None = None


class MockServer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str = Field(pattern=r"^[a-z0-9-]+$")
    display_name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    services: list[SimulatedService]
    health_check: str | None = None


class InfrastructureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_name: str = Field(default="VNSP", min_length=1)
    servers: list[MockServer] = Field(min_length=1)


class ServiceStatusUpdate(BaseModel):
    status: SimulatedServiceStatus


class ServerInventoryResponse(BaseModel):
    servers: list[MockServer]


class ServiceInventoryResponse(BaseModel):
    server_id: str | None = None
    services: list[SimulatedService]


class ServiceDetailsResponse(BaseModel):
    server_id: str
    service: SimulatedService


class ServiceStatusResponse(BaseModel):
    server_id: str
    service: SimulatedService


class SimulatedLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime
    level: LogLevel
    server_id: str
    service_name: str
    request_id: str | None = None
    trace_id: str | None = None
    event_name: str
    message: str
    http_method: str | None = None
    endpoint: str | None = None
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    exception_category: str | None = None
    dependency_name: str | None = None
    scenario_name: SimulationScenarioName
    source_file: str


class LogSearchResponse(BaseModel):
    count: int = Field(ge=0)
    logs: list[SimulatedLogRecord]


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int = Field(default=42, ge=0, le=2**32 - 1)


class SimulationResult(BaseModel):
    scenario: SimulationScenarioName
    request_id: str | None = None
    trace_id: str | None = None
    outcome: str
    http_status_code: int | None = None
    generated_log_count: int = Field(ge=0)
    logs: list[SimulatedLogRecord]
    message: str


class SimulationResetResponse(BaseModel):
    removed_log_files: int = Field(ge=0)
    restored_service_statuses: bool
    message: str


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_name: str = Field(min_length=1, max_length=64)
    error_description: str = Field(min_length=1, max_length=1000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    start_time: datetime | None = None
    end_time: datetime | None = None
    service_name: str | None = Field(default=None, min_length=1, max_length=64)
    server_name: str | None = Field(default=None, min_length=1, max_length=128)
    error_code: str | None = Field(default=None, min_length=1, max_length=100)
    action_name: str | None = Field(default=None, max_length=128)
    additional_context: str | None = Field(default=None, max_length=2000)
    environment: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("application_name", "error_description")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = redact_sensitive_text(value.strip())
        if not normalized:
            raise ValueError("this field must not be blank")
        return normalized

    @field_validator("request_id", "service_name", "server_name", "error_code", "action_name", "additional_context", "environment")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        normalized = redact_sensitive_text(value.strip()) if value is not None else None
        return normalized or None

    @field_validator("start_time", "end_time")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("end_time")
    @classmethod
    def validate_time_order(cls, value: datetime | None, info):
        start_time = info.data.get("start_time")
        if value is not None and start_time is not None and value < start_time:
            raise ValueError("end_time must be at or after start_time")
        return value


class InvestigationRequestDetails(BaseModel):
    application_name: str
    error_description: str
    request_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    service_name: str | None = None
    server_name: str | None = None
    error_code: str | None = None
    action_name: str | None = None
    additional_context: str | None = None
    environment: str | None = None


class RequestResponseDetail(BaseModel):
    timestamp: datetime
    server_id: str
    service_name: str
    http_method: str | None = None
    endpoint: str | None = None
    http_status_code: int | None = None
    duration_ms: int | None = None
    event_name: str
    source_file: str


class InvestigationResult(BaseModel):
    status: EvidenceStatus
    input: InvestigationRequestDetails
    candidate_services: list[str]
    confirmed_services: list[str]
    request_id: str | None = None
    trace_id: str | None = None
    error_category: str | None = None
    classification_basis: str
    evidence: list[SimulatedLogRecord]
    timeline: list[SimulatedLogRecord]
    request_response_details: list[RequestResponseDetail]
    observed_errors: list[str]
    hypothesis: str | None = None
    recommended_checks: list[str]
    missing_evidence: list[str]
    message: str


class InvestigationLifecycleStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class InvestigationJobResponse(BaseModel):
    investigation_id: str
    status: InvestigationLifecycleStatus
    stage: str
    message: str
    progress: int = Field(ge=0, le=100)
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class RankedServiceCandidate(BaseModel):
    service_name: str
    server_id: str
    score: float = Field(ge=0, le=1)
    confirmed_by_evidence: bool = False
    selection_reason: str


class IncidentLogEvent(BaseModel):
    timestamp: datetime
    application_name: str | None = None
    environment: str | None = None
    server_id: str
    service_name: str
    source_label: str
    source_file: str
    level: LogLevel = LogLevel.INFO
    request_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    event_name: str | None = None
    message: str
    http_method: str | None = None
    endpoint: str | None = None
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    exception_category: str | None = None


class ClassifierCandidate(BaseModel):
    service_name: str
    server_id: str
    category: str
    score: float = Field(ge=0, le=1)
    historical_root_cause: str
    resolution: str


class InvestigationReport(BaseModel):
    investigation_id: str
    application_name: str
    error_message: str
    environment: str | None = None
    investigated_at: datetime
    status: InvestigationLifecycleStatus
    source_label: str
    classifier_status: str
    classifier_model: str
    classification_confidence: float = Field(ge=0, le=1)
    error_category: str
    ranked_candidates: list[ClassifierCandidate]
    likely_components: list[RankedServiceCandidate]
    confirmed_components: list[str]
    request_id: str | None = None
    trace_id: str | None = None
    evidence: list[IncidentLogEvent]
    request_response_trace: list[IncidentLogEvent]
    failure_point: str | None = None
    error_interpretation: str
    root_cause_hypothesis: str | None = None
    recommended_next_steps: list[str]
    evidence_gaps: list[str]
    message: str
