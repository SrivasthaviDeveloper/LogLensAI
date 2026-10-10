"""FastAPI endpoints for the local VNSP troubleshooting demo."""

from fastapi import FastAPI, Response

from diagnostics import diagnose_scenario, list_available_scenarios
from models import (
    DiagnosticResult,
    DiagnosticStatus,
    HealthResponse,
    ScenarioListResponse,
)


app = FastAPI(
    title="VNSP Intelligent Troubleshooter",
    description="Local demo API backed only by fictional diagnostic scenarios.",
    version="0.1.0",
)


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
