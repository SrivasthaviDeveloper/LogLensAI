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
from incident_analysis import explain_error


ROOT = Path(__file__).parent
CONFIG_PATH = Path(os.getenv("VNSP_CONFIG", ROOT / "config" / "services.json"))


def load_catalog() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        st.error(f"Could not load service catalog at {CONFIG_PATH}: {exc}")
        return {"applications": {}}


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
