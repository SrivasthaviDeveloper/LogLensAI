"""Streamlit intake, real backend job polling, and evidence report rendering."""

from __future__ import annotations

import json
import inspect
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from investigation_client import InvestigationApiError, fetch_investigation_report, fetch_investigation_status, submit_investigation
from models import InvestigationJobResponse, InvestigationLifecycleStatus, InvestigationRequest, InvestigationReport


ACTIVE = {InvestigationLifecycleStatus.QUEUED, InvestigationLifecycleStatus.RUNNING}


def _dataframe(frame: pd.DataFrame) -> None:
    """Use the Streamlit sizing option supported by the installed version."""

    options = {"width": "stretch"} if "width" in inspect.signature(st.dataframe).parameters else {"use_container_width": True}
    st.dataframe(frame, hide_index=True, **options)


def parse_event_time(value: str) -> tuple[datetime | None, datetime | None]:
    """Parse an optional ISO timestamp as a five-minute UTC range."""

    if not value.strip():
        return None, None
    try:
        instant = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Use an ISO-8601 timestamp, for example 2026-10-10T10:00:00Z.") from exc
    if instant.tzinfo is None:
        raise ValueError("Include a timezone in the event time, such as Z or +05:30.")
    instant = instant.astimezone(timezone.utc)
    return instant - timedelta(minutes=5), instant + timedelta(minutes=5)


def _render_report(report: InvestigationReport) -> None:
    st.subheader("Investigation report")
    st.caption(f"Investigation ID: `{report.investigation_id}` · Source: **{report.source_label}**")
    if report.status == InvestigationLifecycleStatus.PARTIAL_SUCCESS:
        st.warning("Partial result: evidence is incomplete. Review the evidence gaps before drawing conclusions.")
    else:
        st.success("Investigation completed. Historical classifier results are suggestions; confirmed components come from retrieved evidence.")

    metrics = st.columns(4)
    metrics[0].metric("Error category", report.error_category.replace("_", " ").title())
    metrics[1].metric("Classifier confidence", f"{report.classification_confidence:.0%}")
    metrics[2].metric("Matching log events", len(report.evidence))
    metrics[3].metric("Confirmed services", len(report.confirmed_components))
    st.write("**Application:**", report.application_name)
    st.write("**Input error:**", report.error_message)
    st.write("**Model:**", report.classifier_model, "·", report.classifier_status)
    st.write("**Likely failure point:**", report.failure_point or "Not confirmed")
    st.write("**Request ID:**", report.request_id or "Not located")
    st.write("**Trace ID:**", report.trace_id or "Not consistently present")
    st.markdown("### Error interpretation")
    st.write(report.error_interpretation)
    st.markdown("### Root-cause hypothesis")
    st.write(report.root_cause_hypothesis or "No evidence-supported root-cause hypothesis is available.")

    st.markdown("### Ranked service candidates")
    if report.likely_components:
        _dataframe(pd.DataFrame([
            {"Service": item.service_name, "Server": item.server_id, "Model score": item.score,
             "Confirmed by logs": item.confirmed_by_evidence, "Why selected": item.selection_reason}
            for item in report.likely_components
        ]))
    else:
        st.info("No service candidate could be confirmed from the configured service registry.")

    st.markdown("### Matching log evidence and request/response trace")
    if report.evidence:
        _dataframe(pd.DataFrame([
            {"Timestamp": item.timestamp.astimezone(timezone.utc).isoformat(), "Server": item.server_id,
             "Service": item.service_name, "Level": item.level.value, "Request ID": item.request_id or item.correlation_id,
             "Trace ID": item.trace_id, "HTTP": item.http_status_code, "Error code": item.error_code,
             "Source": item.source_file, "Message": item.message}
            for item in report.request_response_trace
        ]))
    else:
        st.info("No matching request/response logs were retrieved.")

    st.markdown("### Recommended next steps")
    for item in report.recommended_next_steps:
        st.markdown(f"- {item}")
    if report.evidence_gaps:
        st.markdown("### Evidence gaps")
        st.warning("Insufficient evidence to confirm the root cause. The following additional logs or identifiers are required:")
        for gap in report.evidence_gaps:
            st.markdown(f"- {gap}")
    st.download_button(
        "Download report JSON",
        data=json.dumps(report.model_dump(mode="json"), indent=2),
        file_name=f"loglens-investigation-{report.investigation_id}.json",
        mime="application/json",
    )


