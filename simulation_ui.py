"""Streamlit presentation for the local mock infrastructure and investigations."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
import inspect
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from models import (
    InvestigationResult,
    ServerInventoryResponse,
    ServiceInventoryResponse,
    ServiceStatusResponse,
    SimulatedLogRecord,
    SimulatedServiceStatus,
    SimulationResetResponse,
    SimulationResult,
    SimulationScenarioName,
)
from simulation_client import SimulationApiError, request_simulation_api, simulation_api_url


SCENARIO_LABELS = {
    SimulationScenarioName.AUTHENTICATION_FAILURE.value: "Authentication failure",
    SimulationScenarioName.REDIS_TIMEOUT.value: "Redis timeout",
    SimulationScenarioName.DATABASE_FAILURE.value: "Database failure",
    SimulationScenarioName.DOWNSTREAM_UNAVAILABLE.value: "Downstream service unavailable",
    SimulationScenarioName.SUCCESSFUL_REQUEST.value: "Successful request",
    SimulationScenarioName.MISSING_CORRELATION.value: "Missing correlation evidence",
    SimulationScenarioName.CONNECTION_REFUSED.value: "Connection refused",
    SimulationScenarioName.MISSING_RESPONSE.value: "Missing response evidence",
}


def _validated(response: Any, model: type) -> Any:
    try:
        return model.model_validate(response)
    except ValidationError:
        raise SimulationApiError("The simulation API returned an unexpected response structure.") from None


def _render_log_table(records: list[SimulatedLogRecord]) -> None:
    rows = [
        {
            "Time (UTC)": record.timestamp.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
            "Level": record.level.value,
            "Server": record.server_id,
            "Service": record.service_name,
            "Event": record.event_name,
            "HTTP": record.http_status_code,
            "Error": record.error_code,
            "Request ID": record.request_id,
            "Source file": record.source_file,
            "Message": record.message,
        }
        for record in records
    ]
    _dataframe(pd.DataFrame(rows))


def _dataframe(frame: pd.DataFrame) -> None:
    """Use Streamlit's current width API with a fallback for declared older versions."""

    width_options = (
        {"width": "stretch"}
        if "width" in inspect.signature(st.dataframe).parameters
        else {"use_container_width": True}
    )
    st.dataframe(frame, **width_options, hide_index=True)


def _display_investigation(report: InvestigationResult) -> None:
    if report.status.value == "not_found":
        st.warning(report.message)
        if report.missing_evidence:
            st.markdown("**Missing evidence**")
            for item in report.missing_evidence:
                st.markdown(f"- {item}")
        if report.candidate_services:
            st.markdown(f"**Candidate services (rule-based):** {', '.join(report.candidate_services)}")
        if report.error_category:
            st.caption(f"Text-based category hint: {report.error_category}; basis: {report.classification_basis}.")
        return

    st.success(report.message)
    col1, col2, col3 = st.columns(3)
    col1.metric("Evidence records", len(report.evidence))
    col2.metric("Confirmed services", len(report.confirmed_services))
    col3.metric("Error category", report.error_category or "No error observed")
    st.caption(f"Classification basis: {report.classification_basis}. Candidate services are heuristic suggestions, not confirmed participants.")
    st.write("**Candidates:** " + (", ".join(report.candidate_services) or "None identified"))
    st.write("**Confirmed by matching logs:** " + (", ".join(report.confirmed_services) or "None"))
    st.write(f"**Request ID:** {report.request_id or 'Not provided'}")
    st.write(f"**Trace ID:** {report.trace_id or 'Not present or not unique in matching evidence'}")

    if report.hypothesis:
        st.markdown("### Possible cause — hypothesis")
        st.write(report.hypothesis)
    elif report.error_category == "success":
        st.info("The correlated records show a successful request. No failure root cause is reported.")

    if report.observed_errors:
        st.markdown("### Observed errors")
        for error in report.observed_errors:
            st.markdown(f"- {error}")
    if report.request_response_details:
        st.markdown("### Request and response details")
        _dataframe(pd.DataFrame([item.model_dump(mode="json") for item in report.request_response_details]))
    st.markdown("### Recommended next checks")
    for check in report.recommended_checks:
        st.markdown(f"- {check}")
    if report.missing_evidence:
        st.markdown("### Evidence limitations")
        for item in report.missing_evidence:
            st.markdown(f"- {item}")
    with st.expander("Correlated evidence and chronological timeline", expanded=True):
        _render_log_table(report.timeline)


