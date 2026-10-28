"""Run this tool's matching on one half of the converted Splink data and compute the rates.

    .venv\\Scripts\\python tools\\run_splink.py h1            # first half: the rules were written looking at this
    .venv\\Scripts\\python tools\\run_splink.py h2 --final    # second half: allowed ONCE; a marker file blocks a repeat

Prerequisite: tools/convert_splink.py has written var/splink/*.csv.
The rules below are the ones fixed from the first half. They are not changed after the second half is run.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from close_kit import auth, db, ingest, matchrun, metrics, versions, workbook  # noqa: E402
from close_kit.roles import User  # noqa: E402
from tools import sample_builders as sb  # noqa: E402

DATA = ROOT / "var" / "splink"
MARKER = DATA / "h2_run_done.json"


def rule(order, name, kind, keys, window, tol):
    return ["pair", order, name, kind, "1対1", keys, window, tol, "", "", "", "", "はい"]


AMT_DATE = "金額=金額; 勘定日=勘定日"
MEMO_AMT_DATE = "摘要=摘要; 金額=金額; 勘定日=勘定日"
# Rules fixed from the first half (docs/validation/splink_rules.md says how they were chosen).
RULES = [
    rule(10, "摘要が同じ・金額 30.00 以内・日付 21 日以内", "自動", MEMO_AMT_DATE, 21, 3000),
    rule(20, "金額が同じ・日付 6 日以内", "候補", AMT_DATE, 6, 0),
]


def build_workbook(rules=None) -> bytes:
    sheets = {
        "データ源": [
            ["origin", "csv", "", "帳簿の借方を + にする", 1, "西暦", "id", "date", "amount_minor", "", "memo", ""],
            ["destination", "csv", "", "入金を + にする", 1, "西暦", "id", "date", "amount_minor", "", "memo", ""],
        ],
        "会計期間": [["2022-H1", "2022-01-01", "2022-06-30", "いいえ"]],
        "照合セット": [["pair", "origin", "destination", "はい", "右", "1100", "いいえ"]],
        "表示項目": [], "小計項目": [], "パスルール": rules if rules is not None else RULES,
    }
    return sb.build_workbook(sheets)


def run(half: str, rules=None, workdir: Path | None = None) -> dict:
    auth.SCRYPT_N = 2**10  # throw-away account for the run
    with tempfile.TemporaryDirectory() as td:
        path = (workdir or Path(td)) / f"splink_{half}.sqlite3"
        if path.exists():
            path.unlink()
        conn = db.connect(path)
        uid = auth.create_user(conn, "runner", "password-runner", ["管理者", "作成者"])
        user = User(id=uid, name="runner", roles=frozenset({"管理者", "作成者"}))
        data = build_workbook(rules)
        res = workbook.parse_workbook(data)
        assert res.ok, [str(e) for e in res.errors]
        versions.save_version(conn, user, res, data)
        t0 = time.time()
        for src, name in (("origin", f"origin_{half}.csv"), ("destination", f"destination_{half}.csv")):
            r = ingest.ingest(conn, user, src, name, (DATA / name).read_bytes())
            assert r.ok, r.reasons
        t1 = time.time()
        s = matchrun.run_match(conn, user, "pair")
        t2 = time.time()
        truth = metrics.parse_truth_csv((DATA / f"truth_{half}.csv").read_bytes())
        m = metrics.from_database(conn, "sandbox", "pair", truth)
        by_rule = {}
        for g in conn.execute("SELECT rule_order, kind, state, reason, COUNT(*) AS n FROM match_groups"
                              " GROUP BY rule_order, kind, state, reason"):
            by_rule[f"{g['rule_order']}/{g['kind']}/{g['state']}/{g['reason']}"] = g["n"]
        conn.close()
    return {"half": half, "by_class": s.by_class, "groups": s.groups, "groups_by_rule": by_rule,
            "metrics": m.__dict__, "seconds": {"ingest": round(t1 - t0, 1), "match": round(t2 - t1, 1)}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("half", choices=["h1", "h2"])
    ap.add_argument("--final", action="store_true", help="required for h2; allowed once")
    a = ap.parse_args()
    if a.half == "h2":
        if not a.final:
            sys.exit("h2 is the final run: pass --final (allowed once)")
        if MARKER.exists():
            sys.exit(f"h2 was already run ({MARKER}); it is not repeated")
    out = run(a.half)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if a.half == "h2":
        MARKER.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
