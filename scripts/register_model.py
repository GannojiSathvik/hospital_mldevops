"""CLI: register the latest trained model in the MLflow Model Registry.

Run:  python -m scripts.register_model
Reads the MLflow model URI saved by training in models/model_metadata.json and
registers it under the name "healthcare-readmission". Each registration
creates a new numbered version, giving an auditable history of which model
(and which training-data hash) was promoted. Requires the `model:register`
permission when ENFORCE_PIPELINE_RBAC=true.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REGISTERED_MODEL_NAME = "healthcare-readmission"


def register_model(
    metadata_path: str = "models/model_metadata.json", name: str = REGISTERED_MODEL_NAME
):
    import mlflow

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
    meta = json.loads(Path(metadata_path).read_text())
    uri = meta.get("mlflow_model_uri")
    if not uri:
        raise RuntimeError(
            "No mlflow_model_uri in metadata; train with MLflow logging enabled first"
        )
    mv = mlflow.register_model(uri, name)
    client = mlflow.MlflowClient()
    client.set_model_version_tag(name, mv.version, "training_data_hash", meta["training_data_hash"])
    client.set_model_version_tag(name, mv.version, "threshold", str(meta["threshold"]))
    client.set_model_version_tag(name, mv.version, "trained_by", meta["trained_by"])
    print(f"Registered '{name}' version {mv.version} from {uri}")
    return mv


def main() -> None:
    if os.getenv("ENFORCE_PIPELINE_RBAC", "true").lower() != "false":
        from src.security.rbac import require_pipeline_permission

        require_pipeline_permission("model:register")
    register_model()


if __name__ == "__main__":
    main()
