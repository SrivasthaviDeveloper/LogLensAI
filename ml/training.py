"""Read the documented synthetic CSV and persist a local TF-IDF artifact."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ml.errors import ClassifierError
from ml.features import build_artifact


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "data" / "training_incidents.csv"
DEFAULT_ARTIFACT = ROOT / "data" / "models" / "vnsp_tfidf.json"
MODEL_VERSION = "vnsp-tfidf-cosine-v1"
REQUIRED_COLUMNS = {
    "application_name", "error_message", "error_code", "service_name", "server_id",
    "environment", "historical_root_cause", "resolution", "request_pattern",
    "response_pattern", "error_category",
}


def read_examples(dataset_path: Path = DEFAULT_DATASET) -> list[dict[str, str]]:
    try:
        with Path(dataset_path).open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                raise ClassifierError("Training CSV is missing one or more documented columns.")
            rows = [
                {key: (value or "").strip() for key, value in row.items() if key is not None}
                for row in reader
            ]
    except OSError as exc:
        raise ClassifierError("Training data could not be read.") from exc
    if not rows or any(not all(row.get(key) for key in REQUIRED_COLUMNS) for row in rows):
        raise ClassifierError("Training data must contain non-empty values for each required field.")
    return rows


def artifact_for_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    return build_artifact(rows, MODEL_VERSION)


def train_model(
    dataset_path: Path = DEFAULT_DATASET,
    artifact_path: Path = DEFAULT_ARTIFACT,
) -> dict[str, Any]:
    """Train and persist a JSON TF-IDF artifact from the documented CSV schema."""

    artifact = artifact_for_rows(read_examples(dataset_path))
    destination = Path(artifact_path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    except OSError as exc:
        raise ClassifierError("Trained model artifact could not be written.") from exc
    return artifact
