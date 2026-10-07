"""Scheduling example: run monitoring daily and retraining weekly with Prefect.

This file is a reference for how the flows would be scheduled in a real
deployment. It is NOT needed for the lab demo (there you run the flows by hand
or via GitHub Actions cron: .github/workflows/drift-monitoring.yml / retraining.yml).

Prefect vocabulary:
  * flow       - a Python function decorated with @flow; one end-to-end job.
  * deployment - a flow + a schedule + where to run it, registered with a
                 Prefect server so it runs without anyone typing a command.
  * cron       - a schedule string "minute hour day-of-month month day-of-week".

How to use (needs a long-running Prefect server, e.g. `prefect server start`):
    python -m pipelines.orchestration
`serve()` registers both deployments and keeps a worker process alive that
triggers the flows on schedule. Stop with Ctrl+C.

Note: the retraining flow checks RBAC (pipeline:retrain), so the process
running it must have PIPELINE_API_KEY set to an ml_engineer or admin key.
"""

from __future__ import annotations

from prefect import serve

from pipelines.monitoring_pipeline import monitoring_flow
from pipelines.retraining_pipeline import retraining_flow


def main() -> None:
    # Every day at 02:00 UTC: compare yesterday's predictions with training data.
    monitoring_deployment = monitoring_flow.to_deployment(
        name="daily-drift-monitoring",
        cron="0 2 * * *",
        tags=["monitoring"],
    )
    # Every Sunday at 03:00 UTC: retrain *only if* the latest monitoring run
    # decided it was needed (force=False reads reports/drift/retrain_decision.json).
    retraining_deployment = retraining_flow.to_deployment(
        name="weekly-conditional-retraining",
        cron="0 3 * * 0",
        parameters={"force": False},
        tags=["retraining"],
    )
    serve(monitoring_deployment, retraining_deployment)


if __name__ == "__main__":
    main()
