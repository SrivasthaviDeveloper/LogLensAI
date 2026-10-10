"""FastAPI endpoints for the local VNSP troubleshooting demo."""

import os

import httpx
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from diagnostics import diagnose_scenario, list_available_scenarios
from models import (
    DiagnosticResult,
    DiagnosticStatus,
    HealthResponse,
    ScenarioListResponse,
)


app = FastAPI(
    title="LogLensAI Provisioning Troubleshooter",
    description="Local incident API that triggers an authenticated Jenkins demo log-pull job.",
    version="0.1.0",
)


class IncidentRequest(BaseModel):
    application: str = Field(min_length=1, max_length=100)
    error: str = Field(min_length=1, max_length=2000)
    event_time: str | None = None
    order_id: str | None = None
    correlation_id: str | None = None


@app.post("/investigate", tags=["incident investigation"])
def investigate_incident(request: IncidentRequest) -> dict:
    """Trigger an authenticated Jenkins demo job to retrieve reference logs."""
    base_url = os.getenv("JENKINS_DEMO_URL", "http://127.0.0.1:8020").rstrip("/")
    username = os.getenv("JENKINS_DEMO_USERNAME", "loglens-demo")
    token = os.getenv("JENKINS_DEMO_TOKEN", "demo-jenkins-token")
    try:
        response = httpx.post(
            f"{base_url}/job/LogLensAI-Reference-Log-Pull/buildWithParameters",
            params=request.model_dump(exclude_none=True),
            auth=(username, token),
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Jenkins demo job unavailable or could not retrieve logs. Start the local demo services and retry.",
        ) from None


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health_check() -> HealthResponse:
    """Report that the local API process is responding."""

    return HealthResponse(status="ok")


@app.get("/scenarios", response_model=ScenarioListResponse, tags=["scenarios"])
def list_scenarios() -> ScenarioListResponse:
    """List the fabricated scenarios supported by the diagnostic engine."""

    return list_available_scenarios()


@app.get(
    "/diagnostics/{request_id}",
    response_model=DiagnosticResult,
    responses={
        400: {"model": DiagnosticResult, "description": "Malformed scenario identifier."},
        404: {"model": DiagnosticResult, "description": "Scenario identifier is not available."},
        500: {"model": DiagnosticResult, "description": "Diagnostic could not be completed."},
    },
    tags=["diagnostics"],
)
def get_diagnostic(request_id: str, response: Response) -> DiagnosticResult:
    """Return a structured diagnosis and assign HTTP status from its result."""

    try:
        result = diagnose_scenario(request_id)
    except Exception:
        # Never return exception details or stack traces to the caller.
        result = DiagnosticResult(
            scenario_id="INVALID",
            status=DiagnosticStatus.ANALYSIS_ERROR,
            message="The diagnostic request could not be processed.",
        )

    response.status_code = {
        DiagnosticStatus.DIAGNOSED: 200,
        DiagnosticStatus.INVALID_REQUEST: 400,
        DiagnosticStatus.NOT_FOUND: 404,
        DiagnosticStatus.ANALYSIS_ERROR: 500,
    }.get(result.status, 500)
    return result
