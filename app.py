from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError


ROOT = Path(__file__).parent
CONFIG_PATH = Path(os.getenv("VNSP_CONFIG", ROOT / "config" / "services.json"))


def load_catalog() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        st.error(f"Could not load service catalog at {CONFIG_PATH}: {exc}")
        return {"applications": {}}


def explain_error(error: str) -> tuple[str, list[str]]:
    text = error.lower()
    if any(term in text for term in ("credential", "unauthorized", "401", "invalid token", "authentication")):
        return (
            "The request appears to be failing authentication or authorization. A credential mismatch can occur when the caller and VNSP use different client IDs, secrets, token audiences, scopes, or environment-specific credentials.",
            ["Compare the caller's credential identity and environment with VNSP's configured client; do not paste secrets into this tool.", "Check token expiry, audience, scopes, clock skew, and any recent secret rotation.", "Use the request/correlation ID to find the corresponding authentication decision in both applications."],
        )
    if any(term in text for term in ("timeout", "timed out", "deadline exceeded", "504", "gateway timeout")):
        return (
            "The request exceeded a time limit somewhere in the call path. The slow segment may be the caller, a VNSP service, a downstream dependency, or an orchestration step.",
            ["Compare timestamps and durations across caller and VNSP logs using the same correlation ID.", "Check dependency latency, retries, connection pool saturation, and configured client/load-balancer timeouts.", "Determine whether the operation completed despite the caller timing out before retrying it."],
        )
    if any(term in text for term in ("5xx", "500", "502", "503", "internal server error")):
        return (
            "The error suggests a server-side failure or an unavailable upstream. The first failing service in the request path is the key to finding the cause.",
            ["Trace the correlation ID through each service and identify the first error, not only the final response.", "Check recent deployment/configuration changes and dependency health around the event time.", "Look for exception class, stack trace, retry exhaustion, or circuit-breaker events."],
        )
    return (
        "No specific error family was recognized. Use the request ID, exact message, and event time to trace the failure through the mapped services.",
        ["Locate the first failing service and inspect its surrounding log events.", "Compare the request and response at the caller and service boundary.", "Check for recent deployment, configuration, credential, or dependency changes."],
    )


def search_cloudwatch(region: str, log_groups: list[str], query: str, start: datetime, end: datetime, limit: int) -> list[dict[str, Any]]:
    # CloudWatch Logs filter patterns are substring searches when wrapped in quotes.
    client = boto3.Session().client("logs", region_name=region)
    results: list[dict[str, Any]] = []
    for group in log_groups:
        kwargs: dict[str, Any] = {
            "logGroupName": group,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": min(limit, 10000),
        }
        if query.strip():
            safe_query = query.strip().replace('"', r'\"')
            kwargs["filterPattern"] = f'"{safe_query}"'
        response = client.filter_log_events(**kwargs)
        for event in response.get("events", []):
            results.append({
                "time": datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
                "log_group": group,
                "stream": event.get("logStreamName", ""),
                "message": event.get("message", "").strip(),
            })
    return sorted(results, key=lambda item: item["time"], reverse=True)[:limit]


st.set_page_config(page_title="VNSP Incident Triage", page_icon="🔎", layout="wide")
st.title("VNSP Incident Triage")
st.caption("Map an incident to the services and AWS regions to inspect, then search CloudWatch for matching evidence.")

catalog = load_catalog()
applications = catalog.get("applications", {})
if not applications:
    st.warning("No applications are configured. Add entries to config/services.json to enable service and log-group mapping.")

with st.form("incident"):
    left, right = st.columns(2)
    with left:
        app_name = st.selectbox("Application", list(applications) or ["VNSP"])
        error_text = st.text_area("Error or symptom", placeholder="e.g. credential mismatch, request timed out, or paste a sanitized error message", height=120)
    with right:
        correlation_id = st.text_input("Request / correlation ID (optional)")
        lookback = st.selectbox("Search window", [15, 30, 60, 180, 360], index=2, format_func=lambda n: f"Last {n} minutes")
    submitted = st.form_submit_button("Check and validate", type="primary")

if submitted:
    app_config = applications.get(app_name, {})
    services = app_config.get("services", [])
    st.subheader("Investigation targets")
    if services:
        for service in services:
            st.markdown(f"**{service.get('name', 'Unnamed service')}** · {service.get('role', 'Service')} · `{service.get('environment', 'unspecified')}`")
            st.caption(f"Regions: {', '.join(service.get('regions', [])) or 'not configured'} | Log groups: {', '.join(service.get('log_groups', [])) or 'not configured'}")
    else:
        st.info("No service mapping is configured for this application yet.")

    if error_text.strip():
        explanation, checks = explain_error(error_text)
        st.subheader("What the error may mean")
        st.write(explanation)
        st.subheader("Suggested checks")
        for check in checks:
            st.markdown(f"- {check}")

    targets = [(service, region) for service in services for region in service.get("regions", []) if service.get("log_groups")]
    if not targets:
        st.info("CloudWatch search needs at least one service with both a region and log group in the service catalog.")
    else:
        all_groups: dict[str, list[str]] = {}
        for service, region in targets:
            all_groups.setdefault(region, []).extend(service.get("log_groups", []))
        query = correlation_id.strip() or error_text.strip()
        start = datetime.now(timezone.utc) - timedelta(minutes=lookback)
        end = datetime.now(timezone.utc)
        st.subheader("CloudWatch evidence")
        for region, groups in all_groups.items():
            groups = list(dict.fromkeys(groups))
            with st.expander(f"{region} · {len(groups)} log group(s)", expanded=True):
                try:
                    events = search_cloudwatch(region, groups, query, start, end, 100)
                    if events:
                        st.dataframe(events, use_container_width=True, hide_index=True)
                    else:
                        st.info("No matching events found in the selected window.")
                except NoCredentialsError:
                    st.error("AWS credentials were not found. Configure the AWS SDK default credential chain (for example, an AWS profile or role) and retry.")
                except ClientError as exc:
                    st.error(f"AWS returned an error for {region}: {exc.response.get('Error', {}).get('Code', 'ClientError')} — {exc.response.get('Error', {}).get('Message', str(exc))}")
                except (BotoCoreError, OSError) as exc:
                    st.error(f"Could not query CloudWatch in {region}: {exc}")

with st.expander("Security and scope"):
    st.write("This app performs read-only CloudWatch log searches using the AWS SDK credentials already configured for the running environment. It does not ask for or store AWS keys, connect to hosts over SSH, or change infrastructure. Keep incident text free of passwords, tokens, and other secrets.")
