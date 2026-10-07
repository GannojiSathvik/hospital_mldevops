"""Automated data validation ("data contract" checks) run before training.

Garbage in, garbage out: if a new data delivery has a renamed column, a new
category spelling, impossible blood pressure values or a column that secretly
contains the answer (leakage), training would silently produce a bad model.
`validate_dataframe` catches these problems up front and returns a
ValidationResult:

- errors   -> the data must NOT be used (pipeline stops)
- warnings -> data is usable but a human should know (e.g. class imbalance)

Checks: required columns, schema changes (unexpected columns), dtypes, allowed
categories, missing-value limits, numeric ranges, duplicate patient IDs,
target values/distribution, class imbalance, and data leakage.
Rule thresholds live in configs/validation_config.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from src.features.schema import (
    BINARY_FEATURES,
    CATEGORY_VALUES,
    FEATURE_COLUMNS,
    ID_COLUMN,
    NUMERIC_FEATURES,
    NUMERIC_RANGES,
    TARGET,
)

DEFAULT_CONFIG_PATH = "configs/validation_config.yaml"
DEFAULT_RULES = {
    "min_rows": 100,
    "max_missing_fraction": 0.20,
    "warn_missing_fraction": 0.05,
    "target": {
        "allowed_values": [0, 1],
        "min_positive_rate": 0.02,
        "max_positive_rate": 0.60,
        "imbalance_warning_rate": 0.20,
    },
    "leakage": {
        "max_abs_correlation": 0.95,
        "suspicious_name_patterns": ["readmi", "target", "label", "outcome"],
    },
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    # Friendly alias.
    passed = is_valid

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }

    def summary(self) -> str:
        lines = [
            f"Validation {'PASSED' if self.is_valid else 'FAILED'}: "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        ]
        lines += [f"  ERROR:   {e}" for e in self.errors]
        lines += [f"  WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)


def load_rules(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load validation thresholds; fall back to built-in defaults if the file is missing."""
    p = Path(path)
    if p.exists():
        return yaml.safe_load(p.read_text())
    return DEFAULT_RULES


def validate_dataframe(
    df: pd.DataFrame, require_target: bool = True, rules: dict | None = None
) -> ValidationResult:
    """Run all checks on `df`. Set require_target=False for inference data (no labels)."""
    rules = rules or load_rules()
    res = ValidationResult()
    res.stats["n_rows"] = int(len(df))
    res.stats["n_columns"] = int(df.shape[1])

    # 1. Required columns / schema changes ---------------------------------
    required = FEATURE_COLUMNS + ([TARGET] if require_target else [])
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        res.errors.append(f"Missing required columns: {missing_cols}")
    known = set(FEATURE_COLUMNS) | {ID_COLUMN, TARGET}
    unexpected = [c for c in df.columns if c not in known]
    if unexpected:
        res.warnings.append(
            f"Schema change: unexpected columns {unexpected} (they will be ignored by the model)"
        )

    if len(df) < rules.get("min_rows", 0):
        res.errors.append(f"Too few rows: {len(df)} < {rules['min_rows']}")

    # 2. Missing values -----------------------------------------------------
    present_features = [c for c in FEATURE_COLUMNS if c in df.columns]
    missing_frac = df[present_features].isna().mean()
    res.stats["missing_fraction"] = {
        k: round(float(v), 4) for k, v in missing_frac.items() if v > 0
    }
    for col, frac in missing_frac.items():
        if frac > rules["max_missing_fraction"]:
            res.errors.append(
                f"Column '{col}' has {frac:.1%} missing (limit {rules['max_missing_fraction']:.0%})"
            )
        elif frac > rules["warn_missing_fraction"]:
            res.warnings.append(f"Column '{col}' has {frac:.1%} missing values")

    # 3. Numeric dtypes + plausible ranges ----------------------------------
    for col in NUMERIC_FEATURES:
        if col not in df.columns:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            res.errors.append(f"Column '{col}' should be numeric but has dtype {df[col].dtype}")
            continue
        lo, hi = NUMERIC_RANGES[col]
        out = df[col].notna() & ((df[col] < lo) | (df[col] > hi))
        if out.any():
            res.errors.append(
                f"Column '{col}' has {int(out.sum())} value(s) outside plausible range [{lo}, {hi}]"
            )

    # 4. Binary flags must be 0/1 -------------------------------------------
    for col in BINARY_FEATURES:
        if col not in df.columns:
            continue
        values = df[col].dropna()
        if not pd.api.types.is_numeric_dtype(values) or not values.isin([0, 1]).all():
            bad = sorted(map(str, set(values.unique()) - {0, 1}))[:5]
            res.errors.append(f"Binary column '{col}' contains values other than 0/1: {bad}")

    # 5. Categorical values must come from the allowed list -----------------
    for col, allowed in CATEGORY_VALUES.items():
        if col not in df.columns:
            continue
        values = df[col].dropna().astype(str)
        bad = sorted(set(values.unique()) - set(allowed))
        if bad:
            res.errors.append(
                f"Column '{col}' has unknown categories {bad[:5]} (allowed {allowed})"
            )

    # 6. Duplicate patient IDs ---------------------------------------------
    if ID_COLUMN in df.columns:
        n_dup = int(df[ID_COLUMN].duplicated().sum())
        res.stats["duplicate_ids"] = n_dup
        if n_dup:
            res.errors.append(f"{n_dup} duplicate {ID_COLUMN} value(s)")
    else:
        res.warnings.append(f"No '{ID_COLUMN}' column; cannot check for duplicate patients")

    # 7. Target checks: values, distribution, imbalance, leakage -----------
    if require_target and TARGET in df.columns:
        _check_target(df, res, rules)

    return res


