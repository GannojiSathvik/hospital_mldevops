"""CLI: evaluate the saved model on the held-out test set (DVC stage `evaluate`).

Run:  python -m scripts.evaluate
Reloads models/readmission_model.joblib + threshold.json (exactly what the API
uses), writes reports/evaluation/metrics.json, classification_report.txt and
the confusion/ROC/PR/calibration figures.
"""

import json

from src.models.evaluate import evaluate_saved_model


def main() -> None:
    metrics = evaluate_saved_model()
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
