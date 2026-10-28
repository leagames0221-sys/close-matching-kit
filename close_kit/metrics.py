"""Precision / recall against an external answer file (formulas: docs/validation/metrics.md).

A group (bundle) is expanded to every pair (left row, right row) inside it, both for the true pairs and for
the pairs the tool made. A denominator of 0 gives None, shown as 該当なし.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from itertools import product


class TruthError(Exception):
    pass


def parse_truth_csv(data: bytes) -> set[tuple[str, str]]:
    """CSV with a header row; columns `左の行` and `右の行` hold the ext ids of the two rows."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise TruthError("正解ファイルは UTF-8 で書く") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "左の行" not in reader.fieldnames or "右の行" not in reader.fieldnames:
        raise TruthError("正解ファイルの見出しは「左の行」「右の行」")
    return {(r["左の行"].strip(), r["右の行"].strip()) for r in reader if r["左の行"].strip()}


@dataclass
class Metrics:
    truth_pairs: int
    auto_pairs: int
    auto_correct: int
    cand_pairs: int
    cand_correct: int
    precision: float | None
    recall: float | None
    recall_with_candidates: float | None
    unmatched_rows: int
    duplicate_rows: int


def ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def compute(auto_pairs: set[tuple[str, str]], cand_pairs: set[tuple[str, str]],
            truth: set[tuple[str, str]], unmatched_rows: int = 0, duplicate_rows: int = 0) -> Metrics:
    auto_ok = len(auto_pairs & truth)
    cand_ok = len((auto_pairs | cand_pairs) & truth)
    return Metrics(
        truth_pairs=len(truth), auto_pairs=len(auto_pairs), auto_correct=auto_ok,
        cand_pairs=len(cand_pairs), cand_correct=cand_ok,
        precision=ratio(auto_ok, len(auto_pairs)),
        recall=ratio(auto_ok, len(truth)),
        recall_with_candidates=ratio(cand_ok, len(truth)),
        unmatched_rows=unmatched_rows, duplicate_rows=duplicate_rows)


def expand_groups(groups: dict[int, tuple[list[str], list[str]]]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for lefts, rights in groups.values():
        out.update(product(lefts, rights))
    return out


def from_database(conn: sqlite3.Connection, env: str, match_set: str, truth: set[tuple[str, str]]) -> Metrics:
    """Auto pairs = groups of kind 自動 (state 完了 or 差額の処理待ち). Candidate pairs = groups of kind 候補."""
    auto: dict[int, tuple[list[str], list[str]]] = {}
    cand: dict[int, tuple[list[str], list[str]]] = {}
    q = ("SELECT g.id, g.kind, gr.side, t.ext_id FROM match_groups g"
         " JOIN match_group_rows gr ON gr.group_id = g.id JOIN transactions t ON t.id = gr.txn_id"
         " WHERE g.env = ? AND g.match_set = ? AND g.kind IN ('自動', '候補')")
    for r in conn.execute(q, (env, match_set)):
        target = auto if r["kind"] == "自動" else cand
        l, rt = target.setdefault(r["id"], ([], []))
        (l if r["side"] == "L" else rt).append(r["ext_id"])
    # an ext_id names a row only within its side; one shared by two rows (for example the record number of two
    # Zengin files, or the line number of two CSV files without a row key) cannot be counted, so it is refused
    shared = {(r["side"], r["ext_id"]) for r in conn.execute(
        "SELECT m.side, t.ext_id FROM match_rows m JOIN transactions t ON t.id = m.txn_id"
        " WHERE m.env = ? AND m.match_set = ? GROUP BY m.side, t.ext_id HAVING COUNT(*) > 1", (env, match_set))}
    used = {("L", l) for l, _ in truth} | {("R", r) for _, r in truth}
    for lefts, rights in list(auto.values()) + list(cand.values()):
        used |= {("L", x) for x in lefts} | {("R", x) for x in rights}
    clash = sorted(used & shared)
    if clash:
        shown = "、".join(f"{'左' if s == 'L' else '右'} {x}" for s, x in clash[:5])
        raise TruthError(f"行の名前（ext_id）が 2 つ以上の明細に当たるため数えられない: {shown}")
    counts = {r["klass"]: r["n"] for r in conn.execute(
        "SELECT klass, COUNT(*) AS n FROM match_rows WHERE env = ? AND match_set = ? GROUP BY klass",
        (env, match_set))}
    return compute(expand_groups(auto), expand_groups(cand), truth,
                   unmatched_rows=counts.get("未照合", 0), duplicate_rows=counts.get("重複", 0))