@st.dialog("Investigation progress")
def _show_progress(api_base_url: str, investigation_id: str) -> None:
    """Poll the actual job status while the backend performs its work."""

    try:
        status = fetch_investigation_status(api_base_url, investigation_id)
    except InvestigationApiError as exc:
        st.error(str(exc))
        if st.button("Close", key="close_investigation_error"):
            st.session_state["show_investigation_dialog"] = False
            st.rerun()
        return
    st.caption(f"Investigation ID: `{status.investigation_id}`")
    st.write(status.message)
    st.progress(status.progress)
    st.caption(f"Current backend stage: `{status.stage}` · State: `{status.status.value}`")
    if status.status in ACTIVE:
        with st.spinner("Waiting for the current backend stage to finish..."):
            time.sleep(0.75)
        st.rerun()
        return
    if status.status == InvestigationLifecycleStatus.FAILED:
        st.error(status.error or "Investigation failed. The original input is retained for retry.")
    elif status.status == InvestigationLifecycleStatus.PARTIAL_SUCCESS:
        st.warning("The job completed, but retrieved evidence was incomplete.")
    else:
        st.success("Investigation completed.")
    if st.button("Close", key="close_investigation_progress"):
        st.session_state["show_investigation_dialog"] = False
        st.rerun()


def render_incident_workflow(api_base_url: str, applications: list[str]) -> None:
    """Render the structured intake, job popup, report, and retry action."""

    st.subheader("Incident investigation")
    st.caption("Submit an incident to the backend. The current default source is clearly labeled local fictional mock logs.")
    with st.form("incident_intake", clear_on_submit=False):
        required_col, action_col = st.columns(2)
        with required_col:
            application_name = st.selectbox("Application Name · required", applications or ["VNSP"])
        with action_col:
            action_name = st.text_input("Action Name · optional", placeholder="e.g. CreateCircuit")
        error_message = st.text_area(
            "Error Message / Error Description · required",
            placeholder="Paste a sanitized error message. Do not include passwords, tokens, private keys, or personal data.",
            height=150,
        )
        with st.expander("Optional investigation context"):
            columns = st.columns(2)
            with columns[0]:
                environment = st.selectbox("Environment", ["Unspecified", "Development", "Testing", "Staging", "Production"])
                request_id = st.text_input("Request / correlation ID", placeholder="SIM-REDIS-0000000042")
                service_name = st.text_input("Known microservice", placeholder="redis-cache")
            with columns[1]:
                event_time = st.text_input("Approximate event time (ISO-8601 with timezone)", placeholder="2026-10-10T10:00:00Z")
                server_name = st.text_input("Server ID or configured hostname", placeholder="data-server-01")
                error_code = st.text_input("Error code / exception type", placeholder="REDIS_OPERATION_TIMEOUT")
            additional_context = st.text_area("Additional diagnostic context", height=80)
        submitted = st.form_submit_button("Validate", type="primary")

    if submitted:
        if not application_name.strip():
            st.error("Application Name is required.")
        elif not error_message.strip():
            st.error("Error Message / Error Description is required.")
        else:
            try:
                start_time, end_time = parse_event_time(event_time)
                request = InvestigationRequest(
                    application_name=application_name,
                    action_name=action_name,
                    error_description=error_message,
                    request_id=request_id or None,
                    start_time=start_time,
                    end_time=end_time,
                    service_name=service_name or None,
                    server_name=server_name or None,
                    error_code=error_code or None,
                    additional_context=additional_context or None,
                    environment=None if environment == "Unspecified" else environment.casefold(),
                )
                st.session_state["investigation_request"] = request.model_dump(mode="json", exclude_none=True)
                job = submit_investigation(api_base_url, request)
                st.session_state["investigation_id"] = job.investigation_id
                st.session_state["show_investigation_dialog"] = True
                st.session_state.pop("investigation_api_error", None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except InvestigationApiError as exc:
                st.session_state["investigation_api_error"] = str(exc)

    if st.session_state.get("investigation_api_error"):
        st.error(st.session_state["investigation_api_error"])
    investigation_id = st.session_state.get("investigation_id")
    if investigation_id and st.session_state.get("show_investigation_dialog"):
        _show_progress(api_base_url, investigation_id)
    if investigation_id:
        try:
            status = fetch_investigation_status(api_base_url, investigation_id)
            if status.status == InvestigationLifecycleStatus.FAILED:
                if st.button("Retry investigation", key="retry_investigation"):
                    previous = InvestigationRequest.model_validate(st.session_state["investigation_request"])
                    job = submit_investigation(api_base_url, previous)
                    st.session_state["investigation_id"] = job.investigation_id
                    st.session_state["show_investigation_dialog"] = True
                    st.rerun()
            elif status.status not in ACTIVE:
                report = fetch_investigation_report(api_base_url, investigation_id)
                _render_report(report)
        except InvestigationApiError as exc:
            st.error(str(exc))
