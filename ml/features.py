"""Text normalization and TF-IDF feature construction."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def normalize_text(value: str) -> list[str]:
    """Normalize text consistently for training and inference."""

    return TOKEN_PATTERN.findall(value.casefold())


def document_for_row(row: dict[str, str]) -> str:
    # Repeat categorical fields to give app/category/service identity modest weight.
    return " ".join((
        row["application_name"], row["application_name"], row["environment"],
        row["error_message"], row["error_message"], row["error_code"],
        row["service_name"], row["service_name"], row["historical_root_cause"],
        row["resolution"], row["request_pattern"], row["response_pattern"],
        row["error_category"],
    ))


def build_artifact(rows: list[dict[str, str]], model_version: str) -> dict[str, Any]:
    """Create a JSON-compatible TF-IDF vectorizer plus example index."""

    tokenized = [normalize_text(document_for_row(row)) for row in rows]
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized:
        document_frequency.update(set(tokens))
    idf = {
        token: round(math.log(1 + len(rows) / (1 + frequency)) + 1, 10)
        for token, frequency in document_frequency.items()
    }
    return {
        "model_version": model_version,
        "training_data_label": "fictional demonstration incidents; not production history",
        "training_rows": len(rows),
        "idf": idf,
        "examples": [
            {"tokens": tokens, **row}
            for tokens, row in zip(tokenized, rows, strict=True)
        ],
    }


def vectorize(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(token for token in tokens if token in idf)
    return {token: (1 + math.log(count)) * idf[token] for token, count in counts.items()}


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return sum(value * right.get(token, 0.0) for token, value in left.items()) / (left_norm * right_norm)
