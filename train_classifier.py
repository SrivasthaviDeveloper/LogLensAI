"""Train and persist the local fictional-incident TF-IDF classifier."""

import argparse
from pathlib import Path

from classifier import DEFAULT_ARTIFACT, DEFAULT_DATASET, evaluate_model, train_model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()
    model = train_model(args.dataset, args.output)
    print(f"Trained {model['model_version']} from {model['training_rows']} fictional examples.")
    print(f"Training data: {model['training_data_label']}")
    print(f"Artifact: {args.output}")
    evaluation = evaluate_model(args.dataset)
    print(
        "Leave-one-out category accuracy: "
        f"{evaluation['correct']}/{evaluation['evaluated_examples']} "
        f"({evaluation['accuracy']:.0%})"
    )


if __name__ == "__main__":
    main()
