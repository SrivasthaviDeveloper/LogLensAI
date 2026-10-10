"""Manually verify the configured mock Ubuntu SSH/SFTP endpoint."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infrastructure import InfrastructureManager  # noqa: E402
from log_providers import LogSourceError, SftpLogProvider  # noqa: E402


def main() -> int:
    try:
        provider = SftpLogProvider.from_environment(InfrastructureManager())
        result = provider.health_check()
    except LogSourceError as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, indent=2))
        return 1

    print(json.dumps(asdict(result), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
