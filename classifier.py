"""Public compatibility exports for the local TF-IDF training and inference modules."""

from ml.errors import ClassifierError
from ml.evaluation import evaluate_model
from ml.features import normalize_text
from ml.model import TfidfIncidentClassifier
from ml.training import DEFAULT_ARTIFACT, DEFAULT_DATASET, MODEL_VERSION, train_model

__all__ = [
    "ClassifierError",
    "DEFAULT_ARTIFACT",
    "DEFAULT_DATASET",
    "MODEL_VERSION",
    "TfidfIncidentClassifier",
    "evaluate_model",
    "normalize_text",
    "train_model",
]
