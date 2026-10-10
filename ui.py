"""Streamlit dashboard for the local VNSP troubleshooting API.

The API URL is read from ``VNSP_API_BASE_URL`` and defaults to
``http://127.0.0.1:8000`` for local development.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from incident_analysis import explain_error
from models import (
    DiagnosticResult,
    DiagnosticStatus,
    ErrorCategory,
    HealthResponse,
    ScenarioListResponse,
)


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
SERVICE_CATALOG_PATH = Path(__file__).parent / "config" / "services.json"
API_TIMEOUT_SECONDS = 5.0


class ApiConnectionError(RuntimeError):
    """Safe, user-facing error for API connectivity or response problems."""


def resolve_api_base_url(value: str | None = None) -> str:
    """Resolve the API base URL from an explicit value or environment setting."""

    configured = value if value is not None else os.getenv("VNSP_API_BASE_URL")
    return (configured or DEFAULT_API_BASE_URL).strip().rstrip("/")


def _get_json(
    base_url: str,
    path: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, Any]:
    """Perform one bounded GET and hide transport/response internals."""

    try:
        with httpx.Client(timeout=API_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.get(f"{resolve_api_base_url(base_url)}{path}")
    except (httpx.HTTPError, ValueError):
        raise ApiConnectionError(
            "Could not connect to the troubleshooting API. Check that the backend is running and retry."
        ) from None

    try:
        payload = response.json()
    except ValueError:
        raise ApiConnectionError(
            "The troubleshooting API returned an unreadable response. Retry or check the backend."
        ) from None
    return response.status_code, payload


def fetch_health(
    base_url: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> HealthResponse:
    """Check the API health endpoint and validate its response."""

    status_code, payload = _get_json(
        resolve_api_base_url(base_url), "/health", transport=transport
    )
    if status_code != 200:
        raise ApiConnectionError(
            f"The troubleshooting API health check returned HTTP {status_code}."
        )
    try:
        return HealthResponse.model_validate(payload)
    except ValidationError:
        raise ApiConnectionError(
            "The troubleshooting API returned an unexpected health response."
        ) from None


def fetch_scenarios(
    base_url: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ScenarioListResponse:
    """Retrieve and validate available scenario summaries from the API."""

    status_code, payload = _get_json(
        resolve_api_base_url(base_url), "/scenarios", transport=transport
    )
    if status_code != 200:
        raise ApiConnectionError(
            f"The troubleshooting API scenario list returned HTTP {status_code}."
        )
    try:
        return ScenarioListResponse.model_validate(payload)
    except ValidationError:
        raise ApiConnectionError(
            "The troubleshooting API returned an unexpected scenario list."
        ) from None


def fetch_diagnostic(
    request_id: str,
    base_url: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> DiagnosticResult:
    """Fetch and validate one diagnosis from the existing FastAPI backend."""

    safe_request_id = httpx.URL("/").copy_with(path=f"/diagnostics/{request_id}").path
    status_code, payload = _get_json(
        resolve_api_base_url(base_url), safe_request_id, transport=transport
    )
    if status_code not in (200, 400, 404, 500):
        raise ApiConnectionError(
            f"The troubleshooting API returned HTTP {status_code} for this diagnostic."
        )
    try:
        return DiagnosticResult.model_validate(payload)
    except ValidationError:
        raise ApiConnectionError(
            "The troubleshooting API returned an unexpected diagnostic response."
        ) from None


def _category_label(category: ErrorCategory | None) -> str:
    labels = {
        ErrorCategory.CREDENTIAL_MISMATCH: "Credential mismatch",
        ErrorCategory.REQUEST_TIMEOUT: "Request timeout",
        ErrorCategory.UNKNOWN_ERROR: "Unknown error",
    }
    return labels.get(category, "Unavailable")


def _application_names() -> list[str]:
    """Read application labels for incident intake without requiring AWS access."""
    try:
        catalog = json.loads(SERVICE_CATALOG_PATH.read_text(encoding="utf-8"))
        applications = catalog.get("applications", {})
        if isinstance(applications, dict) and applications:
            return list(applications)
    except (OSError, json.JSONDecodeError):
        pass
    return ["Other"]


def _render_dashboard() -> None:
    st.set_page_config(
        page_title="VNSP Intelligent Troubleshooter",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        :root { color-scheme: light; }
        .stApp { background: #f5f4f8; color: #292638; }
        .block-container { max-width: 1440px; padding-top: 1.5rem; padding-bottom: 3rem; }
        h1, h2, h3 { color: #403451; letter-spacing: -0.025em; }
        h2 { margin-top: 1.15rem; }
        .hero { background: linear-gradient(118deg, #11263d 0%, #17435a 65%, #087e82 100%);
                border: 1px solid #173c54; border-radius: 18px; padding: 1.7rem 1.9rem;
                margin-bottom: 1.5rem; box-shadow: 0 12px 30px rgba(18, 41, 61, .14); }
        .hero-kicker { color: #ffd166; font-size: .72rem; font-weight: 800;
                       letter-spacing: .15em; margin: 0 0 .45rem 0; }
        .hero h1.hero-title { display: block; color: #ffffff !important;
                      -webkit-text-fill-color: #ffffff; font-size: clamp(2.15rem, 4vw, 3rem);
                      font-weight: 800; line-height: 1.12; letter-spacing: -0.035em;
                      font-family: "Aptos Display", "Segoe UI Variable Display", "Trebuchet MS", sans-serif;
                      margin: .15rem 0 .35rem; text-shadow: 0 2px 12px rgba(0, 0, 0, .28); }
        .hero-copy { color: #d2e2eb; font-size: 1rem; margin: .6rem 0 0 0; }
        [data-testid="stSidebar"] { background: #efedf4; border-right: 1px solid #ddd7e7; }
        [data-testid="stMetric"] { background: #ffffff; border: 1px solid #d9e2ea;
            border-top: 3px solid #18a999; border-radius: 12px; padding: .9rem 1rem;
            box-shadow: 0 4px 14px rgba(23, 48, 70, .04); }
        [data-testid="stMetricLabel"] { color: #6b6278; }
        [data-testid="stMetricValue"] { color: #403451; }
        [data-testid="stForm"] { background: #ffffff; border: 1px solid #d9e2ea;
            border-radius: 16px; padding: 1.2rem 1.3rem 1rem; box-shadow: 0 8px 24px rgba(23, 48, 70, .06); }
        [data-testid="stWidgetLabel"] p { color: #4c405d; font-weight: 600; }
        [data-baseweb="input"] > div, [data-baseweb="select"] > div,
        [data-baseweb="textarea"] { background: #fbfdff; border-color: #cbd8e3; border-radius: 9px; }
        [data-baseweb="input"]:focus-within > div, [data-baseweb="select"]:focus-within > div,
        [data-baseweb="textarea"]:focus-within { border-color: #109b91; box-shadow: 0 0 0 1px #109b91; }
        [data-testid="stExpander"] { background: #f6f9fb; border: 1px solid #d9e2ea; border-radius: 10px; }
        .section-note { color: #6b6278; font-size: .9rem; margin-top: -.5rem; }
        div.stButton > button[kind="primary"] { background: #087e82; border: 1px solid #087e82;
            border-radius: 9px; color: #ffffff; font-weight: 700; min-height: 2.7rem; }
        div.stButton > button[kind="primary"]:hover { background: #06696e; border-color: #06696e; }
        div.stButton > button:focus { box-shadow: 0 0 0 3px rgba(24, 169, 153, .25); }
        [data-testid="stAlert"] { border-radius: 10px; }
        @media (max-width: 700px) {
            .block-container { padding-top: 1rem; }
            .hero { padding: 1.3rem; }
            .hero-title { font-size: 2rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <header class="hero">
          <p class="hero-kicker">LOCAL OPERATIONS DEMO</p>
          <h1 class="hero-title">VNSP Intelligent Troubleshooter</h1>
          <p class="hero-copy">Review simulated service incidents, likely causes, and the log evidence behind each diagnosis.</p>
        </header>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Incident intake")
    st.caption("Enter the action and full error log message. This local heuristic summarizes common patterns; it does not send the message to the demo API.")
    with st.form("incident_intake", clear_on_submit=False):
        app_col, error_col = st.columns(2)
        with app_col:
            application_name = st.selectbox("Application Name", _application_names())
        with error_col:
            action_name = st.text_input("Action Name", placeholder="e.g. CreateSubscription")
        log_message = st.text_area(
            "Full Error Log Message",
            placeholder="Paste the complete error log message here, including relevant context. Remove secrets and personal data first.",
            height=220,
        )
        with st.expander("Advanced options"):
            service_name = st.text_input("Service / Component", placeholder="e.g. billing-api")
            environment = st.selectbox(
                "Environment", ["Unspecified", "Development", "Test", "Staging", "Production"]
            )
            correlation_id = st.text_input(
                "Request / Correlation ID (optional)",
                placeholder="e.g. req-7f3a2c",
            )
            event_time = st.text_input(
                "Event time (optional)",
                placeholder="e.g. 2026-10-10 14:30 UTC",
            )
        analyze_incident = st.form_submit_button("Analyze incident", type="primary")

    if analyze_incident:
        if not action_name.strip() and not log_message.strip():
            st.warning("Enter an action name or a full error log message to analyze.")
        else:
            st.session_state["incident_intake"] = {
                "application": application_name,
                "action_name": action_name.strip(),
                "log_message": log_message,
                "service_name": service_name.strip(),
                "environment": environment,
                "correlation_id": correlation_id.strip(),
                "event_time": event_time.strip(),
            }

    incident = st.session_state.get("incident_intake")
    if incident:
        st.markdown(f"**Application:** {incident['application']} | **Action:** {incident['action_name'] or 'Not provided'}")
        advanced_context = [
            f"Service/component: {incident['service_name']}",
            f"Environment: {incident['environment']}",
            f"Request/correlation ID: {incident['correlation_id']}",
            f"Event time: {incident['event_time']}",
        ]
        provided_context = [item for item in advanced_context if not item.endswith(": ") and not item.endswith(": Unspecified")]
        if provided_context:
            st.caption(" | ".join(provided_context))
        explanation, checks = explain_error(f"{incident['action_name']}\n{incident['log_message']}")
        st.markdown("#### Initial assessment")
        st.write(explanation)
        st.markdown("#### Suggested checks")
        for check in checks:
            st.markdown(f"- {check}")
        if incident["log_message"]:
            with st.expander("View complete submitted error log message"):
                st.code(incident["log_message"], language=None)

    api_base_url = resolve_api_base_url()
    with st.sidebar:
        st.subheader("Workspace")
        st.caption("Backend API")
        st.code(api_base_url, language=None)
        retry_requested = st.button("Retry API connection", use_container_width=True)
        st.caption("Change the endpoint with the `VNSP_API_BASE_URL` environment variable.")

    if retry_requested:
        st.session_state.pop("diagnostic_error", None)

    try:
        with st.spinner("Connecting to the local diagnostic service…"):
            health = fetch_health(api_base_url)
            scenario_list = fetch_scenarios(api_base_url)
        st.sidebar.success(f"API {health.status}")
    except ApiConnectionError as exc:
        st.sidebar.error("Backend unavailable")
        st.error(str(exc))
        st.info("Start the FastAPI backend, then select **Retry API connection**.")
        st.code("python -m uvicorn main:app --reload", language="powershell")
        st.markdown("### Scenarios unavailable")
        st.write("The dashboard will show scenario summaries after it reconnects to the backend.")
        return

    if scenario_list.count == 0:
        st.metric("Simulated scenarios", 0)
        st.info("No scenarios are currently available from the backend.")
        return

    categories = {scenario.category for scenario in scenario_list.scenarios}
    successful_lookups = st.session_state.get("successful_lookups", 0)
    st.subheader("Overview")
    st.markdown(
        '<p class="section-note">A local summary of the fabricated diagnostic catalog and this browser session.</p>',
        unsafe_allow_html=True,
    )
    metric_columns = st.columns(3)
    metric_columns[0].metric("Simulated scenarios", scenario_list.count)
    metric_columns[1].metric("Successful lookups", successful_lookups)
    metric_columns[2].metric("Error categories", len(categories))

    st.divider()
    st.subheader("Run a diagnostic")
    scenario_options = [scenario.scenario_id for scenario in scenario_list.scenarios]
    selector_col, button_col = st.columns([3, 1], vertical_alignment="bottom")
    with selector_col:
        selected_id = st.selectbox(
            "Request ID",
            options=scenario_options,
            format_func=lambda scenario_id: next(
                (
                    f"{scenario_id} · {summary.title}"
                    for summary in scenario_list.scenarios
                    if summary.scenario_id == scenario_id
                ),
                scenario_id,
            ),
        )
    with button_col:
        run_diagnostic = st.button(
            "Run diagnostic", type="primary", use_container_width=True
        )

    if run_diagnostic:
        try:
            with st.spinner(f"Analyzing simulated request {selected_id}…"):
                result = fetch_diagnostic(selected_id, api_base_url)
            st.session_state["diagnostic_result"] = result.model_dump(mode="json")
            st.session_state.pop("diagnostic_error", None)
            if result.status == DiagnosticStatus.DIAGNOSED:
                st.session_state["successful_lookups"] = successful_lookups + 1
        except ApiConnectionError as exc:
            st.session_state.pop("diagnostic_result", None)
            st.session_state["diagnostic_error"] = str(exc)

    diagnostic_error = st.session_state.get("diagnostic_error")
    if diagnostic_error:
        st.error(diagnostic_error)

    stored_result = st.session_state.get("diagnostic_result")
    if not stored_result:
        st.info("Choose a request ID and run a diagnostic to view the simulated findings.")
        return

    result = DiagnosticResult.model_validate(stored_result)
    if result.scenario_id != selected_id:
        st.info("Run the selected request to replace the previous diagnostic results.")
        return

    if result.status != DiagnosticStatus.DIAGNOSED:
        st.warning(result.message)
        return

    st.divider()
    st.subheader("Diagnostic results")
    result_columns = st.columns(3)
    result_columns[0].metric("Request ID", result.scenario_id)
    result_columns[1].metric("Error category", _category_label(result.category))
    result_columns[2].metric("Priority", "Not defined")

    if result.title:
        st.markdown(f"**Scenario:** {result.title}")
    st.markdown("#### Probable root cause")
    st.markdown(result.probable_root_cause or "No root cause was provided.")
    st.markdown("#### Recommended troubleshooting steps")
    for step in result.recommended_steps:
        st.markdown(f"- {step}")

    with st.expander("Fabricated diagnostic logs", expanded=False):
        if result.logs:
            log_rows = [
                {
                    "Timestamp (UTC)": log.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "Service": log.service,
                    "Level": log.level,
                    "Message": log.message,
                }
                for log in result.logs
            ]
            st.dataframe(
                pd.DataFrame(log_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No simulated log entries were returned for this request.")


if __name__ == "__main__":
    _render_dashboard()
