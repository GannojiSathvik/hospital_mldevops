"""CLI: validate raw data and write stratified train/test splits.

Run:  python -m scripts.preprocess
Uses params.yaml `split` section (test_size, random_state) and paths from configs/config.yaml.
"""

from pathlib import Path

import yaml

from src.data.preprocess import split_dataset
from src.features.schema import TARGET


def main() -> None:
    params = yaml.safe_load(Path("params.yaml").read_text())["split"]
    paths = yaml.safe_load(Path("configs/config.yaml").read_text())["paths"]
    train_df, test_df = split_dataset(
        paths["raw_data"],
        paths["train_data"],
        paths["test_data"],
        test_size=params["test_size"],
        random_state=params["random_state"],
    )
    print(
        f"train={train_df.shape} (pos rate {train_df[TARGET].mean():.3f}) -> {paths['train_data']}"
    )
    print(f"test={test_df.shape} (pos rate {test_df[TARGET].mean():.3f}) -> {paths['test_data']}")


if __name__ == "__main__":
    main()
