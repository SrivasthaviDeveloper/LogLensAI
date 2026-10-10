"""Bounded Streamlit HTTP client for background investigations."""

from __future__ import annotations

from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from models import InvestigationJobResponse, InvestigationReport, InvestigationRequest


T = TypeVar("T", bound=BaseModel)


class InvestigationApiError(RuntimeError):
    """Safe user-facing error returned by the investigation API client."""


def _request(
    method: str,
    base_url: str,
    path: str,
    *,
    payload: dict | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    try:
        with httpx.Client(timeout=httpx.Timeout(3.0, connect=2.0), transport=transport) as client:
            response = client.request(method, f"{base_url.rstrip('/')}{path}", json=payload)
    except httpx.HTTPError:
        raise InvestigationApiError("Could not reach the troubleshooting API. Check the backend and retry.") from None
    try:
        body = response.json()
    except ValueError:
        raise InvestigationApiError("The troubleshooting API returned an unreadable response.") from None
    if response.status_code >= 400:
        detail = body.get("detail", "") if isinstance(body, dict) else ""
        if response.status_code == 422 and isinstance(detail, list):
            fields = ", ".join(str(item.get("loc", ["input"])[-1]) for item in detail if isinstance(item, dict))
            raise InvestigationApiError(f"The investigation request was invalid. Check: {fields or 'required fields'}.")
        raise InvestigationApiError(f"The investigation API returned HTTP {response.status_code}: {detail or 'request failed'}")
    if not isinstance(body, dict):
        raise InvestigationApiError("The troubleshooting API returned an unexpected response.")
    return body


def _validate(model: type[T], body: dict) -> T:
    try:
        return model.model_validate(body)
    except ValidationError:
        raise InvestigationApiError("The troubleshooting API returned an unexpected response structure.") from None


def submit_investigation(
    base_url: str,
    request: InvestigationRequest,
    *,
    transport: httpx.BaseTransport | None = None,
) -> InvestigationJobResponse:
    body = _request("POST", base_url, "/investigations", payload=request.model_dump(mode="json", exclude_none=True), transport=transport)
    return _validate(InvestigationJobResponse, body)


def fetch_investigation_status(
    base_url: str,
    investigation_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> InvestigationJobResponse:
    body = _request("GET", base_url, f"/investigations/{investigation_id}", transport=transport)
    return _validate(InvestigationJobResponse, body)


def fetch_investigation_report(
    base_url: str,
    investigation_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> InvestigationReport:
    body = _request("GET", base_url, f"/investigations/{investigation_id}/report", transport=transport)
    return _validate(InvestigationReport, body)
