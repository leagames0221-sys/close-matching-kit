"""Read-only queries for the screens. Every one checks the "view" operation on the server."""
from __future__ import annotations

import sqlite3
from typing import Any

from collections import Counter

from . import audit, config, matching, matchrun, roles, versions
from .roles import User

PAGE = 200


def overview(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX) -> dict[str, Any]:
    roles.require(conn, user, "view", env=env)
    vid = versions.env_version_id(conn, env)
    return {"env": env, "version_id": vid,
            "versions": versions.list_versions(conn),
            "batches": conn.execute(
                "SELECT b.*, r.created_at FROM batches b JOIN runs r ON r.id = b.run_id WHERE b.env = ?"
                " ORDER BY b.id DESC LIMIT 50", (env,)).fetchall()}


def config_view(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX) -> tuple[int, dict] | None:
    roles.require(conn, user, "view", env=env)
    try:
        return versions.load_config(conn, env)
    except versions.NoConfig:
        return None


def match_summary(conn: sqlite3.Connection, user: User, match_set: str, env: str = config.ENV_SANDBOX) -> dict:
    roles.require(conn, user, "view", env=env)
    s = matchrun.summarize(conn, env, match_set)
    last = conn.execute("SELECT * FROM runs WHERE kind = 'match' AND env = ? ORDER BY id DESC LIMIT 1",
                        (env,)).fetchone()
    s["last_run"] = last
    return s


def mark_duplicates(conn: sqlite3.Connection, env: str, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """The rows as dicts with `dup` added: true when the row's non-empty reference (compared as the engine compares it)
    is shared with another row of the same data source - Reconify's duplicate, shown as a mark (`flag`)."""
    sources = {r["source"] for r in rows}
    shared: set[tuple[str, str]] = set()
    for src in sources:
        cnt: Counter[str] = Counter()
        for x in conn.execute("SELECT reference FROM transactions WHERE env = ? AND source = ? AND reference != ''",
                              (env, src)):
            cnt[matching.norm_text(x["reference"])] += 1
        shared |= {(src, ref) for ref, n in cnt.items() if ref and n >= 2}
    return [{**dict(r), "dup": (r["source"], matching.norm_text(r["reference"])) in shared} for r in rows]


def rows_in_class(conn: sqlite3.Connection, user: User, match_set: str, klass: str,
                  env: str = config.ENV_SANDBOX, offset: int = 0) -> list[dict[str, Any]]:
    roles.require(conn, user, "view", env=env)
    return mark_duplicates(conn, env, conn.execute(
        "SELECT m.txn_id, m.side, m.klass, m.group_id, t.source, t.booking_date, t.amount_minor, t.reference,"
        " t.memo, t.ext_id,"
        " (SELECT j.number FROM journals j WHERE j.link_txn_id = m.txn_id AND j.env = m.env AND j.status IN"
        "  ('下書き', '承認待ち', '承認済み') LIMIT 1) AS journal_number,"
        " (SELECT ai.status FROM adjusting_items ai WHERE ai.txn_id = m.txn_id AND ai.status IN"
        "  ('承認待ち', '未解消', '解消') LIMIT 1) AS item_status"
        " FROM match_rows m JOIN transactions t ON t.id = m.txn_id"
        " WHERE m.env = ? AND m.match_set = ? AND m.klass = ? ORDER BY m.side, t.booking_date, t.id"
        " LIMIT ? OFFSET ?", (env, match_set, klass, PAGE, offset)).fetchall())


def groups(conn: sqlite3.Connection, user: User, match_set: str, kind: str | None = None,
           state: str | None = None, env: str = config.ENV_SANDBOX, offset: int = 0) -> list[sqlite3.Row]:
    roles.require(conn, user, "view", env=env)
    where, args = ["g.env = ?", "g.match_set = ?"], [env, match_set]
    if kind:
        where.append("g.kind = ?")
        args.append(kind)
    if state:
        where.append("g.state = ?")
        args.append(state)
    return conn.execute(
        "SELECT g.*, cu.name AS created_name, du.name AS decided_name FROM match_groups g"
        " LEFT JOIN users cu ON cu.id = g.created_by LEFT JOIN users du ON du.id = g.decided_by"
        f" WHERE {' AND '.join(where)} ORDER BY g.id LIMIT ? OFFSET ?", [*args, PAGE, offset]).fetchall()


def group_rows(conn: sqlite3.Connection, user: User, group_id: int, env: str = config.ENV_SANDBOX
               ) -> list[dict[str, Any]]:
    roles.require(conn, user, "view", env=env)
    return mark_duplicates(conn, env, conn.execute(
        "SELECT gr.side, t.id AS txn_id, t.source, t.booking_date, t.amount_minor, t.reference, t.memo"
        " FROM match_group_rows gr JOIN transactions t ON t.id = gr.txn_id WHERE gr.group_id = ?"
        " ORDER BY gr.side, t.booking_date, t.id", (group_id,)).fetchall())


def audit_entries(conn: sqlite3.Connection, user: User, limit: int = 300) -> list[sqlite3.Row]:
    roles.require(conn, user, "view")
    return audit.list_entries(conn, limit)


def audit_csv(conn: sqlite3.Connection, user: User) -> str:
    roles.require(conn, user, "audit_csv")
    return audit.to_csv(conn)
