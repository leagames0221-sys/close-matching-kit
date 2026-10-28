"""Run the Reconify evals through this repository's rules and compare with expected/result.json.

    .venv\\Scripts\\python tools\\reconify_evals.py        (writes docs/validation/reconify_evals.md)

Reconify's categories are mapped onto this tool's classes like this:
    matched                -> group kind 自動, state 完了 or 差額の処理待ち (one row on each side)
    ambiguous              -> group kind 候補, reason ambiguous (a row with more than one possible partner)
    grouped_matched        -> group kind 自動 with several rows on the right  (1対多)
    many_to_many_matched   -> group kind 自動 with several rows on both sides
    amount_diff/timing_diff-> group kind 候補, reason amount_diff / timing_diff
    unmatched_left/right   -> class 未照合
    duplicates             -> the rows whose non-empty reference is shared inside their data source (the duplicate mark;
                              such a row is a candidate, matched, or - without a partner - in the class 重複)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from close_kit import auth, db, ingest, matching, matchrun, versions, workbook  # noqa: E402
from close_kit.roles import User  # noqa: E402
from tools import sample_builders as sb  # noqa: E402

EVAL_DIR = ROOT / "tests" / "external" / "reconify"
COMMIT = "2ca491fda0a19f2c977be1acf030f65b5e9bd6bc"


def rule(order, name, typ="1対1", keys="参照=参照", window=1, tol=0, lb="", rb="", pk="", limit="", acct=""):
    return ["pair", order, name, "自動", typ, keys, window, tol, lb, rb, pk, limit, "はい", acct]


# The rules written for each eval: what the Reconify reference yaml sets (date_window, amount_tolerance_minor, the
# `passes` it lists). 005 and 006 list one pass each (one_to_many / many_to_many), so each is written as that one rule:
# a 1対1 rule on the reference in front of it would keep the rows as a candidate and the bundle rule would not use them
# (ADR 0003). 003 names the account of the difference (6100); the journal draft that follows is the author's decision (Oracle creates an
# external transaction, Odoo reconciles the difference with the account of a counterpart entry).
RULES = {
    "001-missing-payment": [rule(10, "参照が同じ")],
    "002-duplicate": [rule(10, "参照が同じ")],
    "003-settlement-fee": [rule(10, "参照が同じ", tol=200, acct="6100")],
    "004-timing-difference": [rule(10, "参照が同じ", window=2)],
    "005-one-to-many": [rule(10, "1件の請求と分割入金", "1対多", "", 2, 0, "", "参照", "参照", 10)],
    "006-many-to-many": [rule(10, "両側で束ねる", "多対多", "", 1, 0, "参照", "参照", "参照", 10)],
    "010-accounting-negatives": [rule(10, "参照が同じ")],
}
# 008 needs name_mode: tokens (Jaccard similarity on the description). This tool compares fields for equality only.
NOT_EXPRESSIBLE = {"008-ambiguous-reference": "name_mode: tokens（摘要の語の重なり Jaccard 0.5）。この repo の比べ方は「等しい」だけ"}

# why a difference exists (one entry per eval that has one)
REASONS = {
    "002-duplicate": "Reconify の期待は、2 件目の重複を unmatched_left に置き、1 件目を照合する。この repo は参照が同じ左の 2 行も照合に入れ"
                     "（Reconify の duplicates の手引きの既定 `flag`: 全部の行が照合に入り、重複は見直しに出す）、右の 1 行の相手が"
                     "左の 2 行ありうるので自動にせず、3 行を 1 つの候補（ambiguous）にして人へ回す（Oracle の Ambiguous、"
                     "NetSuite の「選べないので自分で照合する」）。3 行とも重複の印が付く（ADR 0003 の D5）。",
    "003-settlement-fee": "差額 2.00 は許容差の中。規則に差額の相手勘定（6100）があるので、Reconify の matched と同じく照合は 完了 にし、"
                          "差額の仕訳は道具が下書きに作る（作者の判断。Odoo は不足払いのモデルで「差額は counterpart entries の勘定で消す」、"
                          "Oracle は「許容差の中の差額は自動で external transaction になる」と書くだけで、仕訳の下書きは書かない。"
                          "仕訳の承認は作った人以外が行う）。",
    "005-one-to-many": "束ねる右の 2 行は参照 INV-005 を共有するので、この repo は重複の印を付ける（Reconify は group_col: description で"
                       "重複の検出キーを参照から外している。この repo は検出キーを変えられない）。印は見せるだけで、照合の結果"
                       "（grouped_matched 1）は同じ。",
    "006-many-to-many": "束ねる行は参照 INV-006 を共有するので、この repo は左右の 4 行に重複の印を付ける（Reconify は 006 の検出キーを"
                        "参照から外している）。印は見せるだけで、照合の結果（many_to_many_matched 1）は同じ。",
}


def build_workbook(eval_name: str) -> bytes:
    rules = RULES[eval_name]
    sheets = {
        "データ源": [
            ["left", "csv", "", "帳簿の借方を + にする", 100, "西暦", "", "date", "amount", "reference", "description", ""],
            ["right", "csv", "", "入金を + にする", 100, "西暦", "", "date", "amount", "reference", "description", ""],
        ],
        "会計期間": [["2024-01", "2024-01-01", "2024-01-31", "いいえ"]],
        "照合セット": [["pair", "left", "right", "はい", "右", "1100", "いいえ"]],
        "表示項目": [], "小計項目": [], "パスルール": rules,
    }
    return sb.build_workbook(sheets)


def our_result(eval_name: str, tmp: Path) -> dict:
    auth.SCRYPT_N = 2**10  # the evaluator account is throw-away; the production cost is tested elsewhere
    conn = db.connect(tmp / f"{eval_name}.sqlite3")
    uid = auth.create_user(conn, "evaluator", "password-evaluator", ["管理者", "作成者"])
    user = User(id=uid, name="evaluator", roles=frozenset({"管理者", "作成者"}))
    data = build_workbook(eval_name)
    res = workbook.parse_workbook(data)
    assert res.ok, [str(e) for e in res.errors]
    versions.save_version(conn, user, res, data)
    for side in ("left", "right"):
        r = ingest.ingest(conn, user, side, f"{side}.csv", (EVAL_DIR / eval_name / "inputs" / f"{side}.csv").read_bytes())
        assert r.ok, r.reasons
    matchrun.run_match(conn, user, "pair")
    src_row = {r["id"]: (r["source"], r["src_row"]) for r in conn.execute("SELECT id, source, src_row FROM transactions")}
    out: dict = {"matched": [], "grouped_matched": [], "many_to_many_matched": [], "amount_diff": [],
                 "timing_diff": [], "unmatched_left": [], "unmatched_right": [], "duplicates": [], "ambiguous": [],
                 "notes": [], "drafts": []}
    for g in conn.execute("SELECT * FROM match_groups ORDER BY id"):
        rows = conn.execute("SELECT txn_id, side FROM match_group_rows WHERE group_id = ?", (g["id"],)).fetchall()
        ls = sorted(src_row[r["txn_id"]][1] - 1 for r in rows if r["side"] == "L")
        rs = sorted(src_row[r["txn_id"]][1] - 1 for r in rows if r["side"] == "R")
        if g["kind"] == "自動":
            key = "matched" if len(ls) == 1 and len(rs) == 1 else (
                "many_to_many_matched" if len(ls) > 1 and len(rs) > 1 else "grouped_matched")
            out[key].append((tuple(ls), tuple(rs)))
            if g["state"] == "差額の処理待ち":
                out["notes"].append(f"left-{ls[0]} と right-{rs[0]}: 状態は 差額の処理待ち（差額 {g['diff_minor']}）")
            for j in conn.execute("SELECT number, status, method FROM journals WHERE link_group_id = ?", (g["id"],)):
                out["drafts"].append(f"left-{ls[0]} と right-{rs[0]}: 完了。差額 {g['diff_minor']} の仕訳 {j['number']} を"
                                     f"道具が下書きに作った（作り方: {j['method']}、状態: {j['status']}）")
        else:
            out.setdefault(g["reason"], []).append((tuple(ls), tuple(rs)))
    shared: dict[tuple[str, str], int] = {}
    for t in conn.execute("SELECT source, reference FROM transactions WHERE reference != ''"):
        key = (t["source"], matching.norm_text(t["reference"]))
        shared[key] = shared.get(key, 0) + 1
    for r in conn.execute("SELECT m.txn_id, m.side, m.klass, t.source, t.reference FROM match_rows m"
                          " JOIN transactions t ON t.id = m.txn_id"):
        n = src_row[r["txn_id"]][1] - 1
        if r["klass"] == "未照合":
            out["unmatched_left" if r["side"] == "L" else "unmatched_right"].append(n)
        if r["reference"] and shared[(r["source"], matching.norm_text(r["reference"]))] >= 2:
            out["duplicates"].append((r["side"], n))       # the duplicate mark, whatever the row became
    conn.close()
    for k in out:
        if k not in ("notes", "drafts"):
            out[k] = sorted(out[k])
    return out


def expected_result(eval_name: str) -> dict:
    d = json.loads((EVAL_DIR / eval_name / "expected" / "result.json").read_text(encoding="utf-8"))

    def n(x: dict) -> int:
        return int(x["id"].split("-")[1])

    out: dict = {"matched": [], "grouped_matched": [], "many_to_many_matched": [], "amount_diff": [],
                 "timing_diff": [], "unmatched_left": [], "unmatched_right": [], "duplicates": []}
    for m in d.get("matched") or []:
        out["matched"].append(((n(m["left"]),), (n(m["right"]),)))
    for m in d.get("grouped_matched") or []:
        out["grouped_matched"].append(((n(m["left"]),), tuple(sorted(n(x) for x in m["rights"]))))
    for m in d.get("many_to_many_matched") or []:
        out["many_to_many_matched"].append((tuple(sorted(n(x) for x in m["lefts"])), tuple(sorted(n(x) for x in m["rights"]))))
    for k in ("amount_diff", "timing_diff"):
        for m in d.get(k) or []:
            out[k].append(((n(m["left"]),), (n(m["right"]),)))
    out["unmatched_left"] = sorted(n(x) for x in d.get("unmatched_left") or [])
    out["unmatched_right"] = sorted(n(x) for x in d.get("unmatched_right") or [])
    for g in d.get("duplicates") or []:
        for t in g["transactions"]:
            out["duplicates"].append((g["source"][0].upper(), n(t)))
    for k in out:
        out[k] = sorted(out[k])
    return out


def compare(eval_name: str, tmp: Path) -> dict:
    if eval_name in NOT_EXPRESSIBLE:
        return {"name": eval_name, "expressible": False, "reason": NOT_EXPRESSIBLE[eval_name], "diffs": []}
    ours, exp = our_result(eval_name, tmp), expected_result(eval_name)
    diffs = []
    for k in exp:
        if ours[k] != exp[k]:
            diffs.append(f"{k}: 期待 {exp[k]} / 結果 {ours[k]}")
    return {"name": eval_name, "expressible": True, "ours": ours, "expected": exp, "diffs": diffs,
            "notes": ours["notes"], "drafts": ours["drafts"], "reason": REASONS.get(eval_name, "")}


def all_evals() -> list[str]:
    return sorted(p.name for p in EVAL_DIR.iterdir() if p.is_dir())


def render(results: list[dict]) -> str:
    lines = [
        "# Reconify evals through this repository's rules",
        "",
        f"Fixtures: https://github.com/reconifyhq/reconify at commit `{COMMIT}` (MIT), copied to `tests/external/reconify/`.",
        "Generated by `tools/reconify_evals.py`; the same comparison runs in `tests/test_reconify_evals.py`.",
        "Row numbers are Reconify's ids (`left-N` = N-th data row of the file).",
        "",
        "| Eval | Expected (Reconify) | Result (this repo) | Same? | Reason for any difference |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        if not r["expressible"]:
            lines.append(f"| {r['name']} | matched 1 (token similarity on description) | not run | 表せない | {r['reason']}。"
                         "ディレクトリ名 `008-ambiguous-reference` は Reconify のままで、中身は語の重なりによる照合 |")
            continue
        exp, ours = r["expected"], r["ours"]

        def summ(d):
            parts = []
            for k in ("matched", "grouped_matched", "many_to_many_matched", "amount_diff", "timing_diff"):
                if d[k]:
                    parts.append(f"{k} {len(d[k])}")
            if d["unmatched_left"]:
                parts.append(f"unmatched_left {len(d['unmatched_left'])}")
            if d["unmatched_right"]:
                parts.append(f"unmatched_right {len(d['unmatched_right'])}")
            if d.get("ambiguous"):
                parts.append(f"ambiguous {len(d['ambiguous'])} (候補)")
            if d["duplicates"]:
                parts.append(f"duplicates {len(d['duplicates'])} rows")
            return ", ".join(parts) or "-"
        only_dup = bool(r["diffs"]) and all(d.startswith("duplicates:") for d in r["diffs"])
        same = ("yes" if not r["diffs"] and not r["notes"] else
                ("category yes, state differs" if not r["diffs"] else
                 ("categories yes, duplicate mark differs" if only_dup else "no")))
        if r["drafts"]:
            same = "yes（差額の仕訳は別に下書きになる）"
        reason = r["reason"] or ("-" if same == "yes" else "")
        lines.append(f"| {r['name']} | {summ(exp)} | {summ(ours)} | {same} | {reason} |")
    lines += ["", "Details of every difference:", ""]
    for r in results:
        if r["expressible"] and (r["diffs"] or r["notes"] or r["drafts"]):
            lines.append(f"- {r['name']}")
            for d in r["diffs"]:
                lines.append(f"  - {d}")
            for d in r["notes"] + r["drafts"]:
                lines.append(f"  - {d}")
            if r["name"] == "002-duplicate":
                lines.append(f"  - 候補（ambiguous）: {r['ours']['ambiguous']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        results = [compare(n, Path(td)) for n in all_evals()]
    out = ROOT / "docs" / "validation" / "reconify_evals.md"
    out.write_text(render(results), encoding="utf-8")
    print(render(results))


if __name__ == "__main__":
    main()
