"""End-to-end tests for model inference, background jobs, and guarded SFTP retrieval."""

from __future__ import annotations

import io
import json
import socket
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import log_providers as provider_module
from classifier import ClassifierError, TfidfIncidentClassifier, evaluate_model, train_model
from infrastructure import InfrastructureManager
from log_providers import LogSourceError, SftpLogProvider, create_log_provider
from models import InvestigationLifecycleStatus, InvestigationRequest, SimulationScenarioName
from orchestration import InvestigationJobStore, InvestigationOrchestrator
from security import redact_sensitive_text
from simulation import generate_scenario


def test_train_infer_evaluate_and_unknown_application(tmp_path):
    artifact_path = tmp_path / "model.json"
    artifact = train_model(artifact_path=artifact_path)
    assert artifact["training_rows"] >= 10
    assert "fictional" in artifact["training_data_label"]
    model = TfidfIncidentClassifier.load(artifact_path)
    result = model.classify(InvestigationRequest(application_name="VNSP", error_description="Redis operation timeout"))
    assert result["status"] == "classified"
    assert result["category"] == "request_timeout"
    assert result["candidates"][0]["service_name"] == "redis-cache"
    assert 0 <= result["confidence"] <= 1
    strict = TfidfIncidentClassifier.load(artifact_path, confidence_threshold=0.99)
    assert strict.classify(InvestigationRequest(
        application_name="VNSP", error_description="some unusual uncatalogued event"
    ))["status"] == "insufficient_evidence"
    assert evaluate_model()["evaluated_examples"] == artifact["training_rows"]
    assert TfidfIncidentClassifier.load(artifact_path).classify(
        InvestigationRequest(application_name="Other App", error_description="something failed")
    )["status"] == "insufficient_evidence"


def test_missing_model_is_reported(tmp_path):
    with pytest.raises(ClassifierError, match="artifact is not available"):
        TfidfIncidentClassifier.load(tmp_path / "missing.json")


def test_request_redacts_secrets_before_analysis_or_storage():
    request = InvestigationRequest(
        application_name=" VNSP ",
        error_description="token audience mismatch password=TopSecret Bearer abc.def.ghi",
        additional_context="client_secret:another-secret",
    )
    rendered = f"{request.error_description} {request.additional_context}"
    assert "TopSecret" not in rendered
    assert "abc.def.ghi" not in rendered
    assert "another-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert "PRIVATE KEY" in redact_sensitive_text("-----BEGIN PRIVATE KEY-----x-----END PRIVATE KEY-----")


def test_local_end_to_end_redis_request_is_correlated_and_evidence_linked(tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    simulated = generate_scenario(SimulationScenarioName.REDIS_TIMEOUT, manager, seed=741)
    request = InvestigationRequest(
        application_name="VNSP",
        action_name="GetCircuitStatus",
        error_description="Redis operation timeout after retry",
        error_code="REDIS_OPERATION_TIMEOUT",
        request_id=simulated.request_id,
    )
    stages: list[str] = []
    report = InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()).run(
        "demo-investigation-741", request, lambda stage, _message, _progress: stages.append(stage)
    )
    assert stages == ["classifying", "discovering", "connecting_log_source", "retrieving_logs", "correlating", "analyzing", "preparing_report"]
    assert report.status == InvestigationLifecycleStatus.COMPLETED
    assert report.request_id == simulated.request_id
    assert report.trace_id == simulated.trace_id
    assert report.error_category == "request_timeout"
    assert report.source_label.startswith("local mock logs")
    assert report.failure_point == "redis-cache"
    assert {event.service_name for event in report.evidence} >= {"api-gateway", "circuit-service", "redis-cache"}
    assert all(event.source_file.endswith(".jsonl") for event in report.evidence)
    assert all(item.confirmed_by_evidence for item in report.likely_components if item.service_name in report.confirmed_components)


