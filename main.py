"""FastAPI endpoints for the local VNSP troubleshooting demo."""

from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from diagnostics import diagnose_scenario, list_available_scenarios
from infrastructure import (
    InfrastructureError,
    InfrastructureManager,
    UnknownServerError,
    UnknownServiceError,
)
from investigation import investigate_incident
from classifier import ClassifierError, TfidfIncidentClassifier
from orchestration import InvestigationJobStore, InvestigationOrchestrator
from models import (
    DiagnosticResult,
    DiagnosticStatus,
    HealthResponse,
    InvestigationRequest,
    InvestigationResult,
    InvestigationJobResponse,
    InvestigationLifecycleStatus,
    InvestigationReport,
    LogLevel,
    LogSearchResponse,
    ServerInventoryResponse,
    ServiceDetailsResponse,
    ServiceInventoryResponse,
    ServiceStatusResponse,
    ServiceStatusUpdate,
    ScenarioListResponse,
    SimulationRequest,
    SimulationResetResponse,
    SimulationResult,
    SimulationScenarioName,
)
from simulation import generate_scenario


app = FastAPI(
    title="VNSP Intelligent Troubleshooter",
    description="Local demo API backed only by fictional diagnostic scenarios.",
    version="0.1.0",
)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field locations without echoing potentially sensitive invalid input."""

    safe_errors = [
        {"loc": error.get("loc", ()), "msg": "Invalid or missing value.", "type": error.get("type", "value_error")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": safe_errors})


infrastructure_manager = InfrastructureManager()
try:
    incident_classifier = TfidfIncidentClassifier.load_or_train()
except ClassifierError:
    incident_classifier = None
investigation_jobs = InvestigationJobStore(InvestigationOrchestrator(infrastructure_manager, incident_classifier))


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


@app.get("/infrastructure/servers", response_model=ServerInventoryResponse, tags=["infrastructure"])
def list_mock_servers() -> ServerInventoryResponse:
    """List logical simulated servers and the services hosted on them."""

    return ServerInventoryResponse(servers=infrastructure_manager.list_servers())


@app.get("/infrastructure/servers/{server_id}/services", response_model=ServiceInventoryResponse, tags=["infrastructure"])
def list_server_services(server_id: str) -> ServiceInventoryResponse:
    try:
        services = infrastructure_manager.list_services(server_id)
    except UnknownServerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ServiceInventoryResponse(server_id=server_id, services=services)


@app.get("/infrastructure/services", response_model=ServiceInventoryResponse, tags=["infrastructure"])
def list_mock_services() -> ServiceInventoryResponse:
    return ServiceInventoryResponse(services=infrastructure_manager.list_services())


@app.get("/infrastructure/services/{service_name}", response_model=ServiceDetailsResponse, tags=["infrastructure"])
def get_mock_service(service_name: str) -> ServiceDetailsResponse:
    try:
        server_id, service = infrastructure_manager.get_service(service_name)
    except UnknownServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ServiceDetailsResponse(server_id=server_id, service=service)


@app.patch("/infrastructure/services/{service_name}/status", response_model=ServiceStatusResponse, tags=["infrastructure"])
def update_mock_service_status(service_name: str, update: ServiceStatusUpdate) -> ServiceStatusResponse:
    try:
        server_id = infrastructure_manager.get_service(service_name)[0]
        service = infrastructure_manager.set_service_status(service_name, update.status)
    except UnknownServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ServiceStatusResponse(server_id=server_id, service=service)


def _filtered_logs(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    server_id: str | None = None,
    service_name: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    severity: LogLevel | None = None,
    error_category: str | None = None,
    environment: str | None = None,
) -> LogSearchResponse:
    try:
        logs = infrastructure_manager.search_logs(
            request_id=request_id,
            trace_id=trace_id,
            server_id=server_id,
            service_name=service_name,
            start_time=start_time,
            end_time=end_time,
            severity=severity,
            error_category=error_category,
            environment=environment,
        )
    except (UnknownServerError, UnknownServiceError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InfrastructureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LogSearchResponse(count=len(logs), logs=logs)


@app.get("/infrastructure/services/{service_name}/logs", response_model=LogSearchResponse, tags=["logs"])
def list_service_logs(
    service_name: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    severity: LogLevel | None = None,
    error_category: str | None = None,
) -> LogSearchResponse:
    return _filtered_logs(
        service_name=service_name,
        start_time=start_time,
        end_time=end_time,
        severity=severity,
        error_category=error_category,
    )


@app.get("/infrastructure/servers/{server_id}/logs", response_model=LogSearchResponse, tags=["logs"])
def list_server_logs(
    server_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    severity: LogLevel | None = None,
    error_category: str | None = None,
) -> LogSearchResponse:
    return _filtered_logs(
        server_id=server_id,
        start_time=start_time,
        end_time=end_time,
        severity=severity,
        error_category=error_category,
    )


@app.get("/logs/search", response_model=LogSearchResponse, tags=["logs"])
def search_mock_logs(
    request_id: str | None = None,
    trace_id: str | None = None,
    server_id: str | None = None,
    service_name: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    severity: LogLevel | None = None,
    error_category: str | None = None,
    environment: str | None = None,
) -> LogSearchResponse:
    return _filtered_logs(
        request_id=request_id,
        trace_id=trace_id,
        server_id=server_id,
        service_name=service_name,
        start_time=start_time,
        end_time=end_time,
        severity=severity,
        error_category=error_category,
        environment=environment,
    )


@app.post("/simulation/scenarios/{scenario_name}", response_model=SimulationResult, tags=["simulation"])
def run_simulation(
    scenario_name: SimulationScenarioName,
    simulation_request: SimulationRequest | None = None,
) -> SimulationResult:
    request = simulation_request or SimulationRequest()
    return generate_scenario(scenario_name, infrastructure_manager, seed=request.seed)


@app.post("/simulation/reset", response_model=SimulationResetResponse, tags=["simulation"])
def reset_simulation() -> SimulationResetResponse:
    removed = infrastructure_manager.reset_logs()
    infrastructure_manager.reset_service_statuses()
    return SimulationResetResponse(
        removed_log_files=removed,
        restored_service_statuses=True,
        message="Generated simulation logs were cleared and configured service statuses were restored.",
    )


@app.post("/investigate", response_model=InvestigationResult, tags=["investigation"])
def investigate(request: InvestigationRequest) -> InvestigationResult:
    try:
        result = investigate_incident(request, infrastructure_manager)
    except UnknownServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InfrastructureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.post(
    "/investigations",
    response_model=InvestigationJobResponse,
    status_code=202,
    tags=["investigation jobs"],
)
def submit_investigation(request: InvestigationRequest) -> InvestigationJobResponse:
    """Queue a structured investigation and return its ID without waiting for logs."""

    try:
        return investigation_jobs.submit(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationJobResponse,
    tags=["investigation jobs"],
)
def get_investigation_status(investigation_id: str) -> InvestigationJobResponse:
    try:
        return investigation_jobs.get_status(investigation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Investigation ID was not found.") from exc


@app.get(
    "/investigations/{investigation_id}/report",
    response_model=InvestigationReport,
    tags=["investigation jobs"],
)
def get_investigation_report(investigation_id: str) -> InvestigationReport:
    try:
        status = investigation_jobs.get_status(investigation_id)
        report = investigation_jobs.get_report(investigation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Investigation ID was not found.") from exc
    if status.status in {InvestigationLifecycleStatus.QUEUED, InvestigationLifecycleStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="Investigation report is not ready yet.")
    if report is None:
        raise HTTPException(status_code=500, detail="Investigation did not produce a report.")
    return report
