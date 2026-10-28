"""Period-end balance reconciliation (the bank reconciliation statement).

Shape: the "Bank Reconciliation Statement" of the US Department of Justice, Office of Justice Programs (Bank
Reconciliation Guide Sheet, Exhibit 1). Only the shape and its example figures are used.

    bank balance at the period end
      + deposits in the books but not yet at the bank      (入金)
      - payments in the books but not yet at the bank      (出金)
    = adjusted bank balance
    book balance at the period end
      +/- movements of the bank that the books do not have yet (approved journals on the deposit account, and bank
          rows the books booked only after the period end)
    = adjusted book balance
    difference (adjusted bank - adjusted book) = the unidentified difference

The statement is built "as of the period end E": a row dated after E did not exist yet. So a book row dated on or
before E whose bank partner is dated after E is a timing item, however the matching later paired them, and what
happens after E never changes the statement of that period (it can only turn an unexplained row into an explained one).
"""
from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import audit, config, roles
from .money import fmt_amount
from .reasons import bank_letter, bank_minus_book, find_match_set
from .roles import User


@dataclass
class Line:
    section: str            # 銀行の側 | 帳簿の側
    kind: str               # 調整項目 | 時期のずれ | 仕訳 | 翌期に記帳
    amount: int
    date: str
    ref: str = ""
    memo: str = ""
    reason_code: str = ""
    age_days: int | None = None
    txn_id: int | None = None
    item_id: int | None = None
    journal: str = ""
    status: str = ""


@dataclass
class Statement:
    match_set: str
    period: str
    period_end: str
    known: bool = False
    why_unknown: str = ""
    bank_end: int | None = None
    book_end: int | None = None
    transit_in: int = 0          # 帳簿にあって銀行にまだ無い入金
    outstanding_out: int = 0     # 帳簿にあって銀行にまだ無い出金 (positive number)
    book_movements: int = 0      # 帳簿にまだ無い銀行の動き (signed)
    adjusted_bank: int | None = None
    adjusted_book: int | None = None
    difference: int | None = None
    lines: list[Line] = field(default_factory=list)
    unprocessed_book: list[Line] = field(default_factory=list)
    unprocessed_bank: list[Line] = field(default_factory=list)
    unprocessed_groups: list[Line] = field(default_factory=list)   # differences of pairs without a journal yet
    notes: list[str] = field(default_factory=list)


def assemble(bank_end: int, book_end: int, bank_side_items: list[int], book_movements: list[int]) -> dict[str, int]:
    """The arithmetic of the statement. `bank_side_items` are signed amounts of book rows the bank has not seen;
    `book_movements` are signed movements of the bank that the books do not have yet."""
    transit_in = sum(a for a in bank_side_items if a > 0)
    outstanding = -sum(a for a in bank_side_items if a < 0)
    adj_bank = bank_end + transit_in - outstanding
    adj_book = book_end + sum(book_movements)
    return {"transit_in": transit_in, "outstanding_out": outstanding, "adjusted_bank": adj_bank,
            "adjusted_book": adj_book, "book_movements": sum(book_movements), "difference": adj_bank - adj_book}


def period_end_balance(conn: sqlite3.Connection, env: str, source: str, start: str, end: str, mult: int = 1
                       ) -> tuple[int | None, str]:
    """Balance at the end of day `end`: the opening balance of the bundle that holds `end` (or, if none does, the last
    bundle that ends before it) plus the rows of that bundle dated on or before `end`. (None, reason) when it cannot
    be known: no bundle touches the period, or a CSV balance column disagrees with this calculation. `mult` is the
    multiplier of the source (the amounts in the reason are written like the screens write them)."""
    batches = conn.execute(
        "SELECT * FROM batches WHERE env = ? AND source = ? AND opening_minor IS NOT NULL"
        " ORDER BY date_from, id", (env, source)).fetchall()
    if not any(b["date_from"] <= end and b["date_to"] >= start for b in batches):
        return None, f"{source} に期間 {start}〜{end} の束が無いので残高が分からない"
    holding = [b for b in batches if b["date_from"] <= end <= b["date_to"]]
    chosen = holding[-1] if holding else None
    if chosen is None:
        before = [b for b in batches if b["date_to"] < end]
        chosen = before[-1] if before else None
    if chosen is None:
        return None, f"{source} に {end} までの束が無いので残高が分からない"
    total = conn.execute("SELECT COALESCE(SUM(amount_minor), 0) FROM transactions WHERE batch_id = ?"
                         " AND booking_date <= ?", (chosen["id"], end)).fetchone()[0]
    computed = chosen["opening_minor"] + total
    last = conn.execute("SELECT balance_minor FROM transactions WHERE batch_id = ? AND booking_date <= ?"
                        " AND balance_minor IS NOT NULL ORDER BY src_row DESC LIMIT 1",
                        (chosen["id"], end)).fetchone()
    if last is not None and last["balance_minor"] != computed:
        return None, (f"{source}: CSV の行の残高 {fmt_amount(last['balance_minor'], mult)} が、"
                      f"取引前残高 {fmt_amount(chosen['opening_minor'], mult)}"
                      f" + 日付が {end} までの行の合計 {fmt_amount(total, mult)} = {fmt_amount(computed, mult)} と合わない")
    return computed, ""