def test_unknown_request_id_returns_partial_report_without_invented_evidence(tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    report = InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()).run(
        "demo-investigation-missing",
        InvestigationRequest(application_name="VNSP", error_description="Redis timeout", request_id="SIM-REDIS-UNKNOWN"),
        lambda *_: None,
    )
    assert report.status == InvestigationLifecycleStatus.PARTIAL_SUCCESS
    assert report.request_id == "SIM-REDIS-UNKNOWN"
    assert report.evidence == []
    assert any("was not found" in gap for gap in report.evidence_gaps)
    assert report.root_cause_hypothesis is None


def test_request_id_can_be_inferred_but_is_labeled_as_similarity(tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    generate_scenario(SimulationScenarioName.REDIS_TIMEOUT, manager, seed=742)
    report = InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()).run(
        "demo-investigation-inferred",
        InvestigationRequest(application_name="VNSP", error_description="Redis timeout after retry"),
        lambda *_: None,
    )
    assert report.evidence
    assert report.request_id == "SIM-REDIS-0000000742"
    assert any("inferred" in gap for gap in report.evidence_gaps)
    assert report.status == InvestigationLifecycleStatus.PARTIAL_SUCCESS


def test_connection_refused_confirms_gateway_failure_without_false_downstream_contact(tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    simulation = generate_scenario(SimulationScenarioName.CONNECTION_REFUSED, manager, seed=744)
    report = InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()).run(
        "demo-connection-refused",
        InvestigationRequest(application_name="VNSP", error_description="connection refused by payment service", request_id=simulation.request_id),
        lambda *_: None,
    )
    assert report.failure_point == "api-gateway"
    assert "api-gateway" in report.confirmed_components
    assert "payment-service" not in report.confirmed_components
    assert report.evidence[0].http_status_code is None
    assert any(event.error_code == "CONNECTION_REFUSED" for event in report.evidence)


def test_missing_response_is_partial_and_does_not_claim_a_root_cause(tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    simulation = generate_scenario(SimulationScenarioName.MISSING_RESPONSE, manager, seed=745)
    report = InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()).run(
        "demo-missing-response",
        InvestigationRequest(application_name="VNSP", error_description="response not observed", request_id=simulation.request_id),
        lambda *_: None,
    )
    assert report.status == InvestigationLifecycleStatus.PARTIAL_SUCCESS
    assert report.root_cause_hypothesis is None
    assert report.failure_point is None
    assert any("No response status" in gap for gap in report.evidence_gaps)


def test_job_store_reports_terminal_status_and_report(tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    simulation = generate_scenario(SimulationScenarioName.SUCCESSFUL_REQUEST, manager, seed=743)
    jobs = InvestigationJobStore(InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()), workers=1)
    submitted = jobs.submit(InvestigationRequest(
        application_name="VNSP", error_description="successful circuit request", request_id=simulation.request_id
    ))
    deadline = time.monotonic() + 5
    status = submitted
    while status.status in {InvestigationLifecycleStatus.QUEUED, InvestigationLifecycleStatus.RUNNING} and time.monotonic() < deadline:
        time.sleep(0.01)
        status = jobs.get_status(submitted.investigation_id)
    assert status.status == InvestigationLifecycleStatus.COMPLETED
    assert status.progress == 100
    assert jobs.get_report(submitted.investigation_id) is not None
    jobs.executor.shutdown(wait=True)


def test_successful_response_overrides_misleading_historical_error_similarity(tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    simulation = generate_scenario(SimulationScenarioName.SUCCESSFUL_REQUEST, manager, seed=746)
    report = InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()).run(
        "demo-success-no-error",
        InvestigationRequest(
            application_name="VNSP",
            error_description="successful circuit status request",
            request_id=simulation.request_id,
        ),
        lambda *_: None,
    )
    assert report.error_category == "no_error_observed"
    assert report.failure_point is None
    assert report.root_cause_hypothesis is None
    assert "not reproduced" in report.error_interpretation
    assert report.evidence and all((event.http_status_code or 200) < 400 for event in report.evidence)


