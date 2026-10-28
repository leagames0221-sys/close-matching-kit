"""Reason codes: from a difference or an unmatched row to a journal draft or an adjusting item.

What can take which treatment (the two kinds follow the OJP sheet: items that adjust the bank balance and items that
adjust the book balance; for the first line, Oracle creates an external transaction in cash management and Odoo reconciles the difference with
the account of a counterpart entry (underpayments only) - when the rule names the account, the tool drafts that journal
itself (`draft_difference_journal`); that draft, and that a book-side row may also take a journal, are the author's
decisions, ADR 0006):

  group in 差額の処理待ち (a manual match with a difference, or an automatic one whose rule names no account for the
                           difference)                                       -> journal only
  unmatched row of the BANK side (a fee or interest not in the books yet)   -> journal only
  unmatched row of the BOOK side (a receipt / payment the bank has not seen) -> journal or adjusting item

A timing item (booked, not yet at the bank) is a whole transaction that crosses the period end, so it is carried as an
adjusting item on the bank side of the reconciliation. A difference, or something the bank has and the books lack, is
missing from the books and is put right by a journal. Duplicates and candidates take no reason code: they are first
matched by hand or sorted out as duplicates.

The amount of a journal is d = (bank side total) - (book side total). d > 0: debit the deposit account, credit the
counter account; d < 0 the other way round; d = 0 makes no journal. The conversion from the first release's
`left - right` is the one function `bank_minus_book`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import audit, config, journals, roles, versions
from .roles import User
from .timeutil import now_iso


class ReasonError(Exception):
    """The reason code cannot be used here (the message is shown to the person)."""


class SelfDecision(ReasonError):
    pass


@dataclass
class Applied:
    kind: str          # 仕訳 | 調整項目
    id: int
    number: str        # JE-000001 for a journal, "調整項目 N" for an item
    note: str = ""


def bank_letter(ms: dict[str, Any]) -> str:
    return "L" if ms["bank_side"] == "左" else "R"


def bank_minus_book(ms: dict[str, Any], left_total: int, right_total: int) -> int:
    """The only place that turns the first release's `left - right` into `bank - book`."""
    return left_total - right_total if ms["bank_side"] == "左" else right_total - left_total


