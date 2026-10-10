"""Validated data models shared by the diagnostic engine and API."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
