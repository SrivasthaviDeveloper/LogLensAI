"""Publish generated mock logs to the explicitly authorized SFTP endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure import InfrastructureManager  # noqa: E402
from log_providers import LogSourceError, SftpLogProvider  # noqa: E402
from scripts.mock_dataset import DEFAULT_OUTPUT  # noqa: E402


def publish_manifest(
    manifest_path: Path = DEFAULT_OUTPUT / "upload_manifest.json",
    *,
    provider_factory: Callable[[], SftpLogProvider] | None = None,
) -> tuple[dict, bool]:
    """Publish files recorded in a manifest and persist each upload outcome."""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["publish_attempted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries = manifest.get("files", [])
    try:
        provider = provider_factory() if provider_factory else SftpLogProvider.from_environment(InfrastructureManager())
        results = provider.upload_generated_logs(entries)
    except (LogSourceError, OSError, ValueError, KeyError, TypeError) as exc:
        safe_message = str(exc) if isinstance(exc, LogSourceError) else "Local manifest or SFTP configuration could not be read."
        for entry in entries:
            entry["upload_status"] = "failed"
            entry["upload_message"] = safe_message
        manifest["publish_status"] = "failed"
        manifest["publish_message"] = safe_message
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest, False

    by_path = {result.relative_path: result for result in results}
    for entry in entries:
        result = by_path.get(entry.get("relative_path"))
        if result is None:
            entry["upload_status"] = "failed"
            entry["upload_message"] = "No SFTP result was returned for this generated file."
        else:
            entry["upload_status"] = result.status
            entry["upload_message"] = result.message
            entry["uploaded_sha256"] = result.sha256 if result.status in {"uploaded", "unchanged"} else None
    statuses = {entry.get("upload_status") for entry in entries}
    ok = bool(entries) and statuses.issubset({"uploaded", "unchanged"})
    if ok:
        manifest["publish_status"] = "uploaded" if "uploaded" in statuses else "unchanged"
        manifest["publish_message"] = "All generated files are present and checksum-verified on the mock SFTP endpoint."
    elif any(status in {"uploaded", "unchanged"} for status in statuses):
        manifest["publish_status"] = "partial_failure"
        manifest["publish_message"] = "Some files were published; review per-file status before retrying."
    else:
        manifest["publish_status"] = "failed"
        manifest["publish_message"] = "No generated files were published; review per-file status."
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest, ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT / "upload_manifest.json")
    args = parser.parse_args()
    try:
        manifest, ok = publish_manifest(args.manifest)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Publishing failed: {type(exc).__name__}; generate the data and manifest first.")
        return 1
    print(f"Publish status: {manifest.get('publish_status', 'failed')}")
    print(manifest.get("publish_message", "No publishing result was recorded."))
    for entry in manifest.get("files", []):
        print(f"{entry.get('upload_status')}: {entry.get('relative_path')} — {entry.get('upload_message')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