def render_simulation_page() -> None:
    """Render the simulation lab as a separate Streamlit multipage view."""

    st.set_page_config(page_title="Simulation Lab · VNSP", layout="wide")
    if st.session_state.pop("clear_investigation_inputs_next_run", False):
        st.session_state.pop("investigation_request_id", None)
        st.session_state.pop("investigation_error_widget", None)
        st.session_state.pop("investigation_error", None)
    st.title("Mock Infrastructure Simulation Lab")
    st.caption(
        "Local-only demo. These logical hosts, services, request IDs, and JSONL records are fictional; no network requests are sent to them."
    )
    st.code(f"Simulation API: {simulation_api_url()}", language=None)

    try:
        inventory_payload = request_simulation_api("GET", "/infrastructure/servers")
        inventory = _validated(inventory_payload, ServerInventoryResponse)
        service_payload = request_simulation_api("GET", "/infrastructure/services")
        service_inventory = _validated(service_payload, ServiceInventoryResponse)
    except SimulationApiError as exc:
        st.error(str(exc))
        st.info("Start the FastAPI backend, then refresh this page.")
        return

    services = service_inventory.services
    service_names = [service.name for service in services]
    with st.expander(f"Simulated inventory · {len(inventory.servers)} servers · {len(services)} services", expanded=True):
        for server in inventory.servers:
            st.markdown(f"**{server.display_name}** · `{server.server_id}` · `{server.host}:{server.port}`")
            st.caption("Logical example host only; no listener or remote server exists.")
            if server.services:
                _dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Service": service.name,
                                "Host:port": f"{service.host}:{service.port}",
                                "Status": service.status.value,
                                "Environment": service.environment,
                                "Dependencies": ", ".join(service.dependencies) or "None",
                                "Log file": service.log_file,
                            }
                            for service in server.services
                        ]
                    )
                )

    st.markdown("### Simulate a request lifecycle")
    with st.form("simulation_scenario_form"):
        scenario_value = st.selectbox(
            "Scenario",
            list(SCENARIO_LABELS),
            format_func=lambda item: f"{SCENARIO_LABELS[item]} · {item}",
        )
        seed = st.number_input("Deterministic seed", min_value=0, max_value=2**32 - 1, value=42)
        generate = st.form_submit_button("Generate simulated incident", type="primary")

    if generate:
        try:
            payload = request_simulation_api(
                "POST",
                f"/simulation/scenarios/{scenario_value}",
                payload={"seed": int(seed)},
            )
            result = _validated(payload, SimulationResult)
            st.session_state["simulation_result"] = result.model_dump(mode="json")
            st.session_state["simulation_scenario"] = scenario_value
            st.session_state["investigation_request_id"] = result.request_id or ""
            default_error = {
                "authentication_failure": "authentication credential mismatch",
                "redis_timeout": "Redis operation timeout",
                "database_failure": "database connection failure",
                "downstream_unavailable": "notification downstream unavailable",
                "successful_request": "successful circuit status request",
                "missing_correlation": "request arrived without correlation ID",
                "connection_refused": "connection refused by payment service upstream",
                "missing_response": "response not observed for circuit request",
            }[scenario_value]
            st.session_state["investigation_error"] = default_error
            st.session_state["investigation_error_widget"] = default_error
            st.rerun()
        except SimulationApiError as exc:
            st.error(str(exc))

    stored_simulation = st.session_state.get("simulation_result")
    if stored_simulation:
        result = _validated(stored_simulation, SimulationResult)
        st.success(f"{SCENARIO_LABELS[result.scenario.value]} generated · outcome `{result.outcome}` · HTTP {result.http_status_code or 'not recorded'}")
        st.write(f"**Request ID:** {result.request_id or 'Not generated (missing-correlation scenario)'}")
        st.write(f"**Trace ID:** {result.trace_id or 'Not generated'} · **Records:** {result.generated_log_count}")
        with st.expander("Generated service records", expanded=False):
            _render_log_table(result.logs)

    st.markdown("### Investigate simulated evidence")
    environment_options = ["Any"] + sorted({service.environment for service in services})
    with st.form("investigation_form"):
        application_name = st.text_input("Application", value="VNSP")
        error_description = st.text_area(
            "Error description",
            value=st.session_state.get("investigation_error", ""),
            key="investigation_error_widget",
            help="The rule-based classifier suggests candidate services; only matching logs confirm participants.",
        )
        request_id = st.text_input(
            "Request ID (optional; required to correlate evidence)",
            key="investigation_request_id",
        )
        service_options = ["Any"] + service_names
        service_filter = st.selectbox("Service filter", service_options)
        environment_filter = st.selectbox("Environment filter", environment_options)
        filter_by_date = st.checkbox("Limit to a UTC date range")
        date_range: tuple[date, date] | date | None = None
        if filter_by_date:
            date_range = st.date_input(
                "Date range (generated fixtures use 2026-10-10 UTC)",
                value=(date(2026, 10, 10), date(2026, 10, 10)),
            )
        submit_investigation = st.form_submit_button("Investigate logs", type="primary")

    if submit_investigation:
        body: dict[str, Any] = {
            "application_name": application_name,
            "error_description": error_description,
        }
        if request_id.strip():
            body["request_id"] = request_id.strip()
        if service_filter != "Any":
            body["service_name"] = service_filter
        if environment_filter != "Any":
            body["environment"] = environment_filter
        if isinstance(date_range, tuple) and len(date_range) == 2:
            body["start_time"] = datetime.combine(date_range[0], time.min, timezone.utc).isoformat()
            body["end_time"] = datetime.combine(date_range[1], time.max, timezone.utc).isoformat()
        try:
            payload = request_simulation_api("POST", "/investigate", payload=body)
            report = _validated(payload, InvestigationResult)
            st.session_state["investigation_result"] = report.model_dump(mode="json")
        except SimulationApiError as exc:
            st.error(str(exc))

    stored_report = st.session_state.get("investigation_result")
    if stored_report:
        try:
            _display_investigation(_validated(stored_report, InvestigationResult))
        except SimulationApiError as exc:
            st.error(str(exc))

    st.markdown("### Simulated service controls")
    with st.form("service_status_form"):
        selected_service = st.selectbox("Service", service_names)
        selected_status = st.selectbox("Set simulated status", [status.value for status in SimulatedServiceStatus])
        update_status = st.form_submit_button("Update status")
    if update_status:
        try:
            status_payload = request_simulation_api(
                "PATCH",
                f"/infrastructure/services/{selected_service}/status",
                payload={"status": selected_status},
            )
            updated = _validated(status_payload, ServiceStatusResponse)
            st.success(f"{updated.service.name} is now marked {updated.service.status.value} in simulation state.")
            st.rerun()
        except SimulationApiError as exc:
            st.error(str(exc))

    if st.button("Reset generated logs and service statuses"):
        try:
            reset = _validated(
                request_simulation_api("POST", "/simulation/reset"),
                SimulationResetResponse,
            )
            st.session_state.pop("simulation_result", None)
            st.session_state.pop("investigation_result", None)
            st.session_state["clear_investigation_inputs_next_run"] = True
            st.success(f"{reset.message} Removed {reset.removed_log_files} log files.")
            st.rerun()
        except SimulationApiError as exc:
            st.error(str(exc))
