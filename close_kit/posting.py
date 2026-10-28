"""The posting file for approved journals, and the books' own copy of a posted journal.

Format: the field technical names of the SAP S/4HANA app "Upload General Journal Entries" (App ID F2548), one row per
journal line with the header fields repeated ("Fields in One Row"):

    BUKRS,BLART,BLDAT,BUDAT,WAERS,XBLNR,BKTXT,HKONT,WRSOL,WRHAB

What was taken from SAP's documentation is the technical names and their lengths only (BUKRS 4, BLART 2, XBLNR 16,
BKTXT 25, HKONT 10; at most 999 journals per file and 999 lines per journal). The headings of SAP's own template
(the characters you see after downloading it) were not available, the date format was not stated, and nothing was
uploaded to SAP. Dates are written as YYYYMMDD (a guess, said so in the README).

Rules of this module:
 - a journal is written once; "外す" (release) by an administrator is the only way to write it again, and every
   output and release is a row of the append-only `posting_log`;
 - the file is written from production only (the sandbox is for trying things out: `export` refuses it); the first
   line of the file still names the environment;
 - WAERS is JPY, so the output is refused unless every data source involved has the multiplier 1;
 - a ledger row imported later whose reference equals a written journal number (XBLNR) is the books' copy of that
   journal: it gets the class 転記済み, leaves the remainder, and the journal stops counting as "not in the books".
"""
from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import audit, config, roles
from .roles import User
from .timeutil import now_iso

HEADER = ["BUKRS", "BLART", "BLDAT", "BUDAT", "WAERS", "XBLNR", "BKTXT", "HKONT", "WRSOL", "WRHAB"]
SANDBOX_TEXT = "試験用。転記ファイルは production から出す（sandbox では出さない）"


class PostingError(Exception):
    """The output cannot be made (the message is shown to the person)."""


@dataclass
class ExportResult:
    files: list[dict[str, Any]] = field(default_factory=list)   # {id, name, sha256, count}
    truncated: list[str] = field(default_factory=list)          # journal numbers whose memo was cut to 25 chars
    count: int = 0


def multipliers_ok(cfg: dict[str, Any]) -> bool:
    used = set()
    for m in cfg.get("match_sets", []):
        if m.get("reconcile"):
            used.update((m["left"], m["right"]))
    srcs = [s for s in cfg["sources"] if s["name"] in used] or list(cfg["sources"])
    return bool(srcs) and all(s["multiplier"] == 1 for s in srcs)


def pending_journals(conn: sqlite3.Connection, env: str) -> list[sqlite3.Row]:
    """Approved journals not written yet (never written, or released after the last output)."""
    return conn.execute(
        "SELECT j.* FROM journals j WHERE j.env = ? AND j.status = '承認済み' AND COALESCE("
        "(SELECT p.action FROM posting_log p WHERE p.journal_id = j.id ORDER BY p.id DESC LIMIT 1), '外す') = '外す'"
        " ORDER BY j.id", (env,)).fetchall()


def render(env: str, cfg: dict[str, Any], journal_rows: list[sqlite3.Row], conn: sqlite3.Connection,
           note_time: str) -> tuple[bytes, list[str]]:
    d = cfg["description"]
    out = io.StringIO()
    out.write(f"# 環境: {env}（{'試験用の出力。本番の転記には使わない' if env != config.ENV_PRODUCTION else '本番'}）"
              f" 出力 {note_time}\r\n")
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(HEADER)
    cut: list[str] = []
    for j in journal_rows:
        bktxt = j["memo"][:config.BKTXT_LENGTH]
        if len(j["memo"]) > config.BKTXT_LENGTH:
            cut.append(j["number"])
        day = j["entry_date"].replace("-", "")
        for ln in conn.execute("SELECT * FROM journal_lines WHERE journal_id = ? ORDER BY line_no", (j["id"],)):
            w.writerow([d["company_code"], d["journal_type"], day, day, "JPY", j["number"], bktxt,
                        ln["account_code"], ln["debit"] or "", ln["credit"] or ""])
    return out.getvalue().encode("utf-8-sig"), cut


def export(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX) -> ExportResult:
    from . import versions
    roles.require(conn, user, "posting_export", env=env)
    if env != config.ENV_PRODUCTION:
        audit.log(conn, user_id=user.id, user_name=user.name, action="posting_export",
                  outcome=audit.OUTCOME_DENIED, env=env, detail=SANDBOX_TEXT)
        raise PostingError(SANDBOX_TEXT)
    vid, cfg = versions.load_config(conn, env)
    versions.require_current_schema(cfg)
    if not multipliers_ok(cfg):
        msg = "転記ファイルの通貨は JPY 固定で、最小単位の倍率が 1 でないデータ源があるため出力しない"
        audit.log(conn, user_id=user.id, user_name=user.name, action="posting_export",
                  outcome=audit.OUTCOME_DENIED, env=env, version_id=vid, detail=msg)
        raise PostingError(msg)
    todo = pending_journals(conn, env)
    if not todo:
        raise PostingError("出力していない承認済みの仕訳が無い")
    chunks = [todo[i:i + config.POSTING_MAX_JOURNALS] for i in range(0, len(todo), config.POSTING_MAX_JOURNALS)]
    now = now_iso()
    stamp = now.replace("-", "").replace(":", "")[:15]
    res = ExportResult(count=len(todo))
    with conn:
        for k, chunk in enumerate(chunks, start=1):
            content, cut = render(env, cfg, chunk, conn, now)
            res.truncated.extend(cut)
            sha = hashlib.sha256(content).hexdigest()
            name = f"journal_post_{env}_{stamp}_{k}of{len(chunks)}.csv"
            cur = conn.execute(
                "INSERT INTO posting_files(env, name, sha256, content, journal_count, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", (env, name, sha, content, len(chunk), user.id, now))
            fid = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO posting_log(env, journal_id, action, file_id, sha256, by_user, at, note)"
                " VALUES (?, ?, '出力', ?, ?, ?, ?, '')", [(env, j["id"], fid, sha, user.id, now) for j in chunk])
            res.files.append({"id": fid, "name": name, "sha256": sha, "count": len(chunk)})
    audit.log(conn, user_id=user.id, user_name=user.name, action="posting_export", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid,
              detail=f"{len(todo)} 件の仕訳を {len(chunks)} 個のファイルに出力: " + "、".join(
                  f"{f['name']} sha256={f['sha256'][:12]}…" for f in res.files))
    return res


