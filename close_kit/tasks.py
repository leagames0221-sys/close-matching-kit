"""The period-end task list.

Tasks are made per accounting period by an administrator (never twice for one period and task id). A task keeps the
definition it was made with - preparer, reviewer, predecessors, due date rule, completion condition - until the
tasks of that period are made again; a newer workbook does not change it.

Flow: 未着手 -> 作成完了 (the preparer) -> 完了 (the reviewer). The reviewer can send it back (reason required).
Preparer and reviewer are different people (checked when the workbook is read and again here). A task cannot reach
作成完了 while a predecessor of the same period is not 完了. A completion condition is checked at 作成完了 and at 完了:

  なし | 未処理 0 件 | 残高一致の差 0     (the conditions are the author's design decision; the closest source is BlackLine's "decertifies the rec" when the balance changes, ADR 0007)

and a 完了 task whose condition stops holding (new import, a match run, a journal or an adjusting item approved,
rejected or withdrawn) goes back to 作成完了 with the reason written to the log - the same idea as a reconciliation
that is "decertified" when the balance changes. A period's figures are "as of its period end", so events after the
period end do not undo the tasks of that period.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import audit, balance, config, holidays, roles, versions
from .money import fmt_amount, set_multipliers
from .roles import User
from .timeutil import now_iso


class TaskError(Exception):
    """The request cannot be carried out (the message is shown to the person)."""


@dataclass
class Generated:
    created: list[int] = field(default_factory=list)
    existing: int = 0
    warnings: list[str] = field(default_factory=list)


def _cfg(conn: sqlite3.Connection, env: str) -> tuple[int, dict[str, Any]]:
    vid, cfg = versions.load_config(conn, env)
    versions.require_current_schema(cfg)
    return vid, cfg


def _event(conn: sqlite3.Connection, env: str, instance_id: int, user_id: int | None, user_name: str,
           action: str, detail: str = "") -> None:
    conn.execute("INSERT INTO task_events(env, instance_id, at, user_id, user_name, action, detail)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)", (env, instance_id, now_iso(), user_id, user_name, action, detail))


def generate(conn: sqlite3.Connection, user: User, period_name: str, env: str = config.ENV_SANDBOX) -> Generated:
    roles.require(conn, user, "task_generate", env=env)
    vid, cfg = _cfg(conn, env)
    period = next((p for p in cfg["periods"] if p["name"] == period_name), None)
    if period is None:
        raise TaskError(f"会計期間 {period_name!r} は設定に無い")
    if period["closed"]:
        raise TaskError(f"期間 {period_name} は締めてあるので、作業は作らない")
    table = holidays.load()
    company = frozenset(date.fromisoformat(h["date"]) for h in cfg["company_holidays"])
    end = date.fromisoformat(period["end"])
    tasks = [t for t in cfg["tasks"] if t["enabled"] and (t["type"] == "繰り返し" or t["period"] == period_name)]
    ids = {t["id"] for t in tasks}
    res = Generated()
    with conn:
        for t in tasks:
            if conn.execute("SELECT 1 FROM task_instances WHERE env = ? AND period = ? AND task_id = ?",
                            (env, period_name, t["id"])).fetchone():
                res.existing += 1
                continue
            for pre in t["predecessors"]:
                if pre not in ids:
                    res.warnings.append(f"作業 {t['id']} の前の作業 {pre} はこの期間に無いので「前の作業なし」とみなす")
            due = holidays.due_date(end, t["counting"], t["offset"], table=table, company_holidays=company)
            cur = conn.execute(
                "INSERT INTO task_instances(env, period, task_id, name, definition_json, version_id, due_date,"
                " due_note, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '未着手', ?, ?)",
                (env, period_name, t["id"], t["name"], json.dumps(t, ensure_ascii=False, sort_keys=True), vid,
                 due.date.isoformat() if due.date else None, due.note, user.id, now_iso()))
            iid = int(cur.lastrowid)
            res.created.append(iid)
            _event(conn, env, iid, user.id, user.name, "作成", f"設定の版 {vid}、期日 {due.date or due.note}")
    audit.log(conn, user_id=user.id, user_name=user.name, action="task_generate", outcome=audit.OUTCOME_OK, env=env,
              version_id=vid, detail=f"期間 {period_name}: 作った {len(res.created)} 件、すでにある {res.existing} 件")
    return res


# ---- completion conditions -----------------------------------------------------------------------------------

def unprocessed_counts(conn: sqlite3.Connection, env: str, cfg: dict[str, Any], match_set: str, end: str
                       ) -> dict[str, int]:
    """Counts behind the condition 「未処理 0 件」 as of the period end (rows dated on or before `end`)."""
    ms = next((m for m in cfg["match_sets"] if m["name"] == match_set), None)
    if ms is None:
        raise TaskError(f"照合セット {match_set!r} は設定に無い")
    srcs = (ms["left"], ms["right"])
    base = ("FROM transactions t LEFT JOIN match_rows m ON m.env = t.env AND m.match_set = ? AND m.txn_id = t.id"
            " WHERE t.env = ? AND t.source IN (?, ?) AND t.booking_date <= ?")
    args = (match_set, env, *srcs, end)
    out = {}
    out["未照合・重複・候補の行（調整項目も仕訳も無い）"] = conn.execute(
        f"SELECT COUNT(*) {base} AND (m.klass IS NULL OR m.klass IN ('未照合', '重複', '候補'))"
        " AND NOT EXISTS (SELECT 1 FROM adjusting_items ai WHERE ai.txn_id = t.id AND ai.status IN ('未解消', '解消'))"
        " AND NOT EXISTS (SELECT 1 FROM journals j WHERE j.link_txn_id = t.id AND j.status IN"
        " ('下書き', '承認待ち', '承認済み'))", args).fetchone()[0]

    def groups(where: str) -> int:
        return conn.execute(
            "SELECT COUNT(DISTINCT g.id) FROM match_groups g JOIN match_group_rows gr ON gr.group_id = g.id"
            " JOIN transactions t ON t.id = gr.txn_id WHERE g.env = ? AND g.match_set = ? AND t.booking_date <= ?"
            f" AND {where}", (env, match_set, end)).fetchone()[0]

    # The difference of a pair is the business of the period of its latest bank row (its journal takes that date), so
    # a pair whose bank side is dated after the period end does not hold this period up (a later event alone must not
    # undo this period's tasks; the statement of this period counts such a pair as a timing item).
    bank_side = "L" if ms["bank_side"] == "左" else "R"
    def diff_groups(state: str) -> int:
        return conn.execute(
            "SELECT COUNT(DISTINCT g.id) FROM match_groups g JOIN match_group_rows gr ON gr.group_id = g.id"
            " JOIN transactions t ON t.id = gr.txn_id WHERE g.env = ? AND g.match_set = ? AND t.booking_date <= ?"
            " AND g.state = ? AND NOT EXISTS (SELECT 1 FROM match_group_rows b JOIN transactions bt ON bt.id = b.txn_id"
            " WHERE b.group_id = g.id AND b.side = ? AND bt.booking_date > ?)",
            (env, match_set, end, state, bank_side, end)).fetchone()[0]

    out["手の照合の承認待ち"] = groups("g.kind = '手' AND g.state = '承認待ち'")
    out["差額の処理待ち"] = diff_groups("差額の処理待ち")
    # a pair the tool matched at once (完了) whose difference has a draft or a waiting journal is counted here too
    drafted_pairs = conn.execute(
        "SELECT COUNT(DISTINCT g.id) FROM match_groups g JOIN match_group_rows gr ON gr.group_id = g.id"
        " JOIN transactions t ON t.id = gr.txn_id WHERE g.env = ? AND g.match_set = ? AND t.booking_date <= ?"
        " AND g.state != '仕訳の承認待ち' AND EXISTS (SELECT 1 FROM journals j WHERE j.link_group_id = g.id"
        " AND j.status IN ('下書き', '承認待ち'))"
        " AND NOT EXISTS (SELECT 1 FROM match_group_rows b JOIN transactions bt ON bt.id = b.txn_id"
        " WHERE b.group_id = g.id AND b.side = ? AND bt.booking_date > ?)",
        (env, match_set, end, bank_side, end)).fetchone()[0]
    out["仕訳の承認待ち"] = diff_groups("仕訳の承認待ち") + drafted_pairs + conn.execute(
        f"SELECT COUNT(*) {base} AND EXISTS (SELECT 1 FROM journals j WHERE j.link_txn_id = t.id"
        " AND j.status IN ('下書き', '承認待ち'))", args).fetchone()[0]
    out["調整項目の承認待ち"] = conn.execute(
        "SELECT COUNT(*) FROM adjusting_items ai JOIN transactions t ON t.id = ai.txn_id WHERE ai.env = ?"
        " AND ai.match_set = ? AND ai.status = '承認待ち' AND t.booking_date <= ?", (env, match_set, end)
    ).fetchone()[0]
    return out


def check_condition(conn: sqlite3.Connection, env: str, cfg: dict[str, Any], d: dict[str, Any], period_name: str,
                    today: date | None = None) -> list[str]:
    """Why the completion condition does not hold (empty list = it holds)."""
    cond = d["condition"]
    if cond == "なし":
        return []
    ms = d["condition_match_set"]
    p = next((q for q in cfg["periods"] if q["name"] == period_name), None)
    if p is None:
        return [f"会計期間 {period_name} が設定に無い"]
    if not any(m["name"] == ms for m in cfg["match_sets"]):
        return [f"照合セット {ms} が設定に無い"]
    if cond == "未処理 0 件":
        return [f"{k} が {n} 件" for k, n in unprocessed_counts(conn, env, cfg, ms, p["end"]).items() if n]
    st = balance.statement(conn, env, cfg, ms, period_name, today)
    if not st.known:
        return [st.why_unknown]
    if st.difference != 0:
        m = set_multipliers(cfg, ms)[0]                      # written like the balance statement screen writes it
        return [f"残高一致の差が {fmt_amount(st.difference, m)}（調整後の銀行 {fmt_amount(st.adjusted_bank, m)}、"
                f"調整後の帳簿 {fmt_amount(st.adjusted_book, m)}）"]
    return []


# ---- flow ----------------------------------------------------------------------------------------------------

def _instance(conn: sqlite3.Connection, instance_id: int, env: str) -> tuple[sqlite3.Row, dict[str, Any]]:
    row = conn.execute("SELECT * FROM task_instances WHERE id = ? AND env = ?", (instance_id, env)).fetchone()
    if row is None:
        raise TaskError(f"作業 {instance_id} は無い")
    return row, json.loads(row["definition_json"])


def _denied(conn, user: User, action: str, env: str, vid: int | None, detail: str) -> None:
    audit.log(conn, user_id=user.id, user_name=user.name, action=action, outcome=audit.OUTCOME_DENIED, env=env,
              version_id=vid, detail=detail)


def predecessor_states(conn: sqlite3.Connection, env: str, period: str, d: dict[str, Any]) -> list[tuple[str, str]]:
    """[(task id, status)] for the predecessors that exist in this period (others count as "no predecessor")."""
    out = []
    for pre in d["predecessors"]:
        r = conn.execute("SELECT status FROM task_instances WHERE env = ? AND period = ? AND task_id = ?",
                         (env, period, pre)).fetchone()
        if r is not None:
            out.append((pre, r["status"]))
    return out


def _same_person(conn, user: User, row: sqlite3.Row, d: dict[str, Any], action: str, env: str, vid: int) -> None:
    if d["preparer"] == d["reviewer"]:  # the workbook refuses this; checked again here on the operation
        _denied(conn, user, action, env, vid, f"作業 {d['id']}（{row['period']}）: 作成者と確認者が同じ人（{d['preparer']}）")
        raise TaskError(f"作成者と確認者が同じ人（{d['preparer']}）の作業は進められない")


def prepare(conn: sqlite3.Connection, user: User, instance_id: int, env: str = config.ENV_SANDBOX,
            today: date | None = None) -> None:
    roles.require(conn, user, "task_prepare", env=env)
    vid, cfg = _cfg(conn, env)
    row, d = _instance(conn, instance_id, env)
    _same_person(conn, user, row, d, "task_prepare", env, vid)
    if user.name != d["preparer"]:
        _denied(conn, user, "task_prepare", env, vid, f"作業 {d['id']}（{row['period']}）: 作成者は {d['preparer']}")
        raise TaskError(f"この作業の作成者は {d['preparer']} で、{user.name} ではない")
    if row["status"] != "未着手":
        raise TaskError(f"作業 {d['id']} は {row['status']} で、作成完了にできる状態ではない")
    waiting = [f"{t}（{s}）" for t, s in predecessor_states(conn, env, row["period"], d) if s != "完了"]
    if waiting:
        _denied(conn, user, "task_prepare", env, vid, f"作業 {d['id']}: 前の作業が完了していない: " + "、".join(waiting))
        raise TaskError("前の作業が全部「完了」になるまで作成完了にできない: " + "、".join(waiting))
    why = check_condition(conn, env, cfg, d, row["period"], today)
    if why:
        _denied(conn, user, "task_prepare", env, vid, f"作業 {d['id']}: 完了の条件を満たさない: " + "、".join(why))
        raise TaskError(f"完了の条件「{d['condition']}」を満たさない: " + "、".join(why))
    with conn:
        conn.execute("UPDATE task_instances SET status = '作成完了' WHERE id = ?", (instance_id,))
        _event(conn, env, instance_id, user.id, user.name, "作成完了", "")
    audit.log(conn, user_id=user.id, user_name=user.name, action="task_prepare", outcome=audit.OUTCOME_OK, env=env,
              version_id=vid, detail=f"作業 {d['id']}（{row['period']}）を作成完了")


def complete(conn: sqlite3.Connection, user: User, instance_id: int, env: str = config.ENV_SANDBOX,
             today: date | None = None) -> None:
    roles.require(conn, user, "task_review", env=env)
    vid, cfg = _cfg(conn, env)
    row, d = _instance(conn, instance_id, env)
    _same_person(conn, user, row, d, "task_review", env, vid)
    if user.name != d["reviewer"]:
        _denied(conn, user, "task_review", env, vid, f"作業 {d['id']}（{row['period']}）: 確認者は {d['reviewer']}")
        raise TaskError(f"この作業の確認者は {d['reviewer']} で、{user.name} ではない")
    if row["status"] != "作成完了":
        raise TaskError(f"作業 {d['id']} は {row['status']} で、完了にできる状態ではない")
    why = check_condition(conn, env, cfg, d, row["period"], today)
    if why:
        _denied(conn, user, "task_review", env, vid, f"作業 {d['id']}: 完了の条件を満たさない: " + "、".join(why))
        raise TaskError(f"完了の条件「{d['condition']}」を満たさない: " + "、".join(why))
    with conn:
        conn.execute("UPDATE task_instances SET status = '完了', note = '' WHERE id = ?", (instance_id,))
        _event(conn, env, instance_id, user.id, user.name, "完了", "")
    audit.log(conn, user_id=user.id, user_name=user.name, action="task_complete", outcome=audit.OUTCOME_OK, env=env,
              version_id=vid, detail=f"作業 {d['id']}（{row['period']}）を完了")


def send_back(conn: sqlite3.Connection, user: User, instance_id: int, reason: str,
              env: str = config.ENV_SANDBOX) -> None:
    roles.require(conn, user, "task_review", env=env)
    row, d = _instance(conn, instance_id, env)
    if user.name != d["reviewer"]:
        _denied(conn, user, "task_review", env, row["version_id"],
                f"作業 {d['id']}（{row['period']}）: 確認者は {d['reviewer']}")
        raise TaskError(f"この作業の確認者は {d['reviewer']} で、{user.name} ではない")
    if row["status"] != "作成完了":
        raise TaskError(f"作業 {d['id']} は {row['status']} で、差し戻せる状態ではない")
    if not reason.strip():
        raise TaskError("差し戻しには理由の文が要る")
    with conn:
        conn.execute("UPDATE task_instances SET status = '未着手', note = ? WHERE id = ?",
                     (f"差し戻し: {reason.strip()[:300]}", instance_id))
        _event(conn, env, instance_id, user.id, user.name, "差し戻し", reason.strip()[:300])
    audit.log(conn, user_id=user.id, user_name=user.name, action="task_send_back", outcome=audit.OUTCOME_OK, env=env,
              version_id=row["version_id"], detail=f"作業 {d['id']}（{row['period']}）を差し戻し: {reason.strip()[:200]}")


def recheck(conn: sqlite3.Connection, env: str, today: date | None = None) -> list[int]:
    """A 完了 task whose condition no longer holds goes back to 作成完了 (reason on the screen and in the log)."""
    try:
        _vid, cfg = _cfg(conn, env)
    except (versions.NoConfig, versions.SchemaTooOld):
        return []
    changed: list[int] = []
    for row in conn.execute("SELECT * FROM task_instances WHERE env = ? AND status = '完了'", (env,)).fetchall():
        d = json.loads(row["definition_json"])
        if d["condition"] == "なし":
            continue
        try:
            why = check_condition(conn, env, cfg, d, row["period"], today)
        except (TaskError, ValueError) as e:
            why = [str(e)]
        if not why:
            continue
        msg = "完了の条件が崩れたので作成完了に戻した: " + "、".join(why)
        with conn:
            conn.execute("UPDATE task_instances SET status = '作成完了', note = ? WHERE id = ?", (msg[:500], row["id"]))
            _event(conn, env, row["id"], None, "(システム)", "完了の取り消し", msg[:500])
        audit.log(conn, user_id=None, user_name="(システム)", action="task_decertify", outcome=audit.OUTCOME_OK,
                  env=env, version_id=row["version_id"], detail=f"作業 {d['id']}（{row['period']}）: {msg}"[:3000])
        changed.append(row["id"])
    return changed


# ---- reading -------------------------------------------------------------------------------------------------

def list_instances(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX, period: str | None = None,
                   today: date | None = None) -> list[dict[str, Any]]:
    roles.require(conn, user, "view", env=env)
    today = today or date.today()
    try:
        _vid, cfg = _cfg(conn, env)
    except (versions.NoConfig, versions.SchemaTooOld):
        raise
    where, args = ["env = ?"], [env]
    if period:
        where.append("period = ?")
        args.append(period)
    out = []
    for row in conn.execute(f"SELECT * FROM task_instances WHERE {' AND '.join(where)} ORDER BY period, id", args):
        d = json.loads(row["definition_json"])
        try:
            why = check_condition(conn, env, cfg, d, row["period"], today)
        except (TaskError, ValueError) as e:
            why = [str(e)]
        overdue = bool(row["due_date"]) and row["status"] != "完了" and row["due_date"] < today.isoformat()
        out.append({"row": row, "def": d, "preds": predecessor_states(conn, env, row["period"], d),
                    "condition_problems": why, "overdue": overdue})
    return out