def test_failed_job_hides_internal_provider_detail(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    jobs = InvestigationJobStore(InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()), workers=1)
    monkeypatch.setenv("VNSP_LOG_SOURCE", "unsupported-source")
    submitted = jobs.submit(InvestigationRequest(application_name="VNSP", error_description="timeout"))
    deadline = time.monotonic() + 5
    status = submitted
    while status.status in {InvestigationLifecycleStatus.QUEUED, InvestigationLifecycleStatus.RUNNING} and time.monotonic() < deadline:
        time.sleep(0.01)
        status = jobs.get_status(submitted.investigation_id)
    assert status.status == InvestigationLifecycleStatus.FAILED
    assert status.error and "sensitive details" in status.error
    assert "unsupported-source" not in status.message
    jobs.executor.shutdown(wait=True)


def _install_fake_paramiko(monkeypatch, *, fail_connect: bool = False, connect_error: str | None = None, missing_file: bool = False, read_timeout: bool = False, directory_denied: bool = False, directory_closed: bool = False):
    log_line = json.dumps({
        "timestamp": "2026-10-10T10:00:00Z", "level": "ERROR", "request_id": "SIM-REDIS-0000000001",
        "trace_id": "TRACE-REDIS-0000000001", "event_name": "operation_timeout",
        "message": "Redis operation timed out password=do-not-leak", "error_code": "REDIS_OPERATION_TIMEOUT",
    }).encode() + b"\n"
    observed = {"policy": None, "closed": False, "path": None}

    class FakeSSHException(Exception):
        pass

    class FakeAuthenticationException(FakeSSHException):
        pass

    class FakeBadHostKeyException(FakeSSHException):
        pass

    class FakeNoValidConnectionsError(FakeSSHException):
        pass

    class FakeTimeoutFile(io.BytesIO):
        def read(self, *_args, **_kwargs):
            raise socket.timeout("private detail")

    class FakeSftp:
        def stat(self, path):
            observed["path"] = path
            return SimpleNamespace(st_size=len(log_line))

        def open(self, path, mode):
            observed["path"] = path
            if missing_file:
                raise FileNotFoundError(path)
            if read_timeout:
                return FakeTimeoutFile(log_line)
            return io.BytesIO(log_line)

        def listdir(self, path):
            observed["directory"] = path
            if directory_closed:
                raise EOFError("SFTP subsystem exited with status 127")
            if directory_denied:
                raise PermissionError("private directory detail")
            return ["redis-cache.jsonl"]

        def normalize(self, path):
            assert path == "."
            return "/home/demo"

        def close(self):
            observed["closed"] = True

    class FakeClient:
        def load_host_keys(self, path):
            observed["known_hosts"] = path

        def set_missing_host_key_policy(self, policy):
            observed["policy"] = type(policy).__name__

        def connect(self, **kwargs):
            observed["connect_args"] = kwargs
            if fail_connect:
                raise PermissionError("private-password-detail")
            error_types = {
                "authentication": FakeAuthenticationException,
                "host_key": FakeBadHostKeyException,
                "unknown_host_key": FakeSSHException,
                "unreachable": FakeNoValidConnectionsError,
                "timeout": socket.timeout,
            }
            if connect_error:
                if connect_error == "unknown_host_key":
                    raise error_types[connect_error]("Server 127.0.0.1 not found in known_hosts")
                raise error_types[connect_error]("private connection detail")

        def open_sftp(self):
            return FakeSftp()

        def close(self):
            observed["client_closed"] = True

    monkeypatch.setitem(sys.modules, "paramiko", types.SimpleNamespace(
        SSHClient=FakeClient,
        RejectPolicy=type("RejectPolicy", (), {}),
        SSHException=FakeSSHException,
        AuthenticationException=FakeAuthenticationException,
        BadHostKeyException=FakeBadHostKeyException,
        NoValidConnectionsError=FakeNoValidConnectionsError,
    ))
    return observed