def release(conn: sqlite3.Connection, user: User, journal_id: int, note: str,
            env: str = config.ENV_SANDBOX) -> None:
    """「外す」: the journal may be written again. Appends a row; nothing is deleted."""
    roles.require(conn, user, "posting_export", env=env)
    j = conn.execute("SELECT * FROM journals WHERE id = ? AND env = ?", (journal_id, env)).fetchone()
    if j is None:
        raise PostingError(f"仕訳 {journal_id} は無い")
    last = conn.execute("SELECT action FROM posting_log WHERE journal_id = ? ORDER BY id DESC LIMIT 1",
                        (journal_id,)).fetchone()
    if last is None or last["action"] != "出力":
        raise PostingError(f"仕訳 {j['number']} は出力した状態ではない（外す物が無い）")
    if not note.strip():
        raise PostingError("外す理由の文が要る")
    with conn:
        conn.execute("INSERT INTO posting_log(env, journal_id, action, file_id, sha256, by_user, at, note)"
                     " VALUES (?, ?, '外す', NULL, '', ?, ?, ?)", (env, journal_id, user.id, now_iso(), note.strip()[:500]))
    audit.log(conn, user_id=user.id, user_name=user.name, action="posting_release", outcome=audit.OUTCOME_OK,
              env=env, detail=f"仕訳 {j['number']} を外した（理由: {note.strip()[:200]}）")


def rows_to_link(conn: sqlite3.Connection, env: str, ms: dict[str, Any]) -> list[tuple[int, int]]:
    """Ledger rows of this match set whose reference equals the number of a journal that was written."""
    if "bank_side" not in ms:
        return []
    book_src = ms["right"] if ms["bank_side"] == "左" else ms["left"]
    return [(r["txn_id"], r["journal_id"]) for r in conn.execute(
        "SELECT t.id AS txn_id, j.id AS journal_id FROM transactions t JOIN journals j ON j.number = t.reference"
        " AND j.env = t.env LEFT JOIN match_rows mr ON mr.env = t.env AND mr.match_set = ? AND mr.txn_id = t.id"
        " WHERE t.env = ? AND t.source = ? AND j.status = '承認済み'"
        " AND (SELECT p.action FROM posting_log p WHERE p.journal_id = j.id ORDER BY p.id DESC LIMIT 1) = '出力'"
        " AND t.id NOT IN (SELECT txn_id FROM posted_links) AND (mr.klass IS NULL OR mr.klass IN ('未照合', '重複'))"
        " ORDER BY t.id", (ms["name"], env, book_src))]


def link_rows(conn: sqlite3.Connection, env: str, match_set: str, ms: dict[str, Any],
              pairs: list[tuple[int, int]], run_id: int) -> None:
    """Inside the caller's transaction."""
    side = "R" if ms.get("bank_side") == "左" else "L"
    for txn_id, journal_id in pairs:
        conn.execute("INSERT INTO posted_links(env, journal_id, txn_id, linked_at) VALUES (?, ?, ?, ?)",
                     (env, journal_id, txn_id, now_iso()))
        conn.execute("INSERT OR REPLACE INTO match_rows(env, match_set, txn_id, side, klass, group_id, run_id)"
                     " VALUES (?, ?, ?, ?, '転記済み', NULL, ?)", (env, match_set, txn_id, side, run_id))


def list_log(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX, limit: int = 300
             ) -> list[sqlite3.Row]:
    roles.require(conn, user, "view", env=env)
    return conn.execute(
        "SELECT p.*, j.number, u.name AS by_name, f.name AS file_name FROM posting_log p"
        " JOIN journals j ON j.id = p.journal_id JOIN users u ON u.id = p.by_user"
        " LEFT JOIN posting_files f ON f.id = p.file_id WHERE p.env = ? ORDER BY p.id DESC LIMIT ?",
        (env, limit)).fetchall()


def get_file(conn: sqlite3.Connection, user: User, file_id: int, env: str = config.ENV_SANDBOX) -> sqlite3.Row:
    roles.require(conn, user, "view", env=env)
    f = conn.execute("SELECT * FROM posting_files WHERE id = ? AND env = ?", (file_id, env)).fetchone()
    if f is None:
        raise PostingError(f"ファイル {file_id} は無い")
    return f
