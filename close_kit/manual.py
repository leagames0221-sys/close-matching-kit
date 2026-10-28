"""Manual matching and its approval.

A person with the role 作成者 groups rows of class 候補 / 未照合 / 重複 by hand. The group waits in the state
承認待ち. A different person with the role 承認者 approves it (the state becomes 完了, or 差額の処理待ち when the
two sides do not add up to the same amount) or rejects it (it goes back to 候補, state 確認待ち). The person
who made the group can never decide it: that is checked with the user id, whatever roles that user has.
"""
from __future__ import annotations

import json
import sqlite3

from . import audit, config, matching, roles, versions
from .roles import User
from .timeutil import now_iso


class ManualError(Exception):
    """The request cannot be carried out (the message is shown to the user)."""


class SelfApproval(ManualError):
    pass


def _after(conn: sqlite3.Connection, env: str) -> None:
    from . import reasons, tasks
    reasons.refresh_adjusting_items(conn, env)
    tasks.recheck(conn, env)


def _release(conn: sqlite3.Connection, env: str, match_set: str, group_ids: set[int], skip: set[int]) -> None:
    """Dissolve candidate groups; their rows that are not in `skip` go back to 未照合 (or 重複)."""
    for gid in group_ids:
        rows = conn.execute("SELECT gr.txn_id, t.source, t.reference FROM match_group_rows gr"
                            " JOIN transactions t ON t.id = gr.txn_id WHERE gr.group_id = ?", (gid,)).fetchall()
        conn.execute("DELETE FROM match_groups WHERE id = ?", (gid,))
        for r in rows:
            if r["txn_id"] in skip:
                continue
            klass = "未照合"
            ref = matching.norm_text(r["reference"])
            if ref:
                n = sum(1 for x in conn.execute(
                    "SELECT reference FROM transactions WHERE env = ? AND source = ? AND reference != ''",
                    (env, r["source"])) if matching.norm_text(x["reference"]) == ref)
                if n >= 2:
                    klass = "重複"
            conn.execute("UPDATE match_rows SET klass = ?, group_id = NULL WHERE env = ? AND match_set = ?"
                         " AND txn_id = ?", (klass, env, match_set, r["txn_id"]))


def create_manual(conn: sqlite3.Connection, user: User, match_set: str, left_ids: list[int],
                  right_ids: list[int], note: str = "", env: str = config.ENV_SANDBOX) -> int:
    roles.require(conn, user, "manual_match", env=env)
    vid, cfg = versions.load_config(conn, env)
    ms = next((m for m in cfg["match_sets"] if m["name"] == match_set), None)
    if ms is None:
        raise ManualError(f"照合セット {match_set!r} は設定に無い")
    left_ids, right_ids = sorted(set(left_ids)), sorted(set(right_ids))
    if not left_ids or not right_ids:
        raise ManualError("左と右の両方から 1 行以上を選ぶ")
    rows: dict[int, sqlite3.Row] = {}
    for side, ids, source in (("L", left_ids, ms["left"]), ("R", right_ids, ms["right"])):
        for i in ids:
            row = conn.execute(
                "SELECT m.klass, m.group_id, m.side, t.source, t.booking_date, t.amount_minor FROM match_rows m"
                " JOIN transactions t ON t.id = m.txn_id WHERE m.env = ? AND m.match_set = ? AND m.txn_id = ?",
                (env, match_set, i)).fetchone()
            if row is None or row["source"] != source or row["side"] != side:
                raise ManualError(f"行 {i} はこの照合セットの{'左' if side == 'L' else '右'}にない"
                                  "（先に照合を実行する）")
            if row["klass"] not in ("候補", "未照合", "重複"):
                raise ManualError(f"行 {i} は区分「{row['klass']}」で、手の照合には使えない")
            if conn.execute("SELECT 1 FROM journals WHERE env = ? AND link_txn_id = ? AND status IN"
                            " ('下書き', '承認待ち', '承認済み')", (env, i)).fetchone():
                raise ManualError(f"行 {i} には仕訳があり、手の照合には使えない")
            rows[i] = row
    lt = sum(rows[i]["amount_minor"] for i in left_ids)
    rt = sum(rows[i]["amount_minor"] for i in right_ids)
    latest = max(r["booking_date"] for r in rows.values())
    now = now_iso()
    with conn:
        _release(conn, env, match_set, {r["group_id"] for r in rows.values() if r["group_id"] is not None},
                 set(rows))
        cur = conn.execute(
            "INSERT INTO runs(kind, env, version_id, period, created_by, created_at, summary_json)"
            " VALUES ('match', ?, ?, NULL, ?, ?, ?)",
            (env, vid, user.id, now, json.dumps({"manual": True})))
        run_id = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO match_groups(run_id, env, version_id, period, match_set, rule_order, rule_name, kind,"
            " state, reason, left_total, right_total, diff_minor, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, NULL, '手の照合', '手', '承認待ち', ?, ?, ?, ?, ?, ?)",
            (run_id, env, vid, versions.period_for(cfg, latest), match_set, note[:500], lt, rt, lt - rt,
             user.id, now))
        gid = int(cur.lastrowid)
        conn.executemany("INSERT INTO match_group_rows(group_id, txn_id, side) VALUES (?, ?, ?)",
                         [(gid, i, "L") for i in left_ids] + [(gid, i, "R") for i in right_ids])
        conn.executemany("UPDATE match_rows SET klass = '手', group_id = ?, run_id = ? WHERE env = ?"
                         " AND match_set = ? AND txn_id = ?",
                         [(gid, run_id, env, match_set, i) for i in left_ids + right_ids])
    audit.log(conn, user_id=user.id, user_name=user.name, action="manual_match", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid, detail=f"グループ {gid}: 左 {left_ids} 右 {right_ids} 差額 {lt - rt}")
    _after(conn, env)
    return gid


def decide(conn: sqlite3.Connection, user: User, group_id: int, approve: bool,
           env: str = config.ENV_SANDBOX) -> str:
    """Approve or reject a manual group. Returns the new state."""
    op = "承認" if approve else "却下"
    roles.require(conn, user, "manual_decide", env=env)
    g = conn.execute("SELECT * FROM match_groups WHERE id = ? AND env = ?", (group_id, env)).fetchone()
    if g is None or g["kind"] != "手":
        raise ManualError(f"手の照合のグループ {group_id} は無い")
    if g["state"] != "承認待ち":
        raise ManualError(f"グループ {group_id} は {g['state']} で、{op}できる状態ではない")
    if g["created_by"] == user.id:
        audit.log(conn, user_id=user.id, user_name=user.name, action="manual_decide",
                  outcome=audit.OUTCOME_DENIED, env=env, version_id=g["version_id"],
                  detail=f"グループ {group_id}: 本人が照合したグループは本人が{op}できない")
        raise SelfApproval(f"自分が照合したグループは{op}できない（別の承認者が行う）")
    new_state = ("完了" if g["diff_minor"] == 0 else "差額の処理待ち") if approve else "確認待ち"
    with conn:
        conn.execute("UPDATE match_groups SET state = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                     (new_state, user.id, now_iso(), group_id))
        if not approve:
            conn.execute("UPDATE match_rows SET klass = '候補' WHERE group_id = ? AND env = ?", (group_id, env))
    audit.log(conn, user_id=user.id, user_name=user.name, action="manual_decide", outcome=audit.OUTCOME_OK,
              env=env, version_id=g["version_id"], detail=f"グループ {group_id}: {op} -> {new_state}")
    _after(conn, env)
    return new_state
