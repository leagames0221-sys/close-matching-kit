"""Operation log. The table rejects UPDATE and DELETE (triggers in db.py)."""
from __future__ import annotations

import csv
import io
import sqlite3

from .timeutil import now_iso

OUTCOME_OK = "成功"
OUTCOME_DENIED = "拒否"


def log(conn: sqlite3.Connection, *, user_id: int | None, user_name: str, action: str,
        outcome: str, detail: str = "", env: str | None = None,
        version_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log(at, user_id, user_name, action, env, version_id, outcome, detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), user_id, user_name[:200], action, env, version_id, outcome, detail[:4000]),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_entries(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


CSV_HEADER = ["id", "日時", "利用者", "操作", "環境", "設定の版", "結果", "内容"]


# OWASP "CSV Injection": no cell may begin with = + - @, tab, carriage return, line feed, or the full-width
# variants of the formula characters. The page also lists "Prepend each cell field with a single quote" and "Wrap each
# cell field in double quotes", and says "The above techniques are not reliable in Microsoft Excel after saving and
# re-opening the CSV file"; for Excel it offers a tab character inside the quoted field. A leading single quote
# and quoting every field is the author's choice, made here.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")


def _cell(v: object) -> str:
    """Neutralise spreadsheet formula injection in exported CSV."""
    s = "" if v is None else str(v)
    if s.startswith(FORMULA_START):
        return "'" + s
    return s


def to_csv(conn: sqlite3.Connection) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    w.writerow(CSV_HEADER)
    for r in conn.execute("SELECT * FROM audit_log ORDER BY id"):
        w.writerow([r["id"], r["at"], _cell(r["user_name"]), _cell(r["action"]), r["env"] or "",
                    r["version_id"] if r["version_id"] is not None else "", r["outcome"],
                    _cell(r["detail"])])
    return out.getvalue()
