"""CLI: generate the synthetic dataset and the sample API inputs.

Run:  python -m scripts.generate_dataset
Reads generation settings from params.yaml (section `data`) so DVC can track them.
Writes data/raw/healthcare_readmission.csv, data/sample_input.json and
data/sample_batch_input.csv.
"""

import json
from pathlib import Path

import yaml

from src.data.generate import SAMPLE_INPUT, generate_dataset, generate_sample_batch
from src.features.schema import TARGET


def main() -> None:
    params = yaml.safe_load(Path("params.yaml").read_text())["data"]
    paths = yaml.safe_load(Path("configs/config.yaml").read_text())["paths"]

    df = generate_dataset(
        n_rows=params["n_rows"],
        seed=params["seed"],
        positive_rate=params["positive_rate"],
        missing_rate=params["missing_rate"],
        outlier_rate=params["outlier_rate"],
    )
    raw_path = Path(paths["raw_data"])
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(raw_path, index=False)

    Path(paths["sample_input"]).write_text(json.dumps(SAMPLE_INPUT, indent=2) + "\n")
    generate_sample_batch().to_csv(paths["sample_batch"], index=False)

    missing = df.isna().mean()
    print(f"Wrote {raw_path} shape={df.shape} positive_rate={df[TARGET].mean():.3f}")
    print("Missing fraction (non-zero columns):", missing[missing > 0].round(3).to_dict())
    print(f"Wrote {paths['sample_input']} and {paths['sample_batch']}")


if __name__ == "__main__":
    main()