def find_match_set(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    for m in cfg["match_sets"]:
        if m["name"] == name:
            return m
    raise ReasonError(f"照合セット {name!r} は設定に無い")


def find_reason(cfg: dict[str, Any], code: str) -> dict[str, Any]:
    for r in cfg["reason_codes"]:
        if r["code"] == code and r["enabled"]:
            return r
    raise ReasonError(f"理由コード {code!r} は設定に無い、または無効")


def journal_lines(ms: dict[str, Any], reason: dict[str, Any], d: int, memo: str) -> list[dict[str, Any]]:
    dep, counter = ms["deposit_account"], reason["counter_account"]
    if d > 0:
        return [{"account": dep, "debit": d, "credit": 0, "memo": memo},
                {"account": counter, "debit": 0, "credit": d, "memo": memo}]
    return [{"account": counter, "debit": -d, "credit": 0, "memo": memo},
            {"account": dep, "debit": 0, "credit": -d, "memo": memo}]


def _deny(conn: sqlite3.Connection, user: User, env: str, vid: int, detail: str) -> None:
    audit.log(conn, user_id=user.id, user_name=user.name, action="reason_apply", outcome=audit.OUTCOME_DENIED,
              env=env, version_id=vid, detail=detail)


def _active_journal(conn: sqlite3.Connection, env: str, txn_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT id, number, status FROM journals WHERE env = ? AND link_txn_id = ?"
                        " AND status IN ('下書き', '承認待ち', '承認済み')", (env, txn_id)).fetchone()


def apply_reason(conn: sqlite3.Connection, user: User, match_set: str, reason_code: str, *,
                 group_id: int | None = None, txn_id: int | None = None, env: str = config.ENV_SANDBOX) -> Applied:
    roles.require(conn, user, "reason_apply", env=env)
    vid, cfg = versions.load_config(conn, env)
    versions.require_current_schema(cfg)
    try:
        ms = find_match_set(cfg, match_set)
        reason = find_reason(cfg, reason_code)
        if (group_id is None) == (txn_id is None):
            raise ReasonError("グループか行のどちらか 1 つを選ぶ")
        if group_id is not None:
            res = _apply_to_group(conn, user, env, vid, cfg, ms, reason, group_id)
        else:
            res = _apply_to_row(conn, user, env, vid, cfg, ms, reason, txn_id)
    except (ReasonError, journals.JournalError) as e:
        _deny(conn, user, env, vid, f"照合セット {match_set}、理由コード {reason_code}: {e}"[:3000])
        raise
    audit.log(conn, user_id=user.id, user_name=user.name, action="reason_apply", outcome=audit.OUTCOME_OK, env=env,
              version_id=vid, detail=f"照合セット {match_set}、理由コード {reason_code} -> {res.kind} {res.number}")
    from . import tasks
    tasks.recheck(conn, env)
    return res


def _apply_to_group(conn, user, env, vid, cfg, ms, reason, group_id) -> Applied:
    g = conn.execute("SELECT * FROM match_groups WHERE id = ? AND env = ? AND match_set = ?",
                     (group_id, env, ms["name"])).fetchone()
    if g is None:
        raise ReasonError(f"グループ {group_id} はこの照合セットに無い")
    if g["kind"] not in ("自動", "手") or g["state"] != "差額の処理待ち":
        raise ReasonError(f"グループ {group_id} は {g['kind']}/{g['state']} で、理由コードを付けられるのは"
                          "「差額の処理待ち」の自動・手の照合だけ")
    if reason["action"] != "仕訳":
        raise ReasonError(f"差額のグループに付けられるのは処理が「仕訳」の理由コードだけ（{reason['code']} は"
                          f"{reason['action']}）")
    d = bank_minus_book(ms, g["left_total"], g["right_total"])
    if d == 0:
        raise ReasonError(f"グループ {group_id} の差額が 0 で、仕訳を作らない")
    bank_dates = [r["booking_date"] for r in conn.execute(
        "SELECT t.booking_date FROM match_group_rows gr JOIN transactions t ON t.id = gr.txn_id"
        " WHERE gr.group_id = ? AND gr.side = ?", (group_id, bank_letter(ms)))]
    entry_date, shifted = journals.effective_date(cfg, max(bank_dates))
    memo = f"{reason['name']}（照合セット {ms['name']} グループ {group_id}）"
    lines = journal_lines(ms, reason, d, memo)
    issues = journals.validate(cfg, entry_date, memo, lines)
    if issues:
        raise journals.JournalInvalid(issues)
    with conn:
        jid = journals._insert(conn, user, env, vid, cfg, entry_date, memo, lines, "理由コード",
                               link_group_id=group_id, match_set=ms["name"], reason_code=reason["code"])
        conn.execute("UPDATE match_groups SET state = '仕訳の承認待ち' WHERE id = ?", (group_id,))
    return Applied("仕訳", jid, f"JE-{jid:06d}", shifted)


def _apply_to_row(conn, user, env, vid, cfg, ms, reason, txn_id) -> Applied:
    row = conn.execute(
        "SELECT m.klass, m.side, t.booking_date, t.amount_minor, t.source FROM match_rows m"
        " JOIN transactions t ON t.id = m.txn_id WHERE m.env = ? AND m.match_set = ? AND m.txn_id = ?",
        (env, ms["name"], txn_id)).fetchone()
    if row is None:
        raise ReasonError(f"行 {txn_id} はこの照合セットに無い（先に照合を実行する）")
    if row["klass"] in ("候補", "重複"):
        raise ReasonError(f"行 {txn_id} は区分「{row['klass']}」で、理由コードは付けられない"
                          "（先に手の照合をするか、重複の処理をする）")
    if row["klass"] != "未照合":
        raise ReasonError(f"行 {txn_id} は区分「{row['klass']}」で、理由コードを付けられるのは未照合の行だけ")
    if _active_journal(conn, env, txn_id) is not None:
        raise ReasonError(f"行 {txn_id} にはもう仕訳がある")
    if conn.execute("SELECT 1 FROM adjusting_items WHERE txn_id = ? AND status IN ('承認待ち', '未解消', '解消')",
                    (txn_id,)).fetchone():
        raise ReasonError(f"行 {txn_id} にはもう調整項目がある")
    on_bank_side = row["side"] == bank_letter(ms)
    if reason["action"] == "調整項目":
        if on_bank_side:
            raise ReasonError("銀行の側の行に調整項目は作れない（帳簿に無い分は仕訳で直す。銀行の誤りは作らない）")
        p = journals.period_of(cfg, row["booking_date"])
        if p is None:
            raise ReasonError(f"行の日付 {row['booking_date']} がどの会計期間にも入らない")
        with conn:
            cur = conn.execute(
                "INSERT INTO adjusting_items(env, match_set, txn_id, reason_code, amount_minor, period, status,"
                " version_id, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, '承認待ち', ?, ?, ?)",
                (env, ms["name"], txn_id, reason["code"], row["amount_minor"], p["name"], vid, user.id, now_iso()))
        iid = int(cur.lastrowid)
        return Applied("調整項目", iid, f"調整項目 {iid}")
    d = row["amount_minor"] if on_bank_side else -row["amount_minor"]
    if d == 0:
        raise ReasonError(f"行 {txn_id} の金額が 0 で、仕訳を作らない")
    entry_date, shifted = journals.effective_date(cfg, row["booking_date"])
    memo = f"{reason['name']}（照合セット {ms['name']} 行 {txn_id}）"
    lines = journal_lines(ms, reason, d, memo)
    issues = journals.validate(cfg, entry_date, memo, lines)
    if issues:
        raise journals.JournalInvalid(issues)
    with conn:
        jid = journals._insert(conn, user, env, vid, cfg, entry_date, memo, lines, "理由コード",
                               link_txn_id=txn_id, match_set=ms["name"], reason_code=reason["code"])
    return Applied("仕訳", jid, f"JE-{jid:06d}", shifted)


def draft_difference_journal(conn: sqlite3.Connection, maker: User, env: str, vid: int, cfg: dict[str, Any],
                             ms: dict[str, Any], group_id: int, counter_account: str, rule_name: str,
                             left_total: int, right_total: int) -> tuple[int | None, str]:
    """The tool's own draft for the difference of an automatic pair (the rule names the account of the difference).
    Inside the caller's transaction. Returns (journal id, note) or (None, why it could not be made); the caller then
    leaves the pair in 差額の処理待ち for a person."""
    d = bank_minus_book(ms, left_total, right_total)
    if d == 0:
        return None, ""
    bank_dates = [r["booking_date"] for r in conn.execute(
        "SELECT t.booking_date FROM match_group_rows gr JOIN transactions t ON t.id = gr.txn_id"
        " WHERE gr.group_id = ? AND gr.side = ?", (group_id, bank_letter(ms)))]
    try:
        entry_date, shifted = journals.effective_date(cfg, max(bank_dates))
    except journals.JournalError as e:
        return None, f"グループ {group_id}: {e}"
    memo = f"{rule_name}の差額（照合セット {ms['name']} グループ {group_id}）"
    lines = journal_lines(ms, {"counter_account": counter_account}, d, memo)
    issues = journals.validate(cfg, entry_date, memo, lines)
    if issues:
        return None, f"グループ {group_id}: " + " / ".join(str(i) for i in issues)
    jid = journals._insert(conn, maker, env, vid, cfg, entry_date, memo, lines, journals.METHOD_TOOL,
                           link_group_id=group_id, match_set=ms["name"])
    return jid, shifted


# ---- adjusting items ------------------------------------------------------------------------------------------

def decide_item(conn: sqlite3.Connection, user: User, item_id: int, approve: bool, note: str = "",
                env: str = config.ENV_SANDBOX) -> str:
    """Approve (-> 未解消, it enters the reconciliation) or reject. Never the person who made it."""
    op = "承認" if approve else "却下"
    roles.require(conn, user, "adjust_decide", env=env)
    item = conn.execute("SELECT * FROM adjusting_items WHERE id = ? AND env = ?", (item_id, env)).fetchone()
    if item is None:
        raise ReasonError(f"調整項目 {item_id} は無い")
    if item["status"] != "承認待ち":
        raise ReasonError(f"調整項目 {item_id} は {item['status']} で、{op}できる状態ではない")
    if item["created_by"] == user.id:
        audit.log(conn, user_id=user.id, user_name=user.name, action="adjust_decide", outcome=audit.OUTCOME_DENIED,
                  env=env, version_id=item["version_id"], detail=f"調整項目 {item_id}: 本人は{op}できない")
        raise SelfDecision(f"自分が作った調整項目は{op}できない（別の承認者が行う）")
    if not approve and not note.strip():
        raise ReasonError("却下には理由の文が要る")
    new = "未解消" if approve else "却下"
    with conn:
        conn.execute("UPDATE adjusting_items SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?"
                     " WHERE id = ?", (new, user.id, now_iso(), note.strip()[:500], item_id))
    audit.log(conn, user_id=user.id, user_name=user.name, action="adjust_decide", outcome=audit.OUTCOME_OK, env=env,
              version_id=item["version_id"], detail=f"調整項目 {item_id}: {op} -> {new}")
    refresh_adjusting_items(conn, env)
    from . import tasks
    tasks.recheck(conn, env)
    return new


RESOLVING_STATES = ("完了", "差額の処理待ち", "仕訳の承認待ち")


def refresh_adjusting_items(conn: sqlite3.Connection, env: str) -> None:
    """解消 when the row has come into a group in 完了 / 差額の処理待ち; back to 未解消 when that group is undone.
    A group in 仕訳の承認待ち is a 差額の処理待ち group whose difference got a journal draft: it is not undone, so the
    item stays 解消 (otherwise the item would be counted again next to the pair that settles it)."""
    rows = conn.execute(
        "SELECT ai.id, ai.status, ai.resolved_group_id, mr.group_id, g.state, g.run_id"
        " FROM adjusting_items ai LEFT JOIN match_rows mr ON mr.env = ai.env AND mr.match_set = ai.match_set"
        " AND mr.txn_id = ai.txn_id LEFT JOIN match_groups g ON g.id = mr.group_id"
        " WHERE ai.env = ? AND ai.status IN ('未解消', '解消')", (env,)).fetchall()
    with conn:
        for r in rows:
            resolved = r["group_id"] is not None and r["state"] in RESOLVING_STATES
            if resolved and (r["status"] == "未解消" or r["resolved_group_id"] != r["group_id"]):
                conn.execute("UPDATE adjusting_items SET status = '解消', resolved_group_id = ?, resolved_run_id = ?"
                             " WHERE id = ?", (r["group_id"], r["run_id"], r["id"]))
            elif not resolved and r["status"] == "解消":
                conn.execute("UPDATE adjusting_items SET status = '未解消', resolved_group_id = NULL,"
                             " resolved_run_id = NULL WHERE id = ?", (r["id"],))


def list_items(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX, status: str | None = None
               ) -> list[sqlite3.Row]:
    roles.require(conn, user, "view", env=env)
    where, args = ["ai.env = ?"], [env]
    if status:
        where.append("ai.status = ?")
        args.append(status)
    return conn.execute(
        "SELECT ai.*, t.booking_date, t.reference, t.memo, t.source, cu.name AS created_name, du.name AS decided_name"
        " FROM adjusting_items ai JOIN transactions t ON t.id = ai.txn_id"
        " LEFT JOIN users cu ON cu.id = ai.created_by LEFT JOIN users du ON du.id = ai.decided_by"
        f" WHERE {' AND '.join(where)} ORDER BY ai.id DESC", args).fetchall()
