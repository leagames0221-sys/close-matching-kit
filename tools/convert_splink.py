"""Convert the Splink bank-transaction test data (parquet) into CSV files and split it by date.

Run with the converter environment (the only place pyarrow is used; the product never imports it):

    .venv-convert\\Scripts\\python -I tools\\convert_splink.py

Input   var/raw_data/transactions_origin.parquet, transactions_destination.parquet
Output  var/splink/{origin,destination}_{h1,h2}.csv  columns: id,date,amount_minor,memo
        var/splink/truth_{h1,h2}.csv                  columns: 左の行,右の行  (ext ids of the true pairs)
        var/splink/split_info.json                    median date, row counts, sha256 of inputs and outputs

Split (decided before the rules were written, see docs/sources.md): the median of the origin dates; origin rows
dated on or before it are the first half, later ones the second half; each destination row follows the origin row
with the same ground_truth value. Amounts are converted with Decimal(str(value)) * 100 to integers.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "var" / "raw_data"
OUT = ROOT / "var" / "splink"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def to_minor(value: float) -> int:
    scaled = Decimal(str(value)) * 100
    if scaled != scaled.to_integral_value():
        raise ValueError(f"fraction of a minor unit: {value}")
    return int(scaled)


def load(name: str) -> list[dict]:
    t = pq.read_table(RAW / f"transactions_{name}.parquet").to_pylist()
    rows = []
    for r in t:
        d: date = r["transaction_date"]
        rows.append({"id": int(r["unique_id"]), "truth": int(r["ground_truth"]), "date": d.isoformat(),
                     "amount_minor": to_minor(r["amount"]), "memo": r["memo"] or ""})
    return rows


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])


def main() -> None:
    origin, dest = load("origin"), load("destination")
    for name, rows in (("origin", origin), ("destination", dest)):
        c = Counter(r["truth"] for r in rows)
        assert max(c.values()) == 1, f"{name}: a ground_truth value appears more than once"
        assert len({r["id"] for r in rows}) == len(rows), f"{name}: unique_id not unique"
    assert {r["truth"] for r in origin} == {r["truth"] for r in dest}, "ground_truth sets differ"

    ordinals = sorted(date.fromisoformat(r["date"]).toordinal() for r in origin)
    median = date.fromordinal(ordinals[(len(ordinals) - 1) // 2])  # lower median
    first_truth = {r["truth"] for r in origin if date.fromisoformat(r["date"]) <= median}
    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["id", "date", "amount_minor", "memo"]
    info: dict = {"median_origin_date": median.isoformat(), "inputs": {}, "outputs": {}, "counts": {}}
    for name in ("origin", "destination"):
        info["inputs"][f"transactions_{name}.parquet"] = sha256(RAW / f"transactions_{name}.parquet")
    by_truth_dest = {r["truth"]: r for r in dest}
    by_truth_orig = {r["truth"]: r for r in origin}
    for half, pick in (("h1", lambda t: t in first_truth), ("h2", lambda t: t not in first_truth)):
        o = [r for r in origin if pick(r["truth"])]
        d = [r for r in dest if pick(r["truth"])]
        write_csv(OUT / f"origin_{half}.csv", o, cols)
        write_csv(OUT / f"destination_{half}.csv", d, cols)
        truth = [{"左の行": by_truth_orig[t]["id"], "右の行": by_truth_dest[t]["id"]}
                 for t in sorted({r["truth"] for r in o})]
        write_csv(OUT / f"truth_{half}.csv", truth, ["左の行", "右の行"])
        info["counts"][half] = {"origin_rows": len(o), "destination_rows": len(d), "true_pairs": len(truth),
                                "origin_date_min": min(r["date"] for r in o),
                                "origin_date_max": max(r["date"] for r in o)}
    for p in sorted(OUT.glob("*.csv")):
        info["outputs"][p.name] = sha256(p)
    (OUT / "split_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
