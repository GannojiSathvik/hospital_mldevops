"""Lab 6 validation pipeline: RBAC check -> validate the raw dataset -> write a JSON report.

Run:  python -m pipelines.validation_pipeline   (optionally: --data path/to.csv)
Access control: when ENFORCE_PIPELINE_RBAC is "true" (the default) the caller
must present a PIPELINE_API_KEY whose role has the `pipeline:validate`
permission; otherwise PermissionError is raised before any data is touched.
Exit code 1 if validation fails, so CI jobs fail automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from src.data.preprocess import read_patient_csv
from src.data.validation import validate_dataframe


def check_access(permission: str) -> str:
    """Enforce RBAC for CLI pipelines; returns the principal name (or 'local-dev')."""
    if os.getenv("ENFORCE_PIPELINE_RBAC", "true").lower() == "false":
        return "local-dev"
    from src.security.rbac import (
        require_pipeline_permission,  # lazy: module owned by API/security code
    )

    return require_pipeline_permission(permission).name


def run(
    data_path: str | None = None, report_path: str = "reports/evaluation/validation_report.json"
) -> bool:
    principal = check_access("pipeline:validate")
    paths = yaml.safe_load(Path("configs/config.yaml").read_text())["paths"]
    data_path = data_path or paths["raw_data"]

    df = read_patient_csv(data_path)
    result = validate_dataframe(df, require_target=True)
    print(result.summary())

    report = {"data_path": str(data_path), "run_by": principal, **result.to_dict()}
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, indent=2))
    print(f"Validation report written to {report_path}")
    return result.is_valid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=None, help="CSV to validate (default: raw dataset)")
    args = parser.parse_args()
    try:
        ok = run(args.data)
    except PermissionError as exc:
        print(f"ACCESS DENIED: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