def _period(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    for p in cfg["periods"]:
        if p["name"] == name:
            return p
    raise ValueError(f"会計期間 {name!r} は設定に無い")


def _age(period_name: str, cfg: dict[str, Any], today: date) -> int:
    end = date.fromisoformat(_period(cfg, period_name)["end"])
    return max((today - end).days, 0)


def _group_difference(st: Statement, gid: int, g: dict[str, Any], bank_l: str, end: str,
                      approved_for_group: dict[int, list[sqlite3.Row]],
                      waiting_for_group: dict[int, sqlite3.Row] | None = None) -> None:
    """A pair with a difference (bank - book) whose bank side is all on or before the period end: the difference is
    this period's (its journal is dated with the latest bank row). Without an approved journal dated on or before the
    period end it is part of the unidentified difference, so it is listed as unprocessed."""
    banks = g[bank_l]
    if g["d"] == 0 or not banks or not all(d <= end for d, _a in banks):
        return
    if any(j["entry_date"] <= end for j in approved_for_group.get(gid, [])):
        return
    waiting = (waiting_for_group or {}).get(gid)
    status = g["state"] + (f"（仕訳 {waiting['number']} は{waiting['status']}）"
                           if waiting is not None and g["state"] != "仕訳の承認待ち" else "")
    st.unprocessed_groups.append(Line("帳簿の側", "未処理", g["d"], max(d for d, _a in banks), f"グループ {gid}",
                                      "照合の組の差額（銀行の側 − 帳簿の側）", status=status))


def statement(conn: sqlite3.Connection, env: str, cfg: dict[str, Any], match_set: str, period_name: str,
              today: date | None = None) -> Statement:
    ms = find_match_set(cfg, match_set)
    p = _period(cfg, period_name)
    start, end = p["start"], p["end"]
    today = today or date.today()
    st = Statement(match_set=match_set, period=period_name, period_end=end)
    if not ms["reconcile"]:
        st.why_unknown = f"照合セット {match_set} は「残高一致に使う」が「いいえ」"
        return st
    bank_src, book_src = (ms["left"], ms["right"]) if ms["bank_side"] == "左" else (ms["right"], ms["left"])
    bank_l = bank_letter(ms)
    side_of = {ms["left"]: "L", ms["right"]: "R"}

    # ---- rows as of the period end, their matching state, and the groups they sit in
    rows = conn.execute(
        "SELECT t.id, t.source, t.booking_date, t.amount_minor, t.reference, t.memo, m.klass, m.group_id"
        " FROM transactions t LEFT JOIN match_rows m ON m.env = t.env AND m.match_set = ? AND m.txn_id = t.id"
        " WHERE t.env = ? AND t.source IN (?, ?) AND t.booking_date <= ? ORDER BY t.booking_date, t.id",
        (match_set, env, ms["left"], ms["right"], end)).fetchall()
    groups: dict[int, dict[str, Any]] = {}
    for g in conn.execute("SELECT id, kind, state, left_total, right_total FROM match_groups"
                          " WHERE env = ? AND match_set = ?", (env, match_set)):
        groups[g["id"]] = {"state": g["state"], "kind": g["kind"], "L": [], "R": [],
                           "d": bank_minus_book(ms, g["left_total"], g["right_total"])}
    for r in conn.execute(
            "SELECT gr.group_id, gr.side, t.booking_date, t.amount_minor FROM match_group_rows gr JOIN match_groups g"
            " ON g.id = gr.group_id JOIN transactions t ON t.id = gr.txn_id WHERE g.env = ? AND g.match_set = ?",
            (env, match_set)):
        groups[r["group_id"]][r["side"]].append((r["booking_date"], r["amount_minor"]))
    items = {i["txn_id"]: i for i in conn.execute(
        "SELECT * FROM adjusting_items WHERE env = ? AND match_set = ? AND status = '未解消'", (env, match_set))}
    pending_journal = {r["link_txn_id"] for r in conn.execute(
        "SELECT link_txn_id FROM journals WHERE env = ? AND link_txn_id IS NOT NULL AND status IN ('下書き', '承認待ち')",
        (env,))}
    # approved journals by what they settle; a journal dated after the period end settles nothing "as of" that end
    approved_for_row: dict[int, sqlite3.Row] = {}
    approved_for_group: dict[int, list[sqlite3.Row]] = {}
    for j in conn.execute("SELECT number, entry_date, link_txn_id, link_group_id FROM journals"
                          " WHERE env = ? AND status = '承認済み'", (env,)):
        if j["link_txn_id"] is not None:
            approved_for_row[j["link_txn_id"]] = j
        if j["link_group_id"] is not None:
            approved_for_group.setdefault(j["link_group_id"], []).append(j)
    waiting_for_group = {j["link_group_id"]: j for j in conn.execute(
        "SELECT number, status, link_group_id FROM journals WHERE env = ? AND link_group_id IS NOT NULL"
        " AND status IN ('下書き', '承認待ち') ORDER BY id", (env,))}
    starts = {q["name"]: q["start"] for q in cfg["periods"]}
    book_l = "R" if bank_l == "L" else "L"

    bank_items: list[int] = []
    movements: list[int] = []
    # A group waiting for the journal of its difference is still a pair: "仕訳の承認待ち" follows "差額の処理待ち"
    # and must not turn a timing pair back into unprocessed rows (that would let a later event change this period).
    done_states = ("完了", "差額の処理待ち", "仕訳の承認待ち")
    seen_groups: set[int] = set()   # groups whose next-period booking is already counted
    diff_seen: set[int] = set()     # groups whose difference is already looked at
    for r in rows:
        side = side_of[r["source"]]
        is_bank = side == bank_l
        g = groups.get(r["group_id"]) if r["group_id"] is not None else None
        line = Line("銀行の側" if not is_bank else "帳簿の側", "", r["amount_minor"], r["booking_date"], r["reference"],
                    r["memo"], txn_id=r["id"])
        if g is not None and g["state"] in done_states:
            partners = g[book_l if is_bank else bank_l]
            if partners and all(d > end for d, _a in partners):
                if not is_bank:        # booked here, the bank shows it after the period end
                    line.kind = "時期のずれ"
                    st.lines.append(line)
                    bank_items.append(r["amount_minor"])
                elif r["group_id"] not in seen_groups:
                    # at the bank, the books booked it after the period end. What the books will add is the book
                    # side of the pair; a difference of the pair is the business of its journal (dated with the
                    # bank row, so on or before this period end) and is counted with the journals below.
                    seen_groups.add(r["group_id"])
                    banks = g[bank_l]
                    if all(d <= end for d, _a in banks):
                        line.amount = sum(a for _d, a in partners)
                        line.memo = (line.memo + f"（帳簿の側の合計。グループ {r['group_id']}）").strip()
                    else:              # part of the bank side is after the period end too: the rows themselves
                        line.amount = sum(a for d, a in banks if d <= end)
                    line.kind = "翌期に記帳"
                    st.lines.append(line)
                    movements.append(line.amount)
            if r["group_id"] not in diff_seen:
                diff_seen.add(r["group_id"])
                _group_difference(st, r["group_id"], g, bank_l, end, approved_for_group, waiting_for_group)
            continue                   # paired: nothing else to adjust
        if r["klass"] == "処理済み":
            j = approved_for_row.get(r["id"])
            if j is not None and j["entry_date"] <= end:
                continue               # its journal is counted below
            line.kind = "未処理"
            line.status = "処理済み（仕訳 " + (j["number"] if j else "?") + " の日付 " + (
                j["entry_date"] if j else "?") + " は期末日より後）"
            (st.unprocessed_bank if is_bank else st.unprocessed_book).append(line)
            continue
        if r["klass"] == "転記済み":
            continue                   # the books already hold the posted journal
        item = items.get(r["id"])
        if item is not None and not is_bank and starts.get(item["period"], "9999") <= start:
            line.kind = "調整項目"
            line.item_id, line.reason_code, line.status = item["id"], item["reason_code"], item["status"]
            line.age_days = _age(item["period"], cfg, today)
            st.lines.append(line)
            bank_items.append(r["amount_minor"])
            continue
        line.kind = "未処理"
        line.status = r["klass"] or "未照合"
        if r["id"] in pending_journal:
            line.status += "（仕訳の承認待ち）"
        elif g is not None:
            line.status += f"（グループ {r['group_id']}: {g['state']}）"
        (st.unprocessed_bank if is_bank else st.unprocessed_book).append(line)

    # ---- approved journals on the deposit account, dated on or before the period end
    posted = {r["journal_id"] for r in conn.execute(
        "SELECT pl.journal_id FROM posted_links pl JOIN transactions t ON t.id = pl.txn_id"
        " WHERE pl.env = ? AND t.booking_date <= ?", (env, end))}
    for j in conn.execute(
            "SELECT j.id, j.number, j.entry_date, j.reason_code, j.memo, SUM(l.debit - l.credit) AS net"
            " FROM journals j JOIN journal_lines l ON l.journal_id = j.id WHERE j.env = ? AND j.status = '承認済み'"
            " AND j.entry_date <= ? AND l.account_code = ? GROUP BY j.id ORDER BY j.entry_date, j.id",
            (env, end, ms["deposit_account"])):
        if j["id"] in posted or j["net"] == 0:
            continue                   # already inside the book balance, or no effect on the deposit account
        st.lines.append(Line("帳簿の側", "仕訳", j["net"], j["entry_date"], j["number"], j["memo"],
                             reason_code=j["reason_code"], journal=j["number"], status="承認済み"))
        movements.append(j["net"])

    # ---- balances
    mults = {s["name"]: s["multiplier"] for s in cfg["sources"]}
    st.bank_end, why_bank = period_end_balance(conn, env, bank_src, start, end, mults.get(bank_src, 1))
    st.book_end, why_book = period_end_balance(conn, env, book_src, start, end, mults.get(book_src, 1))
    if st.bank_end is None or st.book_end is None:
        st.why_unknown = "残高が分からない: " + " / ".join(w for w in (why_bank, why_book) if w)
        return st
    a = assemble(st.bank_end, st.book_end, bank_items, movements)
    st.known = True
    st.transit_in, st.outstanding_out = a["transit_in"], a["outstanding_out"]
    st.book_movements = a["book_movements"]
    st.adjusted_bank, st.adjusted_book, st.difference = a["adjusted_bank"], a["adjusted_book"], a["difference"]
    return st


def statement_for(conn: sqlite3.Connection, user: User, match_set: str, period_name: str,
                  env: str = config.ENV_SANDBOX, today: date | None = None) -> Statement:
    from . import versions
    roles.require(conn, user, "view", env=env)
    _vid, cfg = versions.load_config(conn, env)
    versions.require_current_schema(cfg)
    return statement(conn, env, cfg, match_set, period_name, today)


CSV_HEADER = ["区分", "種類", "日付", "金額", "参照・仕訳番号", "摘要", "理由コード", "経過の日数", "状態"]


def to_csv(st: Statement) -> str:
    """The statement as a CSV: totals first, then one line per adjusting item / journal / timing row."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    cell = audit._cell
    w.writerow(["照合セット", cell(st.match_set), "期間", cell(st.period), "期末日", st.period_end])
    if not st.known:
        w.writerow(["残高が分からない", cell(st.why_unknown)])
    else:
        for label, v in (("銀行の期末残高", st.bank_end), ("帳簿にあって銀行にまだ無い入金", st.transit_in),
                         ("帳簿にあって銀行にまだ無い出金", -st.outstanding_out),
                         ("調整後の銀行残高", st.adjusted_bank), ("帳簿の期末残高", st.book_end),
                         ("帳簿にまだ無い銀行の動き", st.book_movements), ("調整後の帳簿残高", st.adjusted_book),
                         ("原因の分からない差", st.difference)):
            w.writerow([label, v])
    w.writerow([])
    w.writerow(CSV_HEADER)
    for ln in st.lines + st.unprocessed_book + st.unprocessed_bank + st.unprocessed_groups:
        w.writerow([cell(v) for v in (ln.section, ln.kind, ln.date, ln.amount, ln.journal or ln.ref, ln.memo,
                                      ln.reason_code, "" if ln.age_days is None else ln.age_days, ln.status)])
    return out.getvalue()
