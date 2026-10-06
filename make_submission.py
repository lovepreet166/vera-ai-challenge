"""Build submission.jsonl for the canonical 30 test pairs.

  python dataset/generate_dataset.py --seed-dir dataset --out expanded
  python make_submission.py expanded            # writes submission.jsonl
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from composer import compose

NOW = datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc)  # dataset reference date; keeps output deterministic


def main(expanded: str = "expanded", out: str = "submission.jsonl") -> None:
    root = Path(expanded)
    load = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
    categories = {p.stem: load(p) for p in (root / "categories").glob("*.json")}
    pairs = load(root / "test_pairs.json")["pairs"]
    with open(out, "w", encoding="utf-8") as f:
        for pair in pairs:
            trigger = load(root / "triggers" / f"{pair['trigger_id']}.json")
            merchant = load(root / "merchants" / f"{pair['merchant_id']}.json")
            customer = load(root / "customers" / f"{pair['customer_id']}.json") if pair.get("customer_id") else None
            result = compose(categories[merchant["category_slug"]], merchant, trigger, customer, now=NOW)
            row = {"test_id": pair["test_id"], **{k: result[k] for k in ("body", "cta", "send_as", "suppression_key", "rationale")}}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(pairs)} rows to {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
