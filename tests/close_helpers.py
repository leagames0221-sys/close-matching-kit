"""Helpers shared by the tests of the second release (and, with `env`, of the change requests)."""
from __future__ import annotations

from datetime import date

from close_kit import balance, changes, ingest, journals, matchrun, reasons, versions
from tools import make_samples

MS = "預金照合"
PROD = "production"


def txn(conn, ref: str, source: str | None = None, memo: str | None = None, env: str = "sandbox") -> int:
    q, a = "SELECT id FROM transactions WHERE reference = ? AND env = ?", [ref, env]
    if source:
        q += " AND source = ?"
        a.append(source)
    if memo:
        q += " AND memo LIKE ?"
        a.append(f"%{memo}%")
    rows = conn.execute(q + " ORDER BY id", a).fetchall()
    assert len(rows) >= 1, (ref, source)
    return rows[0]["id"]


def group_with(conn, ref: str, env: str = "sandbox") -> int:
    t = txn(conn, ref, "帳簿", env=env)
    return conn.execute("SELECT group_id FROM match_rows WHERE txn_id = ?", (t,)).fetchone()["group_id"]


def bank_interest_row(conn, env: str = "sandbox") -> int:
    return conn.execute("SELECT id FROM transactions WHERE source = '銀行' AND reference = '' AND amount_minor = 37"
                        " AND env = ?", (env,)).fetchone()["id"]


def statement(conn, period: str = "2026-10", ms: str = MS, today: date = date(2026, 11, 1), env: str = "sandbox"):
    _vid, cfg = versions.load_config(conn, env)
    return balance.statement(conn, env, cfg, ms, period, today)


def approve_journal(conn, maker, approver, jid: int, env: str = "sandbox") -> None:
    journals.submit(conn, maker, jid, env)
    assert journals.decide(conn, approver, jid, True, "", env) == "承認済み"


def walk_the_path(conn, u, with_item_for_payment: bool = False, env: str = "sandbox"):
    """Fee journal, interest journal, an adjusting item for the month-end receipt (the sample's path)."""
    fee = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=group_with(conn, "INV-2610-003", env), env=env)
    interest = reasons.apply_reason(conn, u.maker, MS, "R03", txn_id=bank_interest_row(conn, env), env=env)
    item = reasons.apply_reason(conn, u.maker, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿", env=env), env=env)
    approve_journal(conn, u.maker, u.approver, fee.id, env)
    approve_journal(conn, u.maker, u.approver, interest.id, env)
    assert reasons.decide_item(conn, u.approver, item.id, True, "", env) == "未解消"
    out = {"fee": fee, "interest": interest, "item": item}
    if with_item_for_payment:
        out["pay"] = reasons.apply_reason(conn, u.maker, MS, "R05", txn_id=txn(conn, "PAY-2610-02", "帳簿", env=env),
                                          env=env)
        reasons.decide_item(conn, u.approver, out["pay"].id, True, "", env)
    return out


def import_november(conn, admin, ledger: bytes | None = None, env: str = "sandbox"):
    assert ingest.ingest(conn, admin, "銀行", "b2.txt", make_samples.close_bank_nov(), env).ok
    assert ingest.ingest(conn, admin, "帳簿", "l2.csv", ledger or make_samples.close_ledger_nov(), env).ok
    matchrun.run_match(conn, admin, MS, env)


def put_in_production(conn, workbook_bytes: bytes, requester, pre_approver, approver, result_admin=None, *,
                      title: str = "設定の版を本番へ", reason: str = "試験の準備", confirm: bool = True) -> int:
    """Put a workbook into production the only way there is: loaded into the sandbox, then a change request through
    every step (draft, put forward, pre-approval - the test runs by itself -, the result approved by an approver and an
    administrator, applied, confirmed by the requester). Returns the version id now in production."""
    from conftest import load_workbook_into
    vid = load_workbook_into(conn, requester, workbook_bytes)
    rid = changes.create_draft(conn, requester, title=title, to_version_id=vid, justification=reason)
    changes.submit(conn, requester, rid)
    changes.pre_decide(conn, pre_approver, rid, True)
    changes.decide_result(conn, approver, rid, True)
    changes.decide_result(conn, result_admin or pre_approver, rid, True)
    changes.apply(conn, result_admin or pre_approver, rid)
    if confirm:
        changes.confirm(conn, requester, rid, True)
    assert versions.env_version_id(conn, PROD) == vid
    return vid
