"""UI client and Streamlit smoke tests for the real investigation workflow."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path
import tempfile

APP_TEST_TEMP = Path(__file__).parent.parent / "work" / "streamlit-app-test-tmp"
APP_TEST_TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(APP_TEST_TEMP)

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from investigation_client import InvestigationApiError, fetch_investigation_status, submit_investigation
from incident_ui import parse_event_time
from models import InvestigationRequest


def test_parse_time_range_requires_timezone_and_uses_five_minute_window():
    start, end = parse_event_time("2026-10-10T10:00:00Z")
    assert start is not None and end is not None
    assert (end - start).total_seconds() == 600
    assert start.tzinfo == timezone.utc
    with pytest.raises(ValueError, match="Include a timezone"):
        parse_event_time("2026-10-10T10:00:00")


def test_investigation_client_submits_structured_payload_and_validates_status():
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["payload"] = request.read().decode()
        return httpx.Response(202, json={
            "investigation_id": "demo-job-id",
            "status": "queued",
            "stage": "queued",
            "message": "accepted",
            "progress": 0,
            "started_at": "2026-10-10T10:00:00Z",
            "updated_at": "2026-10-10T10:00:00Z",
        })

    request = InvestigationRequest(
        application_name="VNSP", error_description="password=secret timeout", request_id="SIM-REDIS-1"
    )
    job = submit_investigation("http://api.test", request, transport=httpx.MockTransport(handler))
    assert job.investigation_id == "demo-job-id"
    assert job.progress == 0
    assert observed["method"] == "POST"
    assert "secret" not in observed["payload"]
    assert "[REDACTED]" in observed["payload"]


def test_investigation_client_sanitizes_api_errors():
    transport = httpx.MockTransport(lambda request: httpx.Response(422, json={
        "detail": [{"loc": ["body", "error_description"], "msg": "Invalid or missing value.", "type": "value_error"}]
    }))
    with pytest.raises(InvestigationApiError, match="error_description") as error:
        fetch_investigation_status("http://api.test", "demo-id", transport=transport)
    assert "input" not in str(error.value).casefold()


def test_main_streamlit_page_renders_incident_inputs_without_api():
    page_path = Path(__file__).parent.parent / "ui.py"
    app = AppTest.from_file(str(page_path)).run()
    assert not app.exception
    assert any("VNSP Intelligent Troubleshooter" in item.value for item in app.markdown)
    assert any("Application Name" in item.label for item in app.selectbox)
    assert any("Error Message / Error Description" in item.label for item in app.text_area)
    assert any(item.label == "Validate" for item in app.button)