def _check_target(df: pd.DataFrame, res: ValidationResult, rules: dict) -> None:
    t_rules, l_rules = rules["target"], rules["leakage"]
    y = df[TARGET]
    if y.isna().any():
        res.errors.append(f"Target '{TARGET}' has {int(y.isna().sum())} missing value(s)")
    y = y.dropna()
    if not y.isin(t_rules["allowed_values"]).all():
        res.errors.append(f"Target '{TARGET}' must only contain {t_rules['allowed_values']}")
        return
    if y.nunique() < 2:
        res.errors.append("Target has only one class; a classifier cannot be trained")
        return

    pos_rate = float(y.mean())
    res.stats["positive_rate"] = round(pos_rate, 4)
    if not t_rules["min_positive_rate"] <= pos_rate <= t_rules["max_positive_rate"]:
        res.errors.append(
            f"Target positive rate {pos_rate:.1%} outside expected "
            f"[{t_rules['min_positive_rate']:.0%}, {t_rules['max_positive_rate']:.0%}]"
        )
    minority = min(pos_rate, 1 - pos_rate)
    if minority < t_rules["imbalance_warning_rate"]:
        res.warnings.append(
            f"Class imbalance: minority class is {minority:.1%} of rows "
            "(use class weights, PR-AUC and threshold tuning)"
        )

    # Leakage (a) suspicious column names that look like the label.
    for col in df.columns:
        if col in (TARGET, ID_COLUMN):
            continue
        if any(p in col.lower() for p in l_rules["suspicious_name_patterns"]):
            res.errors.append(f"Possible target leakage: column '{col}' looks like the label")

    # Leakage (b) any numeric column almost perfectly correlated with the target.
    numeric_cols = [c for c in df.select_dtypes("number").columns if c != TARGET]
    if numeric_cols:
        corr = df[numeric_cols].corrwith(df[TARGET].astype(float)).abs()
        res.stats["max_abs_target_correlation"] = (
            round(float(corr.max()), 4) if corr.notna().any() else 0.0
        )
        for col, c in corr.items():
            if pd.notna(c) and c > l_rules["max_abs_correlation"]:
                res.errors.append(
                    f"Possible target leakage: '{col}' has |corr|={c:.3f} with the target"
                )
