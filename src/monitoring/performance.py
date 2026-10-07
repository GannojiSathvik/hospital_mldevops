"""Concept-drift check: re-measure model quality once true outcomes are known.

Data drift (see drift.py) looks only at the inputs. *Concept drift* is when the
relationship between inputs and the outcome changes - the same patient profile
now has a different chance of readmission (e.g. a new discharge programme). The
inputs may look identical, so the only way to see it is to wait until the real
outcome (readmitted within 30 days: yes/no) is known for recent patients and
re-score the model on them. If ROC-AUC falls below the approved minimum
(configs/monitoring_config.yaml -> retraining.roc_auc_min), we retrain.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, recall_score, roc_auc_score

from src.features.schema import FEATURE_COLUMNS, TARGET


def evaluate_with_labels(model, df_with_target: pd.DataFrame, threshold: float = 0.5) -> dict:
    """Score `model` on labelled production data.

    Returns {"roc_auc", "pr_auc", "recall", "n_rows", "positive_rate"}.
    - ROC-AUC: how well the model ranks readmitted above non-readmitted patients
      (0.5 = coin flip, 1.0 = perfect).
    - PR-AUC: average precision; more informative when positives are rare.
    - Recall: share of truly readmitted patients the model flagged (at `threshold`).
    roc_auc / pr_auc are None if only one class is present (undefined).
    """
    y_true = df_with_target[TARGET].astype(int).to_numpy()
    probs = model.predict_proba(df_with_target[FEATURE_COLUMNS])[:, 1]
    preds = (probs >= threshold).astype(int)

    both_classes = len(np.unique(y_true)) == 2
    return {
        "roc_auc": round(float(roc_auc_score(y_true, probs)), 4) if both_classes else None,
        "pr_auc": round(float(average_precision_score(y_true, probs)), 4) if both_classes else None,
        "recall": round(float(recall_score(y_true, preds, zero_division=0)), 4),
        "n_rows": int(len(y_true)),
        "positive_rate": round(float(y_true.mean()), 4) if len(y_true) else None,
    }
