"""Model artifact loading and TF-IDF nearest-example inference."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from models import InvestigationRequest
from ml.errors import ClassifierError
from ml.features import cosine, normalize_text, vectorize
from ml.training import DEFAULT_ARTIFACT, DEFAULT_DATASET, MODEL_VERSION, train_model


MIN_CONFIDENCE = 0.12


class TfidfIncidentClassifier:
    """Small deterministic TF-IDF nearest-example model with explicit threshold."""

    def __init__(self, artifact: dict[str, Any], *, confidence_threshold: float | None = None) -> None:
        if artifact.get("model_version") != MODEL_VERSION or not isinstance(artifact.get("examples"), list):
            raise ClassifierError("Model artifact is invalid or uses an unsupported version.")
        if confidence_threshold is None:
            try:
                confidence_threshold = float(os.getenv("VNSP_CLASSIFIER_MIN_CONFIDENCE", str(MIN_CONFIDENCE)))
            except ValueError as exc:
                raise ClassifierError("Classifier confidence threshold is invalid.") from exc
        if not 0 <= confidence_threshold <= 1:
            raise ClassifierError("Classifier confidence threshold must be between 0 and 1.")
        self.artifact = artifact
        self.confidence_threshold = confidence_threshold
        self.idf: dict[str, float] = artifact["idf"]
        self.examples: list[dict[str, Any]] = artifact["examples"]

    @classmethod
    def load(cls, artifact_path: Path = DEFAULT_ARTIFACT, *, confidence_threshold: float | None = None) -> "TfidfIncidentClassifier":
        try:
            artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClassifierError("A trained model artifact is not available.") from exc
        return cls(artifact, confidence_threshold=confidence_threshold)

    @classmethod
    def load_or_train(
        cls,
        dataset_path: Path = DEFAULT_DATASET,
        artifact_path: Path = DEFAULT_ARTIFACT,
        *,
        confidence_threshold: float | None = None,
    ) -> "TfidfIncidentClassifier":
        try:
            return cls.load(artifact_path, confidence_threshold=confidence_threshold)
        except ClassifierError:
            train_model(dataset_path, artifact_path)
            return cls.load(artifact_path, confidence_threshold=confidence_threshold)

    def classify(self, request: InvestigationRequest, *, limit: int = 5) -> dict[str, Any]:
        app_names = {example["application_name"].casefold() for example in self.examples}
        if request.application_name.casefold() not in app_names:
            return self._unknown("Application is not represented in the fictional training set.")

        query = " ".join(filter(None, (
            request.application_name,
            request.action_name or "",
            request.error_description,
            request.error_code or "",
            request.additional_context or "",
        )))
        vector = vectorize(normalize_text(query), self.idf)
        scored: list[tuple[float, dict[str, Any]]] = []
        for example in self.examples:
            score = cosine(vector, vectorize(example["tokens"], self.idf))
            scored.append((score, example))
        scored.sort(key=lambda item: item[0], reverse=True)
        confidence, best = scored[0]
        if confidence < self.confidence_threshold:
            return self._unknown("No fictional training example was sufficiently similar to this input.", confidence)

        ranked: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for score, example in scored:
            key = (example["service_name"], example["error_category"])
            if key in seen:
                continue
            seen.add(key)
            ranked.append({
                "service_name": example["service_name"],
                "server_id": example["server_id"],
                "category": example["error_category"],
                "score": round(score, 4),
                "historical_root_cause": example["historical_root_cause"],
                "resolution": example["resolution"],
            })
            if len(ranked) >= limit:
                break
        status = "insufficient_evidence" if best["error_category"] == "insufficient_evidence" else "classified"
        return {
            "status": status,
            "model_version": self.artifact["model_version"],
            "category": best["error_category"],
            "confidence": round(confidence, 4),
            "candidates": ranked,
            "training_data_label": self.artifact["training_data_label"],
        }

    def _unknown(self, reason: str, confidence: float = 0.0) -> dict[str, Any]:
        return {
            "status": "insufficient_evidence",
            "model_version": self.artifact["model_version"],
            "category": "unknown",
            "confidence": round(confidence, 4),
            "candidates": [],
            "training_data_label": self.artifact["training_data_label"],
            "reason": reason,
        }
