"""Tests for the local fabricated-scenario diagnostic engine."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from diagnostics import diagnose_scenario
from models import (
    DiagnosticRequest,
    DiagnosticStatus,
    ErrorCategory,
)


@pytest.mark.parametrize(
    ("scenario_id", "category"),
    [
        ("REQ-1001", ErrorCategory.CREDENTIAL_MISMATCH),
        ("REQ-1002", ErrorCategory.REQUEST_TIMEOUT),
        ("REQ-9999", ErrorCategory.UNKNOWN_ERROR),
    ],
)
def test_known_scenarios_return_cause_steps_and_fabricated_logs(scenario_id, category):
    result = diagnose_scenario(scenario_id)

    assert result.status == DiagnosticStatus.DIAGNOSED
    assert result.scenario_id == scenario_id
    assert result.category == category
    assert result.probable_root_cause
    assert result.recommended_steps
    assert result.logs
    assert all(log.service.startswith("Demo ") for log in result.logs)


def test_request_model_is_accepted_and_identifier_is_normalized():
    request = DiagnosticRequest(scenario_id=" req-1001 ")

    result = diagnose_scenario(request)

    assert result.status == DiagnosticStatus.DIAGNOSED
    assert result.scenario_id == "REQ-1001"


@pytest.mark.parametrize("scenario_id", ["REQ-0000", "REQ-1234", "not-a-scenario"])
def test_unsupported_identifier_returns_not_found(scenario_id):
    result = diagnose_scenario(scenario_id)

    assert result.status == DiagnosticStatus.NOT_FOUND
    assert result.scenario_id == scenario_id.upper()
    assert result.category is None
    assert result.logs == []


@pytest.mark.parametrize(
    "malformed_input",
    [None, 1001, "", "   ", {}, {"scenario_id": "REQ-1001", "unexpected": True}],
)
def test_malformed_input_returns_invalid_request(malformed_input):
    result = diagnose_scenario(malformed_input)

    assert result.status == DiagnosticStatus.INVALID_REQUEST
    assert result.scenario_id == "INVALID"
    assert result.category is None
    assert result.logs == []


def test_request_model_rejects_blank_and_non_string_identifiers():
    with pytest.raises(ValidationError):
        DiagnosticRequest(scenario_id="  ")
    with pytest.raises(ValidationError):
        DiagnosticRequest(scenario_id=1001)


def test_unexpected_lookup_error_returns_safe_structured_result():
    with patch("diagnostics.get_scenario", side_effect=RuntimeError("internal details")):
        result = diagnose_scenario("REQ-1001")

    assert result.status == DiagnosticStatus.ANALYSIS_ERROR
    assert result.category is None
    assert result.logs == []
    assert "internal details" not in result.message
