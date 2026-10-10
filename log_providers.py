"""Allowlisted local-mock and optional verified SFTP log providers."""

from __future__ import annotations

import json
import hashlib
import errno
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Protocol
from dataclasses import dataclass

from infrastructure import InfrastructureManager
from models import IncidentLogEvent, InvestigationRequest, LogLevel
from security import redact_sensitive_text


MAX_LOG_FILE_BYTES = 5 * 1024 * 1024
MAX_LOG_RECORDS = 2000
DEFAULT_SOURCE_CONFIG = Path(__file__).resolve().parent / "config" / "log_source.json"
LOCAL_SOURCE_CONFIG = Path(__file__).resolve().parent / "config" / "log_source.local.json"
PLAIN_LOG = re.compile(r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^ ]+)\s+(?P<level>DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\s+(?P<message>.*)$", re.IGNORECASE)


class LogSourceError(RuntimeError):
    """A safe log provider failure without credentials or remote payloads."""


@dataclass(frozen=True)
class SshHealthCheckResult:
    """Sanitized result from the manual SSH and remote-directory check."""

    ok: bool
    host: str
    port: int
    host_key_verified: bool
    remote_log_root_readable: bool
    message: str


@dataclass(frozen=True)
class SftpUploadResult:
    """Per-file publication outcome for generated mock logs."""

    relative_path: str
    sha256: str
    status: str
    message: str


def _source_config() -> dict:
    """Read ignored local endpoint settings when present, otherwise the safe template."""

    path = LOCAL_SOURCE_CONFIG if LOCAL_SOURCE_CONFIG.is_file() else DEFAULT_SOURCE_CONFIG
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LogSourceError("Local log-source configuration is invalid or unavailable.") from exc
    if not isinstance(value, dict):
        raise LogSourceError("Local log-source configuration is invalid or unavailable.")
    return value


class LogProvider(Protocol):
    source_label: str

    def retrieve(
        self,
        service_names: list[str],
        request: InvestigationRequest,
        *,
        on_connected: Callable[[str], None] | None = None,
    ) -> list[IncidentLogEvent]: ...


class LocalMockLogProvider:
    """Read only catalog-known JSONL files from the local simulation directory."""

    source_label = "local mock logs (fictional simulation data)"

    def __init__(self, manager: InfrastructureManager, *, max_records: int = MAX_LOG_RECORDS) -> None:
        self.manager = manager
        self.max_records = max_records

    def retrieve(
        self,
        service_names: list[str],
        request: InvestigationRequest,
        *,
        on_connected: Callable[[str], None] | None = None,
    ) -> list[IncidentLogEvent]:
        services = list(dict.fromkeys(service_names))
        if not services:
            return []
        if on_connected:
            on_connected("Local mock provider selected; no server connection is being attempted.")
        try:
            if request.request_id:
                records = self.manager.search_logs(
                    request_id=request.request_id,
                    start_time=request.start_time,
                    end_time=request.end_time,
                    environment=request.environment,
                )
                records = [record for record in records if record.service_name in services]
            else:
                records = []
                for service_name in services:
                    records.extend(self.manager.search_logs(
                        service_name=service_name,
                        start_time=request.start_time,
                        end_time=request.end_time,
                        environment=request.environment,
                    ))
        except (OSError, ValueError) as exc:
            raise LogSourceError("The local mock log source could not be read.") from exc
        if request.server_name:
            server = request.server_name.casefold()
            server_ids = {
                item.server_id.casefold()
                for item in self.manager.list_servers()
                if server in {item.server_id.casefold(), item.host.casefold()}
            }
            service_names = {
                service.name
                for service in self.manager.list_services()
                if service.host.casefold() == server
            }
            records = [
                record for record in records
                if record.server_id.casefold() in server_ids or record.service_name in service_names
            ]
        return [self._convert(record) for record in records[: self.max_records]]

    def _convert(self, record) -> IncidentLogEvent:
        return IncidentLogEvent(
            timestamp=record.timestamp,
            application_name=self.manager.config.application_name,
            environment=self.manager.get_service(record.service_name)[1].environment,
            server_id=record.server_id,
            service_name=record.service_name,
            source_label=self.source_label,
            source_file=record.source_file,
            level=record.level,
            request_id=record.request_id,
            trace_id=record.trace_id,
            event_name=record.event_name,
            message=record.message,
            http_method=record.http_method,
            endpoint=record.endpoint,
            http_status_code=record.http_status_code,
            duration_ms=record.duration_ms,
            error_code=record.error_code,
            exception_category=record.exception_category,
        )


class SftpLogProvider:
    """Retrieve only catalog-mapped files using SSH host-key verification."""

    source_label = "authorized SSH/SFTP log source"

    def __init__(
        self,
        manager: InfrastructureManager,
        *,
        host: str,
        port: int,
        username: str,
        key_filename: Path | None,
        known_hosts: Path,
        remote_root: str,
        allowed_services: set[str],
        auth_method: Literal["private_key", "password"] = "private_key",
        password: str | None = None,
        timeout_seconds: float = 5.0,
        max_file_bytes: int = MAX_LOG_FILE_BYTES,
        max_records: int = MAX_LOG_RECORDS,
        generated_log_root: Path | None = None,
    ) -> None:
        if not host.strip():
            raise LogSourceError("SSH host must be configured in the local log-source allowlist.")
        if auth_method not in {"private_key", "password"}:
            raise LogSourceError("SSH authentication method must be 'private_key' or 'password'.")
        if not username or not known_hosts.is_file():
            raise LogSourceError("SSH requires a configured username and trusted known-hosts file.")
        if auth_method == "private_key" and (key_filename is None or not key_filename.is_file()):
            raise LogSourceError("Private-key authentication requires an existing configured key file.")
        if auth_method == "password" and not password:
            raise LogSourceError("Password authentication requires LOGLENS_SSH_PASSWORD in the process environment.")
        root = PurePosixPath(remote_root)
        home_relative = bool(root.parts) and root.parts[0] == "~"
        if (not root.is_absolute() and not home_relative) or ".." in root.parts or "\\" in remote_root:
            raise LogSourceError("SSH log root must be an absolute or home-relative configured directory without parent traversal.")
        self.manager = manager
        self.host = host
        self.port = port
        self.username = username
        self.key_filename = key_filename
        self.auth_method = auth_method
        self._password = password
        self.known_hosts = known_hosts
        self.remote_root = root
        self.allowed_services = allowed_services
        self.timeout_seconds = timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.max_records = max_records
        self.generated_log_root = Path(generated_log_root or (Path(__file__).resolve().parent / "data" / "training" / "mock_logs")).resolve()
        if not 1 <= port <= 65535 or not 0 < timeout_seconds <= 30:
            raise LogSourceError("SSH port or connection timeout is outside the permitted range.")
        if not 1 <= max_file_bytes <= 50 * 1024 * 1024 or not 1 <= max_records <= 10000:
            raise LogSourceError("SSH file-size or record limits are outside the permitted range.")

    @classmethod
    def from_environment(cls, manager: InfrastructureManager) -> "SftpLogProvider":
        try:
            source_config = _source_config()
            ssh_config = source_config["ssh"]
            if not ssh_config.get("enabled") or not ssh_config.get("authorized"):
                raise LogSourceError("SSH log retrieval is disabled or not marked authorized in the local log-source configuration.")
            configured_host = str(ssh_config["host"])
            configured_port = int(ssh_config["port"])
            host = os.getenv("LOGLENS_SSH_HOST", os.getenv("VNSP_SSH_HOST", configured_host))
            port = int(os.getenv("LOGLENS_SSH_PORT", os.getenv("VNSP_SSH_PORT", str(configured_port))))
            # Environment variables may select only the endpoint explicitly approved
            # by the local allowlist; they cannot redirect the application elsewhere.
            if host != configured_host or port != configured_port:
                raise LogSourceError("Requested SSH endpoint does not match the explicitly configured mock endpoint.")
            remote_root = os.getenv("LOGLENS_REMOTE_LOG_ROOT", ssh_config["remote_root"])
            if remote_root != ssh_config["remote_root"]:
                raise LogSourceError("Requested remote log root does not match the configured allowlist.")
            auth_method = os.getenv("LOGLENS_SSH_AUTH_METHOD", ssh_config.get("auth_method", "private_key")).strip().casefold()
            key_value = os.getenv("LOGLENS_SSH_KEY") or os.getenv("VNSP_SSH_KEY_FILE")
            host_keys_value = os.getenv("LOGLENS_SSH_HOST_KEY_FILE") or os.getenv("VNSP_SSH_KNOWN_HOSTS")
            return cls(
                manager,
                host=host,
                port=port,
                username=os.getenv("LOGLENS_SSH_USER") or os.environ.get("VNSP_SSH_USERNAME", ""),
                key_filename=Path(key_value).expanduser() if key_value else None,
                known_hosts=Path(host_keys_value).expanduser() if host_keys_value else Path(),
                remote_root=remote_root,
                allowed_services=set(ssh_config["allowed_services"]),
                auth_method=auth_method,  # type: ignore[arg-type]
                password=os.getenv("LOGLENS_SSH_PASSWORD"),
                timeout_seconds=float(os.getenv("LOGLENS_SSH_TIMEOUT_SECONDS", os.getenv("VNSP_SSH_TIMEOUT_SECONDS", "5"))),
                max_file_bytes=int(os.getenv("LOGLENS_SSH_MAX_FILE_BYTES", str(MAX_LOG_FILE_BYTES))),
                max_records=int(os.getenv("LOGLENS_SSH_MAX_RECORDS", str(MAX_LOG_RECORDS))),
            )
        except LogSourceError:
            raise
        except (KeyError, ValueError, TypeError) as exc:
            raise LogSourceError("SSH log source configuration is incomplete or invalid.") from exc

    def _connect(self) -> tuple[object, object]:
        """Create a host-key-verified SSH/SFTP session with safe failure messages."""
        try:
            import paramiko
        except ImportError as exc:
            raise LogSourceError("Paramiko is required for SSH log retrieval; install project requirements and retry.") from exc

        client = paramiko.SSHClient()
        try:
            client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            auth = {
                "key_filename": str(self.key_filename) if self.auth_method == "private_key" else None,
                "password": self._password if self.auth_method == "password" else None,
                "look_for_keys": False,
                "allow_agent": False,
            }
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                timeout=self.timeout_seconds,
                banner_timeout=self.timeout_seconds,
                auth_timeout=self.timeout_seconds,
                **auth,
            )
            sftp = client.open_sftp()
            channel_getter = getattr(sftp, "get_channel", None)
            if callable(channel_getter):
                channel_getter().settimeout(self.timeout_seconds)
            return client, sftp
        except paramiko.BadHostKeyException as exc:
            self._close_session(None, client)
            raise LogSourceError("SSH host-key verification failed: the server key does not match the trusted known-hosts entry.") from exc
        except paramiko.AuthenticationException as exc:
            self._close_session(None, client)
            raise LogSourceError("SSH authentication failed. Check the configured username and authentication method.") from exc
        except (socket.timeout, TimeoutError) as exc:
            self._close_session(None, client)
            raise LogSourceError("SSH connection timed out before the session was established; check the VM and port forwarding.") from exc
        except paramiko.NoValidConnectionsError as exc:
            self._close_session(None, client)
            raise LogSourceError("SSH connection was refused or unreachable; check that the Ubuntu VM is running and NAT forwarding targets guest port 22.") from exc
        except paramiko.SSHException as exc:
            self._close_session(None, client)
            if "known_hosts" in str(exc).casefold() or "host key" in str(exc).casefold():
                raise LogSourceError("SSH host-key verification failed: the server key is not trusted by the configured known-hosts file.") from exc
            raise LogSourceError("SSH negotiation failed. Verify that OpenSSH Server is running and the trusted host key is current.") from exc
        except OSError as exc:
            self._close_session(None, client)
            raise LogSourceError("SSH connection failed at the configured endpoint. Check the VM, host, and forwarded port.") from exc
        except Exception as exc:
            self._close_session(None, client)
            raise LogSourceError("SSH/SFTP session could not be established; verify the configured connection settings.") from exc

    def health_check(self) -> SshHealthCheckResult:
        """Verify SSH host identity and read access to the configured log root."""
        client = None
        sftp = None
        connected = False
        try:
            client, sftp = self._connect()
            connected = True
            remote_root = self._resolve_remote_root(sftp)
            sftp.listdir(str(remote_root))
            return SshHealthCheckResult(
                ok=True,
                host=self.host,
                port=self.port,
                host_key_verified=True,
                remote_log_root_readable=True,
                message="SSH host key verified; SFTP connected and configured remote log directory is readable.",
            )
        except LogSourceError as exc:
            return SshHealthCheckResult(
                ok=False,
                host=self.host,
                port=self.port,
                host_key_verified=connected,
                remote_log_root_readable=False,
                message=str(exc),
            )
        except EOFError:
            return SshHealthCheckResult(
                ok=False,
                host=self.host,
                port=self.port,
                host_key_verified=connected,
                remote_log_root_readable=False,
                message=(
                    "SSH host key verified, but the SFTP channel closed while opening the log directory. "
                    "Check that Ubuntu has a working 'Subsystem sftp internal-sftp' configuration."
                ),
            )
        except OSError as exc:
            error_number = getattr(exc, "errno", None)
            if error_number == errno.ENOENT:
                message = "SSH host key verified, but the configured remote log directory does not exist for this SSH user."
            elif error_number in {errno.EACCES, errno.EPERM} or isinstance(exc, PermissionError):
                message = "SSH host key verified, but this SSH user lacks permission to list the configured remote log directory."
            else:
                message = "SSH host key verified, but SFTP could not list the configured remote log directory."
            return SshHealthCheckResult(
                ok=False,
                host=self.host,
                port=self.port,
                host_key_verified=connected,
                remote_log_root_readable=False,
                message=message,
            )
        except Exception as exc:
            message = (
                "SSH host key verified, but SFTP could not list the configured remote log directory. "
                "Check the SFTP subsystem, configured path, and read permissions."
                if connected
                else "SSH/SFTP connection did not complete; check the configured endpoint and server logs."
            )
            return SshHealthCheckResult(False, self.host, self.port, connected, False, message)
        finally:
            self._close_session(sftp, client)

    @staticmethod
    def _close_session(sftp, client) -> None:
        """Close both transports even if one close operation raises."""
        try:
            if sftp is not None:
                sftp.close()
        except Exception:
            pass
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass

    def _resolve_remote_root(self, sftp) -> PurePosixPath:
        """Resolve ~/ paths via SFTP's home directory without invoking a shell."""
        if self.remote_root.parts[0] == "~":
            home = PurePosixPath(sftp.normalize("."))
            if not home.is_absolute() or ".." in home.parts:
                raise LogSourceError("SFTP server returned an invalid home-directory path.")
            return home.joinpath(*self.remote_root.parts[1:])
        return self.remote_root

    @staticmethod
    def _is_missing_remote_path(error: OSError) -> bool:
        return isinstance(error, FileNotFoundError) or getattr(error, "errno", None) == 2

    def _ensure_remote_root(self, sftp) -> PurePosixPath:
        """Create only the configured log-root path beneath the user's home."""
        if self.remote_root.parts[0] == "~":
            current = PurePosixPath(sftp.normalize("."))
            if not current.is_absolute() or ".." in current.parts:
                raise LogSourceError("SFTP server returned an invalid home-directory path.")
            parts = self.remote_root.parts[1:]
            for part in parts:
                current = current / part
                self._mkdir_if_missing(sftp, current)
            return current
        root = self.remote_root
        try:
            sftp.stat(str(root.parent))
        except OSError as exc:
            raise LogSourceError("The parent of the configured remote log root is unavailable or unreadable.") from exc
        self._mkdir_if_missing(sftp, root)
        return root

    @staticmethod
    def _mkdir_if_missing(sftp, path: PurePosixPath) -> None:
        try:
            sftp.stat(str(path))
        except OSError as exc:
            if not SftpLogProvider._is_missing_remote_path(exc):
                raise
            sftp.mkdir(str(path))

    def upload_generated_logs(self, entries: list[dict[str, str]]) -> list[SftpUploadResult]:
        """Publish generated APP/service/app.log files without overwriting data.

        Files must be beneath the repository's generated mock-log directory and
        paths must appear in the fictional training service registry.
        """
        project_root = Path(__file__).resolve().parent
        local_log_root = self.generated_log_root
        training_root = local_log_root.parent
        try:
            registry = json.loads((project_root / "config" / "training_service_registry.json").read_text(encoding="utf-8"))
            allowed_paths = {
                f"{item['log_directory']}/app.log"
                for item in registry["services"] if item.get("enabled") is True
            }
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LogSourceError("The fictional training service registry is unavailable or invalid.") from exc

        prepared: list[tuple[str, Path, str, bytes]] = []
        for entry in entries:
            relative = PurePosixPath(entry.get("relative_path", ""))
            if (
                len(relative.parts) != 3
                or relative.parts[0] not in {"VNSP", "ESP", "IPSM"}
                or relative.parts[2] != "app.log"
                or ".." in relative.parts
                or "\\" in entry.get("relative_path", "")
                or str(relative) not in allowed_paths
            ):
                raise LogSourceError("A generated log path is outside the application/service publish allowlist.")
            local_path = (training_root / entry.get("local_path", "")).resolve()
            if not local_path.is_relative_to(local_log_root) or not local_path.is_file():
                raise LogSourceError("A generated log file is missing or outside the local mock-log directory.")
            try:
                payload = local_path.read_bytes()
            except OSError as exc:
                raise LogSourceError("A generated local mock log could not be read.") from exc
            if len(payload) > self.max_file_bytes:
                raise LogSourceError("A generated log file exceeded the configured transfer-size limit.")
            checksum = hashlib.sha256(payload).hexdigest()
            if checksum != entry.get("sha256"):
                raise LogSourceError("A generated log file checksum does not match its upload manifest.")
            prepared.append((str(relative), local_path, checksum, payload))
        if not prepared:
            return []

        client = None
        sftp = None
        results: list[SftpUploadResult] = []
        try:
            client, sftp = self._connect()
            remote_root = self._ensure_remote_root(sftp)
            for relative, _local_path, checksum, payload in prepared:
                target = remote_root.joinpath(*PurePosixPath(relative).parts)
                remote_dir = target.parent
                current = remote_root
                for part in PurePosixPath(relative).parts[:-1]:
                    current = current / part
                    self._mkdir_if_missing(sftp, current)
                try:
                    attrs = sftp.stat(str(target))
                except OSError as exc:
                    if not self._is_missing_remote_path(exc):
                        results.append(SftpUploadResult(relative, checksum, "failed", "Existing remote path could not be inspected."))
                        continue
                    attrs = None
                if attrs is not None:
                    try:
                        existing = sftp.open(str(target), "rb")
                        try:
                            existing_bytes = existing.read(self.max_file_bytes + 1)
                        finally:
                            existing.close()
                    except Exception:
                        results.append(SftpUploadResult(relative, checksum, "failed", "Existing remote file could not be safely checked."))
                        continue
                    existing_hash = hashlib.sha256(existing_bytes).hexdigest()
                    if len(existing_bytes) <= self.max_file_bytes and existing_hash == checksum:
                        results.append(SftpUploadResult(relative, checksum, "unchanged", "Remote file already matches the generated checksum."))
                    else:
                        results.append(SftpUploadResult(relative, checksum, "failed", "Remote file differs; refusing to overwrite existing data."))
                    continue

                temporary = remote_dir / f".app.log.{uuid.uuid4().hex}.tmp"
                try:
                    remote_file = sftp.open(str(temporary), "xb")
                    try:
                        remote_file.write(payload)
                    finally:
                        remote_file.close()
                    uploaded = sftp.open(str(temporary), "rb")
                    try:
                        verification = uploaded.read(self.max_file_bytes + 1)
                    finally:
                        uploaded.close()
                    if hashlib.sha256(verification).hexdigest() != checksum or len(verification) != len(payload):
                        raise LogSourceError("Uploaded mock log did not pass checksum verification.")
                    # Paramiko's standard rename fails if the destination exists;
                    # never use posix_rename, which could overwrite another file.
                    sftp.rename(str(temporary), str(target))
                    results.append(SftpUploadResult(relative, checksum, "uploaded", "Uploaded and verified by SHA-256."))
                except Exception as exc:
                    results.append(SftpUploadResult(relative, checksum, "failed", "Upload failed or destination appeared during transfer; no existing file was overwritten."))
                finally:
                    try:
                        sftp.remove(str(temporary))
                    except Exception:
                        pass
            return results
        except LogSourceError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise LogSourceError("SFTP publication timed out while creating directories or transferring generated logs.") from exc
        except Exception as exc:
            raise LogSourceError("SFTP publication failed for the configured mock endpoint.") from exc
        finally:
            self._close_session(sftp, client)

    def retrieve(
        self,
        service_names: list[str],
        request: InvestigationRequest,
        *,
        on_connected: Callable[[str], None] | None = None,
    ) -> list[IncidentLogEvent]:
        client = None
        sftp = None
        results: list[IncidentLogEvent] = []
        try:
            client, sftp = self._connect()
            remote_root = self._resolve_remote_root(sftp)
            if on_connected:
                on_connected("SSH host key was verified and SFTP session opened; reading allowlisted files.")
            for service_name in dict.fromkeys(service_names):
                if service_name not in self.allowed_services:
                    raise LogSourceError("The requested microservice is not allowlisted for SSH log retrieval.")
                server_id, service = self.manager.get_service(service_name)
                relative = PurePosixPath(service.log_file)
                if relative.is_absolute() or ".." in relative.parts:
                    raise LogSourceError("A configured SFTP log path is outside the permitted service log directory.")
                remote_path = str(remote_root / relative)
                try:
                    remote_file = sftp.open(remote_path, "rb")
                    try:
                        if sftp.stat(remote_path).st_size > self.max_file_bytes:
                            raise LogSourceError("A configured SFTP log file exceeded the retrieval size limit.")
                        content = remote_file.read(self.max_file_bytes + 1)
                        if len(content) > self.max_file_bytes:
                            raise LogSourceError("A configured SFTP log file exceeded the retrieval size limit.")
                    finally:
                        remote_file.close()
                except LogSourceError:
                    raise
                except (socket.timeout, TimeoutError):
                    raise
                except OSError:
                    # A missing file on one allowlisted service is partial evidence, not a fabricated record.
                    continue
                for line_number, line in enumerate(content.decode("utf-8", errors="replace").splitlines(), start=1):
                    event = self._parse_line(line, server_id, service_name, remote_path, line_number)
                    if event is None:
                        continue
                    if request.request_id and request.request_id not in (event.request_id, event.correlation_id):
                        continue
                    if request.start_time and event.timestamp < request.start_time:
                        continue
                    if request.end_time and event.timestamp > request.end_time:
                        continue
                    if request.environment and (event.environment or "").casefold() != request.environment.casefold():
                        continue
                    if request.server_name:
                        requested_server = request.server_name.casefold()
                        server_model = self.manager.get_server(event.server_id)
                        _, service_model = self.manager.get_service(service_name)
                        if requested_server not in {
                            event.server_id.casefold(),
                            server_model.host.casefold(),
                            service_model.host.casefold(),
                        }:
                            continue
                    results.append(event)
                    if len(results) >= self.max_records:
                        return results
            return results
        except LogSourceError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise LogSourceError("SFTP log retrieval timed out while reading an allowlisted log file.") from exc
        except Exception as exc:
            # Paramiko exception text may contain host/path details; keep it out of API/UI responses.
            raise LogSourceError("SFTP log retrieval failed for the configured allowlisted source.") from exc
        finally:
            self._close_session(sftp, client)

    def _parse_line(self, line: str, server_id: str, service_name: str, relative_file: str, line_number: int) -> IncidentLogEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            match = PLAIN_LOG.match(line)
            if not match:
                return None
            try:
                timestamp = datetime.fromisoformat(match.group("timestamp").replace("Z", "+00:00"))
            except ValueError:
                return None
            item = {"timestamp": timestamp, "level": match.group("level"), "message": match.group("message")}
        try:
            timestamp = item.get("timestamp")
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                return None
            level_name = str(item.get("level", "INFO")).upper()
            level_name = "WARNING" if level_name == "WARN" else level_name
            level = LogLevel(level_name)
            configured_server_id, _ = self.manager.get_service(service_name)
            if configured_server_id != server_id:
                return None
            return IncidentLogEvent(
                timestamp=timestamp.astimezone(timezone.utc),
                application_name=self.manager.config.application_name,
                environment=item.get("environment", "simulation"),
                server_id=server_id,
                service_name=service_name,
                source_label=self.source_label,
                source_file=f"{relative_file}#{line_number}",
                level=level,
                request_id=item.get("request_id"),
                correlation_id=item.get("correlation_id"),
                trace_id=item.get("trace_id"),
                event_name=item.get("event_name"),
                message=redact_sensitive_text(str(item.get("message", "")))[:2000],
                http_method=item.get("http_method"),
                endpoint=item.get("endpoint"),
                http_status_code=item.get("http_status_code"),
                duration_ms=item.get("duration_ms"),
                error_code=item.get("error_code"),
                exception_category=item.get("exception_category"),
            )
        except (ValueError, TypeError):
            return None


def create_log_provider(manager: InfrastructureManager) -> LogProvider:
    """Select an explicitly configured provider; mock logs are the safe default."""

    configured = os.getenv("LOGLENS_LOG_SOURCE", os.getenv("VNSP_LOG_SOURCE"))
    if configured is None:
        try:
            configured = _source_config().get("mode", "mock")
        except LogSourceError:
            configured = "mock"
    provider = configured.strip().casefold()
    if provider == "mock":
        return LocalMockLogProvider(manager)
    if provider == "sftp":
        return SftpLogProvider.from_environment(manager)
    raise LogSourceError("VNSP_LOG_SOURCE must be either 'mock' or 'sftp'.")
