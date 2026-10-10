"""Streamlit smoke coverage for the simulation inventory page."""

from pathlib import Path
import tempfile

APP_TEST_TEMP = Path(__file__).parent.parent / "work" / "streamlit-app-test-tmp"
APP_TEST_TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(APP_TEST_TEMP)

from streamlit.testing.v1 import AppTest

from infrastructure import InfrastructureManager
from models import ServerInventoryResponse, ServiceInventoryResponse
import simulation_ui


def test_simulation_page_renders_mock_inventory_without_script_errors(monkeypatch, tmp_path):
    manager = InfrastructureManager(data_root=tmp_path / "mock_infrastructure")

    def fake_api_request(method, path, **kwargs):
        if method == "GET" and path == "/infrastructure/servers":
            return ServerInventoryResponse(servers=manager.list_servers()).model_dump(mode="json")
        if method == "GET" and path == "/infrastructure/services":
            return ServiceInventoryResponse(services=manager.list_services()).model_dump(mode="json")
        raise AssertionError(f"Unexpected page request: {method} {path}")

    monkeypatch.setattr(simulation_ui, "request_simulation_api", fake_api_request)
    page_path = Path(__file__).parent.parent / "pages" / "1_Simulation_Lab.py"
    app = AppTest.from_file(str(page_path)).run()

    assert not app.exception
    assert any("Mock Infrastructure Simulation Lab" in item.value for item in app.title)
    assert any("app-server-01" in item.value for item in app.markdown)
