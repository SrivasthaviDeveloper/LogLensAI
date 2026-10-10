"""Small, testable HTTP client for the simulation endpoints used by Streamlit."""

from __future__ import annotations

import os
from typing import Any

import httpx


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
SIMULATION_API_TIMEOUT_SECONDS = 5.0


class SimulationApiError(RuntimeError):
    """Safe user-facing error for simulation API failures."""


def simulation_api_url() -> str:
    """Use the same configurable base URL as the existing diagnostic page."""

    return (os.getenv("VNSP_API_BASE_URL") or DEFAULT_API_BASE_URL).strip().rstrip("/")


def request_simulation_api(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    base_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    """Make a bounded request and return JSON without leaking transport detail."""

    url = (base_url or simulation_api_url()).rstrip("/") + path
    try:
        with httpx.Client(timeout=SIMULATION_API_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.request(method, url, params=params, json=payload)
    except (httpx.HTTPError, ValueError):
        raise SimulationApiError(
            "Could not reach the local simulation API. Start the FastAPI backend and retry."
        ) from None
    if not 200 <= response.status_code < 300:
        raise SimulationApiError(
            f"The local simulation API returned HTTP {response.status_code}. Check the request and retry."
        )
    try:
        return response.json()
    except ValueError:
        raise SimulationApiError("The local simulation API returned an unreadable response.") from None
