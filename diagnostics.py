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
