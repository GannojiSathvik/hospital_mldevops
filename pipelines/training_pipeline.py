"""Lab 6 training pipeline (access-controlled): RBAC -> validate -> split -> train -> evaluate -> register.

Run:  python -m pipelines.training_pipeline  [--skip-register]
Access control: with ENFORCE_PIPELINE_RBAC=true (default) the PIPELINE_API_KEY
must belong to a role with `pipeline:train` (ml_engineer or admin). A viewer
or clinician key gets PermissionError before any data is read. The principal's
name is stamped into model_metadata.json as `trained_by` (accountability).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from src.data.preprocess import split_dataset
from src.models.evaluate import evaluate_saved_model
from src.models.train import run_training


def check_access(permission: str) -> str:
    """Enforce RBAC for CLI pipelines; returns the principal name (or 'local-dev')."""
    if os.getenv("ENFORCE_PIPELINE_RBAC", "true").lower() == "false":
        return "local-dev"
    from src.security.rbac import require_pipeline_permission  # lazy import (separate module)

    return require_pipeline_permission(permission).name


def run(register: bool = True) -> dict:
    trained_by = check_access("pipeline:train")
    params = yaml.safe_load(Path("params.yaml").read_text())
    paths = yaml.safe_load(Path("configs/config.yaml").read_text())["paths"]

    print("== Step 1/4: validate + stratified split")
    split_dataset(
        paths["raw_data"],
        paths["train_data"],
        paths["test_data"],
        test_size=params["split"]["test_size"],
        random_state=params["split"]["random_state"],
    )

    print("== Step 2/4: train, compare, calibrate, threshold")
    summary = run_training(
        paths["train_data"],
        paths["test_data"],
        paths["models_dir"],
        paths["figures_dir"],
        paths["evaluation_dir"],
        params=params,
        trained_by=trained_by,
    )
    print(summary["comparison"].drop(columns=["best_params"]).to_string(index=False))

    print("== Step 3/4: re-evaluate saved model artifact on test set")
    evaluate_saved_model(
        test_path=paths["test_data"],
        figures_dir=paths["figures_dir"],
        evaluation_dir=paths["evaluation_dir"],
    )

    if register:
        print("== Step 4/4: register model in MLflow registry")
        try:
            from scripts.register_model import register_model

            register_model()
        except Exception as exc:  # registry problems should not destroy a good training run
            print(f"WARNING: model registration failed: {exc}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-register", action="store_true")
    args = parser.parse_args()
    try:
        run(register=not args.skip_register)
    except PermissionError as exc:
        print(f"ACCESS DENIED: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
