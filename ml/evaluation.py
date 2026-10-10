"""Deterministic leave-one-out evaluation of synthetic training examples."""

from __future__ import annotations

from pathlib import Path

from models import InvestigationRequest
from ml.model import TfidfIncidentClassifier
from ml.training import DEFAULT_DATASET, artifact_for_rows, read_examples


def evaluate_model(dataset_path: Path = DEFAULT_DATASET) -> dict[str, float | int]:
    """Evaluate top category on each row while excluding that row from training."""

    rows = read_examples(dataset_path)
    if len(rows) < 2:
        from ml.errors import ClassifierError

        raise ClassifierError("At least two examples are required for evaluation.")
    correct = 0
    for index, row in enumerate(rows):
        model = TfidfIncidentClassifier(
            artifact_for_rows(rows[:index] + rows[index + 1 :]), confidence_threshold=0.0
        )
        result = model.classify(InvestigationRequest(
            application_name=row["application_name"],
            error_description=row["error_message"],
            error_code=row["error_code"],
        ))
        correct += int(result["category"] == row["error_category"])
    return {"evaluated_examples": len(rows), "correct": correct, "accuracy": round(correct / len(rows), 4)}
