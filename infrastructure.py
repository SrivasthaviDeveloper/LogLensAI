"""Local-only manager for fictional servers, services, statuses, and JSONL logs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from models import (
    InfrastructureConfig,
    LogLevel,
    MockServer,
    SimulatedLogRecord,
    SimulatedService,
    SimulatedServiceStatus,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "mock_infrastructure.json"
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "mock_infrastructure"


class InfrastructureError(ValueError):
    """Base class for safe simulated infrastructure errors."""


class UnknownServerError(InfrastructureError):
    """Raised when a server ID is absent from the local catalog."""


class UnknownServiceError(InfrastructureError):
    """Raised when a service name is absent from the local catalog."""


class InfrastructureManager:
    """Read and manage a validated inventory without contacting any hosts."""

    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        data_root: Path = DEFAULT_DATA_ROOT,
    ) -> None:
        self.config_path = Path(config_path).resolve()
        requested_data_root = Path(data_root).absolute()
        if requested_data_root == DEFAULT_DATA_ROOT.absolute():
            resolved_data_root = requested_data_root.resolve()
            if (
                not resolved_data_root.is_relative_to(PROJECT_ROOT)
                or requested_data_root.is_symlink()
            ):
                raise InfrastructureError("The default simulation log directory must remain inside the project.")
            self.data_root = resolved_data_root
        else:
            # Alternate roots are accepted for isolated local tests only; API routes
            # always use the fixed project-local default above.
            self.data_root = requested_data_root.resolve()
        try:
            raw_config = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.config = InfrastructureConfig.model_validate(raw_config)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise InfrastructureError("The mock infrastructure catalog is invalid or unavailable.") from exc

        self._servers: dict[str, MockServer] = {}
        self._services: dict[str, tuple[str, SimulatedService]] = {}
        self._status_overrides: dict[str, SimulatedServiceStatus] = {}
        for server in self.config.servers:
            if server.server_id in self._servers:
                raise InfrastructureError("The mock infrastructure contains a duplicate server ID.")
            self._servers[server.server_id] = server
            if not server.host.endswith(".example.invalid"):
                raise InfrastructureError("Simulated server hosts must use the reserved example.invalid domain.")
            for service in server.services:
                if service.name in self._services:
                    raise InfrastructureError("The mock infrastructure contains a duplicate service name.")
                if not service.host.endswith(".example.invalid"):
                    raise InfrastructureError("Simulated service hosts must use the reserved example.invalid domain.")
                self._validate_log_path(server.server_id, service.log_file)
                self._services[service.name] = (server.server_id, service)

        for _, service in self._services.values():
            if any(dependency not in self._services for dependency in service.dependencies):
                raise InfrastructureError("A service dependency is missing from the mock infrastructure catalog.")

    def list_servers(self) -> list[MockServer]:
        """Return the fictional server inventory with current service statuses."""

        return [self._server_with_status(server) for server in self._servers.values()]

    def get_server(self, server_id: str) -> MockServer:
        try:
            server = self._servers[server_id]
        except KeyError as exc:
            raise UnknownServerError(f"Unknown simulated server: {server_id}.") from exc
        return self._server_with_status(server)

    def list_services(self, server_id: str | None = None) -> list[SimulatedService]:
        """List services, optionally limited to a known server."""

        if server_id is not None:
            self.get_server(server_id)
        return [
            self.get_service(name)[1]
            for name, (owner_server, _) in self._services.items()
            if server_id is None or owner_server == server_id
        ]

    def get_service(self, service_name: str) -> tuple[str, SimulatedService]:
        try:
            server_id, service = self._services[service_name]
        except KeyError as exc:
            raise UnknownServiceError(f"Unknown simulated service: {service_name}.") from exc
        status = self._status_overrides.get(service_name, service.status)
        return server_id, service.model_copy(update={"status": status})

    def set_service_status(self, service_name: str, status: SimulatedServiceStatus) -> SimulatedService:
        """Change a service's in-memory simulation status; no process is stopped."""

        self.get_service(service_name)
        baseline = self._services[service_name][1].status
        if status == baseline:
            self._status_overrides.pop(service_name, None)
        else:
            self._status_overrides[service_name] = status
        return self.get_service(service_name)[1]

    def write_logs(self, records: Iterable[SimulatedLogRecord]) -> list[SimulatedLogRecord]:
        """Append deterministic JSONL records, avoiding duplicate fixture events."""

        grouped: dict[Path, list[SimulatedLogRecord]] = {}
        for record in records:
            _, service = self.get_service(record.service_name)
            if record.server_id != self._services[record.service_name][0]:
                raise InfrastructureError("A simulated log record references a mismatched server and service.")
            log_path = self._resolve_log_path(service.log_file)
            grouped.setdefault(log_path, []).append(record)

        saved: list[SimulatedLogRecord] = []
        for log_path, new_records in grouped.items():
            existing_records = self._read_file(log_path)
            identities = {self._record_identity(item) for item in existing_records}
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", newline="\n") as output:
                for record in new_records:
                    if self._record_identity(record) in identities:
                        continue
                    payload = record.model_dump(mode="json")
                    output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                    identities.add(self._record_identity(record))
                    saved.append(record)
        return saved

    def search_logs(
        self,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        server_id: str | None = None,
        service_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        severity: LogLevel | None = None,
        error_category: str | None = None,
        environment: str | None = None,
    ) -> list[SimulatedLogRecord]:
        """Search only catalog-known generated files and apply optional filters."""

        if server_id is not None:
            self.get_server(server_id)
        if service_name is not None:
            self.get_service(service_name)
        if all(
            value is None
            for value in (request_id, trace_id, server_id, service_name, start_time, end_time, severity, error_category, environment)
        ):
            raise InfrastructureError("Provide at least one ID or filter to search simulated logs.")
        if any(value is not None and value.tzinfo is None for value in (start_time, end_time)):
            raise InfrastructureError("Log search timestamps must include a timezone.")
        if start_time is not None and end_time is not None and end_time < start_time:
            raise InfrastructureError("The log search end time must be after its start time.")

        matches: list[SimulatedLogRecord] = []
        for owner_server, service in self._services.values():
            if server_id is not None and owner_server != server_id:
                continue
            if service_name is not None and service.name != service_name:
                continue
            if environment is not None and service.environment != environment:
                continue
            for record in self._read_file(self._resolve_log_path(service.log_file)):
                if request_id is not None and record.request_id != request_id:
                    continue
                if trace_id is not None and record.trace_id != trace_id:
                    continue
                if start_time is not None and record.timestamp < start_time:
                    continue
                if end_time is not None and record.timestamp > end_time:
                    continue
                if severity is not None and record.level != severity:
                    continue
                if error_category is not None and record.exception_category != error_category:
                    continue
                matches.append(record)
        return sorted(matches, key=lambda item: (item.timestamp, item.server_id, item.service_name))

    def reset_logs(self) -> int:
        """Delete generated JSONL files under the dedicated simulation root only."""

        if not self.data_root.exists():
            return 0
        removed = 0
        for path in self.data_root.rglob("*.jsonl"):
            resolved = path.resolve()
            if not resolved.is_relative_to(self.data_root):
                raise InfrastructureError("A generated log path escaped the simulation data directory.")
            if path.is_file():
                path.unlink()
                removed += 1
        return removed

    def reset_service_statuses(self) -> None:
        """Restore configured baseline service states."""

        self._status_overrides.clear()

    def _server_with_status(self, server: MockServer) -> MockServer:
        services = [self.get_service(service.name)[1] for service in server.services]
        return server.model_copy(update={"services": services})

    def _validate_log_path(self, server_id: str, log_file: str) -> None:
        relative_path = Path(log_file)
        if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
            raise InfrastructureError("Mock log paths must be safe project-relative paths.")
        if relative_path.parts[0] != server_id or relative_path.suffix != ".jsonl":
            raise InfrastructureError("Each service log must be JSONL under its owning simulated server directory.")
        self._resolve_log_path(log_file)

    def _resolve_log_path(self, log_file: str) -> Path:
        path = (self.data_root / log_file).resolve()
        if not path.is_relative_to(self.data_root):
            raise InfrastructureError("A configured mock log path escaped the simulation data directory.")
        return path

    @staticmethod
    def _record_identity(record: SimulatedLogRecord) -> tuple[object, ...]:
        return (
            record.scenario_name,
            record.request_id,
            record.trace_id,
            record.timestamp,
            record.event_name,
        )

    @staticmethod
    def _read_file(path: Path) -> list[SimulatedLogRecord]:
        if not path.exists():
            return []
        records: list[SimulatedLogRecord] = []
        try:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if line.strip():
                    try:
                        records.append(SimulatedLogRecord.model_validate_json(line))
                    except ValidationError as exc:
                        raise InfrastructureError(
                            f"Generated mock log file contains an invalid record at line {line_number}."
                        ) from exc
        except OSError as exc:
            raise InfrastructureError("A generated mock log file could not be read.") from exc
        return records
