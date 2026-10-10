"""Pure, local diagnostic logic for the fabricated VNSP scenarios."""

from pydantic import ValidationError

from mock_data import SCENARIOS, get_scenario
from models import (
    DiagnosticRequest,
    DiagnosticResult,
    DiagnosticStatus,
    ScenarioListResponse,
    ScenarioSummary,
)


def diagnose_scenario(identifier: object) -> DiagnosticResult:
    """Analyze one scenario ID and return a structured result.

    ``identifier`` may be a scenario ID string, a ``DiagnosticRequest``, or a
    mapping accepted by that Pydantic model. Invalid input, unsupported IDs,
    and internal lookup failures are represented by explicit result statuses.
    """

    try:
        if isinstance(identifier, str):
            request = DiagnosticRequest(scenario_id=identifier)
        else:
            request = DiagnosticRequest.model_validate(identifier)
    except (ValidationError, TypeError, ValueError):
        return DiagnosticResult(
            scenario_id="INVALID",
            status=DiagnosticStatus.INVALID_REQUEST,
            message="Provide a non-empty scenario_id string, such as REQ-1001.",
        )

    try:
        scenario = get_scenario(request.scenario_id)
    except Exception:
        # Keep the response useful while avoiding internal details in the API/UI.
        return DiagnosticResult(
            scenario_id=request.scenario_id,
            status=DiagnosticStatus.ANALYSIS_ERROR,
            message="The local diagnostic data could not be analyzed.",
        )

    if scenario is None:
        return DiagnosticResult(
            scenario_id=request.scenario_id,
            status=DiagnosticStatus.NOT_FOUND,
            message=f"No fabricated scenario is available for {request.scenario_id}.",
        )

    return DiagnosticResult(
        scenario_id=scenario.scenario_id,
        status=DiagnosticStatus.DIAGNOSED,
        category=scenario.category,
        title=scenario.title,
        probable_root_cause=scenario.probable_root_cause,
        recommended_steps=scenario.recommended_steps,
        logs=scenario.logs,
        message="Fabricated scenario analyzed successfully.",
    )


def list_available_scenarios() -> ScenarioListResponse:
    """Return summaries for the fabricated scenarios available to diagnose."""

    summaries = [
        ScenarioSummary(
            scenario_id=scenario.scenario_id,
            title=scenario.title,
            category=scenario.category,
        )
        for scenario in SCENARIOS.values()
    ]
    return ScenarioListResponse(count=len(summaries), scenarios=summaries)


def classify_error_family(error: str) -> str:
    """Apply the simple keyword rules already used by the AWS prototype."""

    text = error.casefold()
    if any(term in text for term in ("credential", "unauthorized", "401", "invalid token", "authentication")):
        return "authentication"
    if any(term in text for term in ("timeout", "timed out", "deadline exceeded", "504", "gateway timeout")):
        return "timeout"
    if any(term in text for term in ("5xx", "500", "502", "503", "internal server error")):
        return "server_error"
    return "unknown"


def explain_error(error: str) -> tuple[str, list[str]]:
    """Return the prototype's established rule-based explanation and checks."""

    family = classify_error_family(error)
    if family == "authentication":
        return (
            "The request appears to be failing authentication or authorization. A credential mismatch can occur when the caller and VNSP use different client IDs, secrets, token audiences, scopes, or environment-specific credentials.",
            [
                "Compare the caller's credential identity and environment with VNSP's configured client; do not paste secrets into this tool.",
                "Check token expiry, audience, scopes, clock skew, and any recent secret rotation.",
                "Use the request/correlation ID to find the corresponding authentication decision in both applications.",
            ],
        )
    if family == "timeout":
        return (
            "The request exceeded a time limit somewhere in the call path. The slow segment may be the caller, a VNSP service, a downstream dependency, or an orchestration step.",
            [
                "Compare timestamps and durations across caller and VNSP logs using the same correlation ID.",
                "Check dependency latency, retries, connection pool saturation, and configured client/load-balancer timeouts.",
                "Determine whether the operation completed despite the caller timing out before retrying it.",
            ],
        )
    if family == "server_error":
        return (
            "The error suggests a server-side failure or an unavailable upstream. The first failing service in the request path is the key to finding the cause.",
            [
                "Trace the correlation ID through each service and identify the first error, not only the final response.",
                "Check recent deployment/configuration changes and dependency health around the event time.",
                "Look for exception class, stack trace, retry exhaustion, or circuit-breaker events.",
            ],
        )
    return (
        "No specific error family was recognized. Use the request ID, exact message, and event time to trace the failure through the mapped services.",
        [
            "Locate the first failing service and inspect its surrounding log events.",
            "Compare the request and response at the caller and service boundary.",
            "Check for recent deployment, configuration, credential, or dependency changes.",
        ],
    )
