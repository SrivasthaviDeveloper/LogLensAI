"""Generate the fictional three-application workbook and JSONL log fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_dataset import DEFAULT_OUTPUT, generate_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT, help="Output directory (default: data/training).")
    args = parser.parse_args()
    manifest = generate_dataset(args.output_root)
    summary = {
        "workbook": str((args.output_root / manifest["workbook"]).resolve()),
        "scenario_count": manifest["scenario_count"],
        "scenario_count_by_application": manifest["scenario_count_by_application"],
        "event_count": manifest["event_count"],
        "generated_log_files": len(manifest["files"]),
        "manifest": str((args.output_root / "upload_manifest.json").resolve()),
        "data_label": manifest["data_label"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