def test_sftp_retrieval_uses_host_key_rejection_allowlist_and_redacts(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("mock-ubuntu.example.invalid ssh-ed25519 fictional-fingerprint", encoding="utf-8")
    observed = _install_fake_paramiko(monkeypatch)
    provider = SftpLogProvider(
        manager,
        host="mock-ubuntu.example.invalid",
        port=22,
        username="demo-user",
        key_filename=key_file,
        known_hosts=known_hosts,
        remote_root="/var/log/loglens-mock",
        allowed_services={"redis-cache"},
    )
    events = provider.retrieve(["redis-cache"], InvestigationRequest(
        application_name="VNSP", error_description="timeout", request_id="SIM-REDIS-0000000001"
    ))
    assert len(events) == 1
    assert events[0].source_label == "authorized SSH/SFTP log source"
    assert events[0].source_file.endswith("data-server-01/redis-cache.jsonl#1")
    assert "do-not-leak" not in events[0].message
    assert observed["policy"] == "RejectPolicy"
    assert observed["path"].endswith("data-server-01/redis-cache.jsonl")
    assert observed["closed"] and observed["client_closed"]


def test_sftp_connection_failures_are_sanitized_and_resources_close(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("fictional-host-key", encoding="utf-8")
    observed = _install_fake_paramiko(monkeypatch, fail_connect=True)
    provider = SftpLogProvider(
        manager, host="mock-ubuntu.example.invalid", port=22, username="demo-user",
        key_filename=key_file, known_hosts=known_hosts, remote_root="/var/log/mock",
        allowed_services={"redis-cache"},
    )
    with pytest.raises(LogSourceError) as error:
        provider.retrieve(["redis-cache"], InvestigationRequest(application_name="VNSP", error_description="timeout"))
    assert "private-password-detail" not in str(error.value)
    assert observed["client_closed"]


@pytest.mark.parametrize(("failure", "message"), [
    ("authentication", "authentication failed"),
    ("host_key", "host-key verification failed"),
    ("unreachable", "connection was refused or unreachable"),
    ("timeout", "connection timed out"),
    ("unknown_host_key", "host-key verification failed"),
])
def test_sftp_connection_failure_reasons_are_specific_and_sanitized(tmp_path, monkeypatch, failure, message):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("fictional-host-key", encoding="utf-8")
    observed = _install_fake_paramiko(monkeypatch, connect_error=failure)
    provider = SftpLogProvider(
        manager, host="127.0.0.1", port=2222, username="demo-user", key_filename=key_file,
        known_hosts=known_hosts, remote_root="/var/log/loglens-mock", allowed_services={"redis-cache"},
    )
    result = provider.health_check()
    assert not result.ok
    assert message in result.message
    assert "private connection detail" not in result.message
    assert not result.host_key_verified
    assert observed["client_closed"]


def test_sftp_health_check_verifies_host_and_readable_remote_log_root(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("fictional-host-key", encoding="utf-8")
    observed = _install_fake_paramiko(monkeypatch)
    provider = SftpLogProvider(
        manager, host="127.0.0.1", port=2222, username="demo-user", key_filename=key_file,
        known_hosts=known_hosts, remote_root="/var/log/loglens-mock", allowed_services={"redis-cache"},
    )
    result = provider.health_check()
    assert result.ok and result.host_key_verified and result.remote_log_root_readable
    assert observed["directory"] == "/var/log/loglens-mock"
    assert observed["closed"] and observed["client_closed"]


def test_sftp_health_check_reports_unreadable_log_root_after_verified_host(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("fictional-host-key", encoding="utf-8")
    _install_fake_paramiko(monkeypatch, directory_denied=True)
    provider = SftpLogProvider(
        manager, host="127.0.0.1", port=2222, username="demo-user", key_filename=key_file,
        known_hosts=known_hosts, remote_root="/var/log/loglens-mock", allowed_services={"redis-cache"},
    )
    result = provider.health_check()
    assert not result.ok
    assert result.host_key_verified
    assert not result.remote_log_root_readable
    assert "lacks permission to list" in result.message


def test_sftp_health_check_identifies_closed_sftp_subsystem_after_verified_ssh(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("fictional-host-key", encoding="utf-8")
    _install_fake_paramiko(monkeypatch, directory_closed=True)
    provider = SftpLogProvider(
        manager, host="127.0.0.1", port=2222, username="demo-user", key_filename=key_file,
        known_hosts=known_hosts, remote_root="~/loglens/mock_logs", allowed_services={"redis-cache"},
    )

    result = provider.health_check()

    assert not result.ok
    assert result.host_key_verified
    assert not result.remote_log_root_readable
    assert "Subsystem sftp internal-sftp" in result.message


def test_sftp_missing_allowlisted_log_file_returns_no_fabricated_events(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("fictional-host-key", encoding="utf-8")
    observed = _install_fake_paramiko(monkeypatch, missing_file=True)
    provider = SftpLogProvider(
        manager, host="127.0.0.1", port=2222, username="demo-user", key_filename=key_file,
        known_hosts=known_hosts, remote_root="/var/log/loglens-mock", allowed_services={"redis-cache"},
    )
    events = provider.retrieve(["redis-cache"], InvestigationRequest(application_name="VNSP", error_description="timeout"))
    assert events == []
    assert observed["closed"] and observed["client_closed"]


def test_sftp_read_timeout_is_reported_and_resources_close(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    key_file = tmp_path / "demo-key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("fictional-key-placeholder", encoding="utf-8")
    known_hosts.write_text("fictional-host-key", encoding="utf-8")
    observed = _install_fake_paramiko(monkeypatch, read_timeout=True)
    provider = SftpLogProvider(
        manager, host="127.0.0.1", port=2222, username="demo-user", key_filename=key_file,
        known_hosts=known_hosts, remote_root="/var/log/loglens-mock", allowed_services={"redis-cache"},
    )
    with pytest.raises(LogSourceError, match="SFTP log retrieval timed out"):
        provider.retrieve(["redis-cache"], InvestigationRequest(application_name="VNSP", error_description="timeout"))
    assert observed["closed"] and observed["client_closed"]


def test_ssh_source_is_disabled_until_config_is_explicitly_authorized(monkeypatch):
    monkeypatch.setattr(provider_module, "LOCAL_SOURCE_CONFIG", Path("missing-local-log-source.json"))
    monkeypatch.setenv("VNSP_LOG_SOURCE", "sftp")
    with pytest.raises(LogSourceError, match="disabled or not marked authorized"):
        create_log_provider(InfrastructureManager())


def test_environment_endpoint_must_match_the_local_ssh_allowlist(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    local_config = tmp_path / "log_source.local.json"
    local_config.write_text(json.dumps({
        "mode": "sftp",
        "ssh": {
            "enabled": True,
            "authorized": True,
            "host": "127.0.0.1",
            "port": 2222,
            "remote_root": "/var/log/loglens-mock",
            "auth_method": "private_key",
            "allowed_services": ["redis-cache"],
        },
    }), encoding="utf-8")
    private_key = tmp_path / "demo-key"
    host_key_file = tmp_path / "known_hosts"
    private_key.write_text("fictional-key-placeholder", encoding="utf-8")
    host_key_file.write_text("fictional-host-key", encoding="utf-8")
    monkeypatch.setattr(provider_module, "LOCAL_SOURCE_CONFIG", local_config)
    monkeypatch.setenv("LOGLENS_SSH_HOST", "127.0.0.1")
    monkeypatch.setenv("LOGLENS_SSH_PORT", "2222")
    monkeypatch.setenv("LOGLENS_SSH_USER", "demo-user")
    monkeypatch.setenv("LOGLENS_SSH_KEY", str(private_key))
    monkeypatch.setenv("LOGLENS_SSH_HOST_KEY_FILE", str(host_key_file))
    monkeypatch.setenv("LOGLENS_REMOTE_LOG_ROOT", "/var/log/loglens-mock")
    provider = SftpLogProvider.from_environment(manager)
    assert provider.host == "127.0.0.1"
    assert provider.port == 2222

    monkeypatch.setenv("LOGLENS_SSH_HOST", "attacker.example.invalid")
    with pytest.raises(LogSourceError, match="does not match the explicitly configured mock endpoint"):
        SftpLogProvider.from_environment(manager)
