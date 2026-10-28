"""Run the matching engine against the database and store the result.

Re-running: groups in state 完了 / 差額の処理待ち / 承認待ち / 仕訳の承認待ち are fixed (same rows, same group id).
A pair inside the tolerance whose rule names an account for the difference is 完了 at once and the tool drafts the
journal of the difference itself (its maker is the user `config.SYSTEM_USER_NAME`; a person submits it and another
person approves it). Rows that carry a journal (draft, waiting or approved) or are 処理済み / 転記済み leave the remainder and the manual match;
a row with an adjusting item stays in it (the partner may arrive in a later period). Everything
else (automatic candidates, rejected manual groups - they went back to 候補 / 確認待ち -, 未照合, 重複) is
recomputed from the remaining rows only. The operation log keeps the record of a rejection.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import audit, auth, config, matching, posting, roles, versions
from .roles import User
from .timeutil import now_iso

FIXED_STATES = ("完了", "差額の処理待ち", "承認待ち", "仕訳の承認待ち")


@dataclass
class MatchSummary:
    run_id: int
    match_set: str
    by_class: dict[str, dict[str, int]]
    groups: dict[str, int]
    drafted: int = 0                                  # journals the tool drafted for the difference of a pair
    skipped_bundles: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)    # why a journal could not be drafted
    pool_rows: int = 0
    fixed_rows: int = 0


class MatchSetNotFound(Exception):
    pass


def find_match_set(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    for m in cfg["match_sets"]:
        if m["name"] == name:
            return m
    raise MatchSetNotFound(f"照合セット {name!r} は設定に無い")


def _fixed_condition() -> str:
    states = ",".join("'%s'" % s for s in FIXED_STATES)
    return f"g.state IN ({states})"


def run_match(conn: sqlite3.Connection, user: User, match_set: str, env: str = config.ENV_SANDBOX,
              period: str | None = None) -> MatchSummary:
    roles.require(conn, user, "match_run", env=env)
    vid, cfg = versions.load_config(conn, env)
    ms = find_match_set(cfg, match_set)
    if not ms["enabled"]:
        raise MatchSetNotFound(f"照合セット {match_set!r} は無効")
    rules = [r for r in cfg["pass_rules"] if r["match_set"] == match_set]

    fixed_ids = {r[0] for r in conn.execute(
        f"SELECT gr.txn_id FROM match_group_rows gr JOIN match_groups g ON g.id = gr.group_id"
        f" WHERE g.env = ? AND g.match_set = ? AND {_fixed_condition()}", (env, match_set))}
    # rows that leave the remainder because a journal covers them (or the books already hold the journal)
    held_ids = {r[0] for r in conn.execute(
        "SELECT link_txn_id FROM journals WHERE env = ? AND link_txn_id IS NOT NULL"
        " AND status IN ('下書き', '承認待ち', '承認済み')", (env,))}
    held_ids |= {r[0] for r in conn.execute(
        "SELECT txn_id FROM match_rows WHERE env = ? AND match_set = ? AND klass IN ('処理済み', '転記済み')",
        (env, match_set))}
    to_link = posting.rows_to_link(conn, env, ms)
    held_ids |= {txn_id for txn_id, _j in to_link}
    fixed_ids |= held_ids

    def load(side: str, source: str) -> tuple[list[matching.Row], Counter]:
        rows, fixed_refs = [], Counter()
        for t in conn.execute("SELECT id, booking_date, amount_minor, reference, memo FROM transactions"
                              " WHERE env = ? AND source = ?", (env, source)):
            row = matching.Row.make(t["id"], side, t["booking_date"], t["amount_minor"], t["reference"],
                                    t["memo"])
            if t["id"] in fixed_ids:
                if row.ref:
                    fixed_refs[row.ref] += 1
            else:
                rows.append(row)
        return rows, fixed_refs

    lefts, lrefs = load("L", ms["left"])
    rights, rrefs = load("R", ms["right"])
    result = matching.run(lefts, rights, rules, fixed_left_refs=lrefs, fixed_right_refs=rrefs)
    by_id = {r.id: r for r in lefts + rights}

    maker = auth.ensure_system_user(conn) if any(g.diff_account for g in result.groups) else None
    drafted: list[int] = []
    notes: list[str] = []
    now = now_iso()
    with conn:
        conn.execute(
            "DELETE FROM match_rows WHERE env = ? AND match_set = ? AND klass IN ('未照合', '重複', '候補')"
            " AND txn_id NOT IN (SELECT link_txn_id FROM journals WHERE env = ? AND link_txn_id IS NOT NULL"
            " AND status IN ('下書き', '承認待ち', '承認済み'))", (env, match_set, env))
        conn.execute("DELETE FROM match_groups WHERE env = ? AND match_set = ? AND state = '確認待ち'"
                     " AND kind IN ('候補', '手')", (env, match_set))
        cur = conn.execute(
            "INSERT INTO runs(kind, env, version_id, period, created_by, created_at, summary_json)"
            " VALUES ('match', ?, ?, ?, ?, ?, '{}')", (env, vid, period, user.id, now))
        run_id = int(cur.lastrowid)
        group_of: dict[int, int] = {}
        for g in result.groups:
            ids = g.left + g.right
            latest = max(by_id[i].date for i in ids)
            cur = conn.execute(
                "INSERT INTO match_groups(run_id, env, version_id, period, match_set, rule_order, rule_name,"
                " kind, state, reason, left_total, right_total, diff_minor, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, env, vid, versions.period_for(cfg, latest), match_set, g.rule_order, g.rule_name,
                 g.kind, g.state, g.reason, g.left_total, g.right_total, g.diff, user.id, now))
            gid = int(cur.lastrowid)
            conn.executemany("INSERT INTO match_group_rows(group_id, txn_id, side) VALUES (?, ?, ?)",
                             [(gid, i, "L") for i in g.left] + [(gid, i, "R") for i in g.right])
            if g.diff_account and maker is not None:
                from . import reasons
                jid, note = reasons.draft_difference_journal(conn, maker, env, vid, cfg, ms, gid, g.diff_account,
                                                             g.rule_name, g.left_total, g.right_total)
                if jid is None:      # no journal could be made: a person deals with the difference
                    conn.execute("UPDATE match_groups SET state = '差額の処理待ち' WHERE id = ?", (gid,))
                    notes.append(f"仕訳の下書きを作れなかった（{note}）。差額の処理待ちにした")
                else:
                    drafted.append(jid)
                    if note:
                        notes.append(note)
            for i in ids:
                group_of[i] = gid
        conn.executemany(
            "INSERT OR REPLACE INTO match_rows(env, match_set, txn_id, side, klass, group_id, run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(env, match_set, rid, by_id[rid].side, k, group_of.get(rid), run_id)
             for rid, k in result.klass.items()])
        posting.link_rows(conn, env, match_set, ms, to_link, run_id)
        summary = summarize(conn, env, match_set)
        conn.execute("UPDATE runs SET summary_json = ? WHERE id = ?",
                     (json.dumps({"by_class": summary["by_class"], "groups": summary["groups"],
                                  "drafted_journals": len(drafted)}, ensure_ascii=False), run_id))
    audit.log(conn, user_id=user.id, user_name=user.name, action="match_run", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid,
              detail=f"照合セット {match_set}: 左 {len(lefts)} 行・右 {len(rights)} 行を照合、固定 {len(fixed_ids)} 行")
    if drafted:
        numbers = "、".join(f"JE-{j:06d}" for j in drafted)
        audit.log(conn, user_id=maker.id, user_name=maker.name, action="journal_draft_auto", outcome=audit.OUTCOME_OK,
                  env=env, version_id=vid,
                  detail=f"照合セット {match_set}: 許容差の中の差額の仕訳 {len(drafted)} 件を下書きに作った（{numbers}）。"
                         f"照合を実行したのは {user.name}")
    from . import reasons, tasks
    reasons.refresh_adjusting_items(conn, env)
    tasks.recheck(conn, env)
    return MatchSummary(run_id=run_id, match_set=match_set, by_class=summary["by_class"],
                        groups=summary["groups"], drafted=len(drafted),
                        skipped_bundles=result.skipped_bundles, notes=notes, pool_rows=len(by_id),
                        fixed_rows=len(fixed_ids))


def summarize(conn: sqlite3.Connection, env: str, match_set: str) -> dict[str, Any]:
    by_class: dict[str, dict[str, int]] = {k: {"L": 0, "R": 0} for k in ("自動", "候補", "未照合", "重複", "手", "処理済み", "転記済み")}
    for r in conn.execute("SELECT klass, side, COUNT(*) AS n FROM match_rows WHERE env = ? AND match_set = ?"
                          " GROUP BY klass, side", (env, match_set)):
        by_class.setdefault(r["klass"], {"L": 0, "R": 0})[r["side"]] = r["n"]
    groups: dict[str, int] = {}
    for r in conn.execute("SELECT kind, state, COUNT(*) AS n FROM match_groups WHERE env = ? AND match_set = ?"
                          " GROUP BY kind, state", (env, match_set)):
        groups[f"{r['kind']}/{r['state']}"] = r["n"]
    return {"by_class": by_class, "groups": groups}
