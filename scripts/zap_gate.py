"""CLI: DAST gate - fail if an OWASP ZAP report contains any HIGH-risk alert.

Run:  python -m scripts.zap_gate <zap_report.json> [--rules .zap/rules.tsv]

ZAP rates every alert with a risk code: 0 = Informational, 1 = Low,
2 = Medium, 3 = High. This gate prints a count per level and exits 1 only when
at least one High alert is present. Lower levels are still in the HTML report
for a human to review; they just do not block the build.

Alerts whose rule ID is marked IGNORE in the rules file (the same
`.zap/rules.tsv` ZAP itself reads) are skipped, so a documented false positive
cannot fail the gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RISK_NAMES = {0: "Informational", 1: "Low", 2: "Medium", 3: "High"}
HIGH = 3


def load_ignored(rules_path: Path | None) -> set[str]:
    """Rule IDs marked IGNORE in a ZAP rules.tsv (``<id>\\t<ACTION>\\t<comment>``)."""
    if rules_path is None or not rules_path.exists():
        return set()
    ignored = set()
    for line in rules_path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip().upper() == "IGNORE":
            ignored.add(parts[0].strip())
    return ignored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="ZAP traditional JSON report")
    parser.add_argument("--rules", type=Path, default=Path(".zap/rules.tsv"))
    args = parser.parse_args()

    report = json.loads(args.report.read_text())
    ignored = load_ignored(args.rules)

    counts = dict.fromkeys(RISK_NAMES.values(), 0)
    highs: list[str] = []
    for site in report.get("site", []):
        for alert in site.get("alerts", []):
            if str(alert.get("pluginid")) in ignored:
                continue
            risk = int(alert.get("riskcode", 0))
            counts[RISK_NAMES.get(risk, "Informational")] += 1
            if risk >= HIGH:
                highs.append(f"  [{alert.get('pluginid')}] {alert.get('alert')}")

    print("ZAP alerts by risk (distinct alert types):")
    for name, n in counts.items():
        print(f"  {name:<13} {n}")
    if highs:
        print("FAIL: high-risk alerts found:")
        print("\n".join(highs))
        return 1
    print("PASS: no high-risk alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
