"""CLI: train/compare models and save all artifacts to models/ (DVC stage `train`).

Run:  python -m scripts.train
This is the plain training step without RBAC; the access-controlled entry point
for Lab 6 is `python -m pipelines.training_pipeline`.
"""

from src.models.train import run_training


def main() -> None:
    summary = run_training()
    print(summary["comparison"].drop(columns=["best_params"]).to_string(index=False))


if __name__ == "__main__":
    main()
