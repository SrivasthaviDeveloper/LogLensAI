"""FastAPI tests for queued investigations and their status/report resources."""

import asyncio
import time

import httpx
import pytest

import main
from classifier import TfidfIncidentClassifier
from infrastructure import InfrastructureManager
from models import InvestigationLifecycleStatus, SimulationScenarioName
from orchestration import InvestigationJobStore, InvestigationOrchestrator
from simulation import generate_scenario


class AsyncInProcessClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def _send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(_send())

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


@pytest.fixture
def job_client(tmp_path, monkeypatch):
    manager = InfrastructureManager(data_root=tmp_path / "mock_logs")
    monkeypatch.setattr(main, "infrastructure_manager", manager)
    store = InvestigationJobStore(InvestigationOrchestrator(manager, TfidfIncidentClassifier.load()), workers=1)
    monkeypatch.setattr(main, "investigation_jobs", store)
    yield AsyncInProcessClient(main.app), manager, store
    store.executor.shutdown(wait=True)


def _wait_for_terminal(client, investigation_id):
    deadline = time.monotonic() + 5
    status = client.get(f"/investigations/{investigation_id}")
    while status.json()["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.01)
        status = client.get(f"/investigations/{investigation_id}")
    return status


def test_job_submission_status_and_evidence_report(job_client):
    client, manager, _store = job_client
    simulation = generate_scenario(SimulationScenarioName.REDIS_TIMEOUT, manager, seed=846)
    submitted = client.post("/investigations", json={
        "application_name": "VNSP",
        "action_name": "GetCircuitStatus",
        "error_description": "Redis operation timeout",
        "error_code": "REDIS_OPERATION_TIMEOUT",
        "request_id": simulation.request_id,
    })
    assert submitted.status_code == 202
    initial = submitted.json()
    assert initial["investigation_id"]
    assert initial["status"] in {"queued", "running", "completed"}
    assert client.get(f"/investigations/{initial['investigation_id']}/report").status_code in {200, 409}

    final_status = _wait_for_terminal(client, initial["investigation_id"])
    assert final_status.status_code == 200
    assert final_status.json()["status"] == "completed"
    assert final_status.json()["progress"] == 100
    result = client.get(f"/investigations/{initial['investigation_id']}/report")
    assert result.status_code == 200
    report = result.json()
    assert report["request_id"] == simulation.request_id
    assert report["error_category"] == "request_timeout"
    assert report["source_label"].startswith("local mock logs")
    assert report["confirmed_components"]
    assert all(event["source_file"].endswith(".jsonl") for event in report["evidence"])


def test_job_input_validation_and_missing_investigation(job_client):
    client, _manager, _store = job_client
    assert client.post("/investigations", json={"application_name": "VNSP", "error_description": "  "}).status_code == 422
    assert client.post("/investigations", json={"application_name": " ", "error_description": "timeout"}).status_code == 422
    assert client.get("/investigations/does-not-exist").status_code == 404
    assert client.get("/investigations/does-not-exist/report").status_code == 404
    invalid = client.post("/investigations", json={
        "application_name": [], "error_description": "password=do-not-echo-this"
    })
    assert invalid.status_code == 422
    assert "do-not-echo-this" not in invalid.text
    assert "input" not in invalid.text.casefold()


def test_partial_result_when_id_is_not_found(job_client):
    client, _manager, _store = job_client
    submitted = client.post("/investigations", json={
        "application_name": "VNSP", "error_description": "Redis timeout", "request_id": "SIM-REDIS-MISSING"
    })
    investigation_id = submitted.json()["investigation_id"]
    status = _wait_for_terminal(client, investigation_id)
    assert status.json()["status"] == InvestigationLifecycleStatus.PARTIAL_SUCCESS.value
    report = client.get(f"/investigations/{investigation_id}/report").json()
    assert report["evidence"] == []
    assert report["root_cause_hypothesis"] is None
    assert "SIM-REDIS-MISSING" in " ".join(report["evidence_gaps"])
