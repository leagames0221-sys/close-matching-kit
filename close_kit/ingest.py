"""Statement import: read one file, check it, refuse it as a whole if anything is wrong, else store it.

Refusals (each one is returned with its reason and written to the operation log):
 - the file cannot be read, or a balance / total check fails (named in the reason);
 - the date range (from..to) overlaps a batch already imported for the same data source and account, or another
   bundle of the same account in the same file;
 - the file's sha256 was imported before;
 - one of the row keys was imported before.
Nothing is stored unless every check passes.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import audit, config, posting, roles, safefile, versions
from .formats import camt052, csvfmt, zengin
from .formats.base import ParsedBundle, ParseError
from .money import fmt_amount
from .roles import User
from .timeutil import now_iso

PARSERS = {"csv": csvfmt.parse, "zengin": zengin.parse, "camt052": camt052.parse}


@dataclass
class IngestResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rows: int = 0
    batch_ids: list[int] = field(default_factory=list)
    run_id: int | None = None


def find_source(cfg: dict[str, Any], name: str) -> dict[str, Any] | None:
    for s in cfg["sources"]:
        if s["name"] == name:
            return s
    return None


def _check_duplicates(conn: sqlite3.Connection, env: str, src: dict, digest: str,
                      bundles: list[ParsedBundle]) -> list[str]:
    reasons: list[str] = []
    # inside the file: two bundles (zengin blocks / camt reports) of one account must not overlap either
    for i, a in enumerate(bundles):
        for b in bundles[i + 1:]:
            if (a.account == b.account and a.date_from and b.date_from
                    and a.date_from <= b.date_to and b.date_from <= a.date_to):
                reasons.append(f"{a.label} と {b.label}: 同じファイルの中で勘定日の範囲が重なる"
                               f"（{a.date_from}〜{a.date_to} と {b.date_from}〜{b.date_to}）")
    in_file = [r.row_key for b in bundles for r in b.rows if r.row_key]
    if len(set(in_file)) != len(in_file):
        reasons.append("同じファイルの中に同じ行のキーが 2 回ある")
    same = conn.execute("SELECT id, date_from, date_to FROM batches WHERE env = ? AND source = ? AND sha256 = ?",
                        (env, src["name"], digest)).fetchone()
    if same is not None:
        reasons.append(f"同じファイル（sha256 が同じ）を既に取り込んでいる（束 {same['id']}）")
    for b in bundles:
        rows = conn.execute(
            "SELECT id, date_from, date_to FROM batches WHERE env = ? AND source = ? AND account = ?"
            " AND date_from <= ? AND date_to >= ?",
            (env, src["name"], b.account, b.date_to, b.date_from)).fetchall()
        for r in rows:
            reasons.append(
                f"{b.label}: 勘定日の範囲 {b.date_from}〜{b.date_to} が取り込み済みの束 {r['id']}"
                f"（{r['date_from']}〜{r['date_to']}）と重なる")
        keys = [r.row_key for r in b.rows if r.row_key]
        if keys:
            dup = _existing_keys(conn, env, src["name"], keys)
            if dup:
                shown = "、".join(sorted(dup)[:5])
                reasons.append(f"{b.label}: 行のキーが取り込み済みの行と同じ（{len(dup)} 件。例 {shown}）")
    return reasons


def balance_sources(cfg: dict[str, Any]) -> set[str]:
    """Data sources of the match sets that are used for the period-end balance (they must carry balances)."""
    out: set[str] = set()
    for m in cfg.get("match_sets", []):
        if m.get("reconcile"):
            out.update((m["left"], m["right"]))
    return out


def _check_continuity(conn: sqlite3.Connection, env: str, src: dict, bundles: list[ParsedBundle]) -> list[str]:
    """Balance-matching data sources only: the closing balance of one bundle of an account is the opening balance of
    the next bundle (the first release checked one file at a time)."""
    reasons: list[str] = []
    mult = int(src["multiplier"])
    known = [b for b in bundles if b.opening is not None and b.date_from]
    for b in known:
        prev = conn.execute(
            "SELECT id, date_from, date_to, closing_minor FROM batches WHERE env = ? AND source = ? AND account = ?"
            " AND date_to < ? AND closing_minor IS NOT NULL ORDER BY date_to DESC, id DESC LIMIT 1",
            (env, src["name"], b.account, b.date_from)).fetchone()
        if prev is not None and prev["closing_minor"] != b.opening:
            reasons.append(
                f"{b.label}: 取引前残高 {fmt_amount(b.opening, mult)} が、直前の束 {prev['id']}"
                f"（{prev['date_from']}〜{prev['date_to']}）の取引後残高 {fmt_amount(prev['closing_minor'], mult)} と合わない")
        nxt = conn.execute(
            "SELECT id, date_from, date_to, opening_minor FROM batches WHERE env = ? AND source = ? AND account = ?"
            " AND date_from > ? AND opening_minor IS NOT NULL ORDER BY date_from ASC, id ASC LIMIT 1",
            (env, src["name"], b.account, b.date_to)).fetchone()
        if nxt is not None and nxt["opening_minor"] != b.closing:
            reasons.append(
                f"{b.label}: 取引後残高 {fmt_amount(b.closing, mult)} が、次の束 {nxt['id']}"
                f"（{nxt['date_from']}〜{nxt['date_to']}）の取引前残高 {fmt_amount(nxt['opening_minor'], mult)} と合わない")
    ordered = sorted((b for b in known if b.closing is not None), key=lambda x: x.date_from)
    for a, b in zip(ordered, ordered[1:]):
        if a.account == b.account and a.closing != b.opening:
            reasons.append(f"{a.label} の取引後残高 {fmt_amount(a.closing, mult)} と {b.label} の取引前残高 "
                           f"{fmt_amount(b.opening, mult)} が合わない（同じファイルの中の続き）")
    return reasons


def _existing_keys(conn: sqlite3.Connection, env: str, source: str, keys: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT row_key FROM transactions WHERE env = ? AND source = ? AND row_key IN ({ph})",
                [env, source, *chunk]):
            found.add(r["row_key"])
    return found


def ingest(conn: sqlite3.Connection, user: User, source_name: str, filename: str, data: bytes,
           env: str = config.ENV_SANDBOX) -> IngestResult:
    roles.require(conn, user, "ingest", env=env)
    try:
        vid, cfg = versions.load_config(conn, env)
    except versions.NoConfig as e:
        return _refuse(conn, user, env, None, source_name, [str(e)])
    src = find_source(cfg, source_name)
    if src is None:
        return _refuse(conn, user, env, vid, source_name, [f"データ源 {source_name!r} は設定に無い"])
    try:
        if src["format"] != "camt052":
            safefile.check_text_file(data)
        parsed = PARSERS[src["format"]](data, src)
    except (safefile.FileRejected, ParseError) as e:
        return _refuse(conn, user, env, vid, source_name, [str(e)])

    reasons: list[str] = []
    for b in parsed.bundles:
        reasons.extend(f"{b.label}: {m}" for m in b.errors)
    digest = hashlib.sha256(data).hexdigest()
    if not reasons:
        reasons.extend(_check_duplicates(conn, env, src, digest, parsed.bundles))
    if not reasons and src["name"] in balance_sources(cfg):
        reasons.extend(_check_continuity(conn, env, src, parsed.bundles))
    if reasons:
        return _refuse(conn, user, env, vid, source_name, reasons)

    notes = [f"{b.label}: {b.note}" for b in parsed.bundles] + [f"取り込まなかった: {s}" for s in parsed.skipped]
    total = 0
    batch_ids: list[int] = []
    with conn:
        cur = conn.execute(
            "INSERT INTO runs(kind, env, version_id, period, created_by, created_at, summary_json)"
            " VALUES ('ingest', ?, ?, NULL, ?, ?, '{}')", (env, vid, user.id, now_iso()))
        run_id = int(cur.lastrowid)
        for b in parsed.bundles:
            cur = conn.execute(
                "INSERT INTO batches(run_id, env, version_id, period, source, account, fmt, sha256, size,"
                " date_from, date_to, row_count, note, opening_minor, closing_minor)"
                " VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, env, vid, src["name"], b.account, src["format"], digest, len(data),
                 b.date_from, b.date_to, len(b.rows), b.note, b.opening, b.closing))
            bid = int(cur.lastrowid)
            batch_ids.append(bid)
            conn.executemany(
                "INSERT INTO transactions(batch_id, run_id, env, version_id, period, source, account,"
                " booking_date, amount_minor, reference, memo, src_row, row_key, ext_id, balance_minor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(bid, run_id, env, vid, versions.period_for(cfg, r.booking_date), src["name"], b.account,
                  r.booking_date, r.amount_minor, r.reference, r.memo, r.src_row, r.row_key,
                  r.ext_id or str(r.src_row), r.balance) for r in b.rows])
            total += len(b.rows)
        # a ledger row that carries the number of a written journal is the books' copy of it: linked on import, so
        # the statement never counts the journal and its copy together (not even before the next match run)
        linked = 0
        for ms in cfg.get("match_sets", []):
            if "bank_side" in ms and src["name"] == (ms["right"] if ms["bank_side"] == "左" else ms["left"]):
                pairs = posting.rows_to_link(conn, env, ms)
                posting.link_rows(conn, env, ms["name"], ms, pairs, run_id)
                linked += len(pairs)
    if linked:
        notes.append(f"転記した仕訳の番号を参照に持つ行 {linked} 行を「転記済み」にした")
    audit.log(conn, user_id=user.id, user_name=user.name, action="ingest", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid, detail=f"データ源 {source_name}、{total} 行、sha256={digest[:16]}…")
    from . import tasks
    tasks.recheck(conn, env)
    return IngestResult(ok=True, notes=notes, rows=total, batch_ids=batch_ids, run_id=run_id)


def _refuse(conn: sqlite3.Connection, user: User, env: str, vid: int | None, source: str,
            reasons: list[str]) -> IngestResult:
    audit.log(conn, user_id=user.id, user_name=user.name, action="ingest", outcome=audit.OUTCOME_DENIED,
              env=env, version_id=vid, detail=f"データ源 {source}: " + " / ".join(reasons)[:3000])
    return IngestResult(ok=False, reasons=reasons)
