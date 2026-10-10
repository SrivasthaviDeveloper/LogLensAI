"""Fictional scenarios and log entries for local troubleshooting practice."""

from models import (
    DiagnosticLog,
    ErrorCategory,
    ScenarioData,
)


SCENARIOS: dict[str, ScenarioData] = {
    "REQ-1001": ScenarioData(
        scenario_id="REQ-1001",
        title="Simulated partner credential mismatch",
        category=ErrorCategory.CREDENTIAL_MISMATCH,
        probable_root_cause=(
            "The fictional caller profile sent an audience value that does not match "
            "the value expected by the simulated VNSP authentication service."
        ),
        recommended_steps=[
            "Compare the caller and service audience settings in the local demo configuration.",
            "Confirm both simulated components are using the same demo environment profile.",
            "Trace the request ID across the caller and authentication log entries.",
        ],
        logs=[
            DiagnosticLog(
                timestamp="2026-01-14T09:15:02Z",
                service="Demo Partner Client",
                level="INFO",
                message="REQ-1001 submitted using simulated profile PARTNER_DEMO_A.",
            ),
            DiagnosticLog(
                timestamp="2026-01-14T09:15:03Z",
                service="Demo VNSP Auth Service",
                level="WARN",
                message=(
                    "REQ-1001 rejected: demo audience 'vnsp-sandbox' did not match "
                    "expected demo audience 'vnsp-training'. No credential values recorded."
                ),
            ),
        ],
    ),
    "REQ-1002": ScenarioData(
        scenario_id="REQ-1002",
        title="Simulated request timeout",
        category=ErrorCategory.REQUEST_TIMEOUT,
        probable_root_cause=(
            "The simulated orchestration step took longer than the demo client's "
            "configured response window, so the caller recorded a timeout."
        ),
        recommended_steps=[
            "Compare the simulated client timeout with the orchestration duration in the logs.",
            "Check which mock workflow step consumed the most time.",
            "Check whether a retry would repeat the simulated operation before retrying it.",
        ],
        logs=[
            DiagnosticLog(
                timestamp="2026-01-14T09:22:10Z",
                service="Demo Partner Client",
                level="INFO",
                message="REQ-1002 started; configured demo response window is 2000 ms.",
            ),
            DiagnosticLog(
                timestamp="2026-01-14T09:22:12Z",
                service="Demo VNSP Orchestrator",
                level="WARN",
                message="REQ-1002 inventory simulation step exceeded the caller response window.",
            ),
            DiagnosticLog(
                timestamp="2026-01-14T09:22:13Z",
                service="Demo Partner Client",
                level="ERROR",
                message="REQ-1002 ended with simulated ReadTimeout after 2000 ms.",
            ),
        ],
    ),
    "REQ-9999": ScenarioData(
        scenario_id="REQ-9999",
        title="Simulated unexpected error",
        category=ErrorCategory.UNKNOWN_ERROR,
        probable_root_cause=(
            "The fabricated logs show an unexpected payload-shape validation failure, "
            "but do not establish why that payload was produced."
        ),
        recommended_steps=[
            "Compare the simulated request payload shape with the demo schema expected by the service.",
            "Use the request ID to inspect the caller and service entries together.",
            "Add a focused regression scenario after the payload discrepancy is understood.",
        ],
        logs=[
            DiagnosticLog(
                timestamp="2026-01-14T09:31:44Z",
                service="Demo Partner Client",
                level="INFO",
                message="REQ-9999 submitted a fabricated connectivity request.",
            ),
            DiagnosticLog(
                timestamp="2026-01-14T09:31:45Z",
                service="Demo VNSP Request Handler",
                level="ERROR",
                message="REQ-9999 raised SimulatedPayloadShapeError while validating a demo field.",
            ),
        ],
    ),
}


def get_scenario(scenario_id: str) -> ScenarioData | None:
    """Return a fabricated scenario by ID, or ``None`` when it is unsupported."""

    return SCENARIOS.get(scenario_id)
