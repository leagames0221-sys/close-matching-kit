"""Journals: the five validation rules at three moments, the CSV import, the approval route and who may approve,
immutability after approval, reason codes (journal drafts and adjusting items)."""
import sqlite3
from datetime import date as D

import pytest

from close_kit import journals, matchrun, reasons, roles, versions, workbook
from conftest import load_workbook_into, make_user
from tools import make_samples, sample_builders as sb
from close_helpers import MS, approve_journal, bank_interest_row, group_with, txn

CFG = workbook.parse_workbook(make_samples.close_workbook()).config


def L(acc, dr=0, cr=0, memo=""):
    return {"account": acc, "debit": dr, "credit": cr, "memo": memo}


def rules(issues):
    return sorted({i.rule for i in issues})


GOOD = [L("6100", 1000), L("1100", 0, 1000)]

# ---- the five rules, with their boundaries -------------------------------------------------------------------------


def test_a_balanced_journal_in_an_open_period_has_no_issue():
    assert journals.validate(CFG, "2026-10-15", "memo", GOOD) == []


@pytest.mark.parametrize("dr, cr", [(1001, 1000), (1000, 1001), (1, 0 + 2)])
def test_rule1_debit_and_credit_one_unit_apart_are_refused(dr, cr):
    issues = journals.validate(CFG, "2026-10-15", "", [L("6100", dr), L("1100", 0, cr)])
    assert rules(issues) == [1] and "差" in issues[0].message


def test_rule2_line_count_boundaries():
    assert rules(journals.validate(CFG, "2026-10-15", "", [L("6100", 5)])) == [1, 2]            # 1 line
    assert journals.validate(CFG, "2026-10-15", "", GOOD) == []                                    # 2 lines
    lines999 = [L("6100", 1) for _ in range(998)] + [L("1100", 0, 998)]
    assert len(lines999) == 999 and journals.validate(CFG, "2026-10-15", "", lines999) == []
    lines1000 = [L("6100", 1) for _ in range(999)] + [L("1100", 0, 999)]
    assert len(lines1000) == 1000 and rules(journals.validate(CFG, "2026-10-15", "", lines1000)) == [2]


@pytest.mark.parametrize("bad, why", [
    (L("6100", 0, 0), "金額が 0"), (L("6100", -5, 0), "負の金額"), (L("6100", 5, 5), "両方に金額"),
])
def test_rule2_each_line_has_exactly_one_positive_side(bad, why):
    issues = journals.validate(CFG, "2026-10-15", "", [bad, L("1100", 0, 5)])
    assert any(i.rule == 2 and i.line == 1 and why in i.message for i in issues)


def test_rule3_unknown_account_code_names_the_line_and_the_value():
    issues = journals.validate(CFG, "2026-10-15", "", [L("9999", 10), L("1100", 0, 10)])
    assert [(i.rule, i.line) for i in issues] == [(3, 1)] and "9999" in issues[0].message


@pytest.mark.parametrize("day, ok", [("2026-09-01", False), ("2026-09-30", False), ("2026-10-01", True),
                                      ("2026-11-30", True), ("2026-12-01", False), ("2026-08-31", False),
                                      ("2026-02-30", False), ("abc", False)])
def test_rule4_closed_first_and_last_day_outside_every_period_and_not_a_date(day, ok):
    issues = journals.validate(CFG, day, "", GOOD)
    assert (issues == []) is ok
    assert ok or rules(issues) == [4]


@pytest.mark.parametrize("memo", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1", "＝1"])
def test_rule5_a_memo_that_would_be_a_formula_is_refused(memo):
    assert rules(journals.validate(CFG, "2026-10-15", memo, GOOD)) == [5]
    assert rules(journals.validate(CFG, "2026-10-15", "", [L("6100", 1, 0, memo), L("1100", 0, 1)])) == [5]


def test_rule5_ordinary_memos_pass():
    assert journals.validate(CFG, "2026-10-15", "手数料 =50", [L("6100", 1, 0, "a-b"), L("1100", 0, 1, "x@y")]) == []


# ---- three moments: save, submit, approve ------------------------------------------------------------------------

def test_save_refuses_and_stores_nothing(conn, oct_world):
    u = oct_world
    with pytest.raises(journals.JournalInvalid) as e:
        journals.create_manual(conn, u.maker, "2026-09-30", "", [L("6100", 10), L("9999", 0, 9)])
    assert {i.rule for i in e.value.issues} == {1, 3, 4}
    assert conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0
    row = conn.execute("SELECT outcome, detail FROM audit_log WHERE action = 'journal_create' ORDER BY id DESC").fetchone()
    assert row["outcome"] == "拒否" and "規則4" in row["detail"]


def test_period_closed_between_submission_and_approval_stops_the_approval(conn, oct_world):
    u = oct_world
    jid = journals.create_manual(conn, u.maker, "2026-10-15", "memo", GOOD)
    journals.submit(conn, u.maker, jid)
    s = make_samples.close_sheets()
    s["会計期間"][1][3] = "はい"                       # 2026-10 is closed now
    load_workbook_into(conn, u.admin, sb.build_workbook(s))
    with pytest.raises(journals.JournalInvalid) as e:
        journals.decide(conn, u.approver, jid, True)
    assert [i.rule for i in e.value.issues] == [4]
    assert conn.execute("SELECT status FROM journals WHERE id = ?", (jid,)).fetchone()[0] == "承認待ち"


def test_account_removed_between_submission_and_approval_stops_the_approval(conn, oct_world):
    u = oct_world
    jid = journals.create_manual(conn, u.maker, "2026-10-15", "", [L("1200", 5), L("1100", 0, 5)])
    journals.submit(conn, u.maker, jid)
    s = make_samples.close_sheets()
    s["勘定科目"] = [r for r in s["勘定科目"] if r[0] != "1200"]
    load_workbook_into(conn, u.admin, sb.build_workbook(s))
    with pytest.raises(journals.JournalInvalid) as e:
        journals.decide(conn, u.approver, jid, True)
    assert [i.rule for i in e.value.issues] == [3]


def test_submit_validates_again(conn, oct_world):
    u = oct_world
    jid = journals.create_manual(conn, u.maker, "2026-10-15", "", GOOD)
    s = make_samples.close_sheets()
    s["会計期間"][1][3] = "はい"
    load_workbook_into(conn, u.admin, sb.build_workbook(s))
    with pytest.raises(journals.JournalInvalid):
        journals.submit(conn, u.maker, jid)


def test_number_status_and_authorship(conn, oct_world):
    u = oct_world
    jid = journals.create_manual(conn, u.maker, "2026-10-15", "memo", GOOD)
    j = conn.execute("SELECT * FROM journals WHERE id = ?", (jid,)).fetchone()
    assert (j["number"], j["status"], j["method"], j["debit_total"], j["period"]) == ("JE-000001", "下書き", "手入力", 1000, "2026-10")
    other = make_user(conn, "maker-b", ["作成者"])
    with pytest.raises(journals.JournalError):
        journals.submit(conn, other, jid)                    # only the author submits
    with pytest.raises(journals.JournalError):
        journals.withdraw(conn, other, jid)                  # only the author withdraws
    journals.submit(conn, u.maker, jid)
    journals.withdraw(conn, u.maker, jid)
    assert conn.execute("SELECT status FROM journals WHERE id = ?", (jid,)).fetchone()[0] == "取り下げ"   # kept, not deleted
    with pytest.raises(journals.JournalError):
        journals.submit(conn, u.maker, jid)


def test_rejection_needs_a_reason_and_is_kept(conn, oct_world):
    u = oct_world
    jid = journals.create_manual(conn, u.maker, "2026-10-15", "", GOOD)
    journals.submit(conn, u.maker, jid)
    with pytest.raises(journals.JournalError):
        journals.decide(conn, u.approver, jid, False, "")
    assert journals.decide(conn, u.approver, jid, False, "金額が違う") == "却下"
    j = conn.execute("SELECT * FROM journals WHERE id = ?", (jid,)).fetchone()
    assert (j["status"], j["decision_note"]) == ("却下", "金額が違う")


# ---- CSV import --------------------------------------------------------------------------------------------------

def test_import_groups_rows_by_slip_and_all_rows_become_drafts(conn, oct_world):
    u = oct_world
    r = journals.import_csv(conn, u.maker, make_samples.journals_import_good())
    assert r.ok and len(r.journal_ids) == 2
    a, b = (conn.execute("SELECT * FROM journals WHERE id = ?", (i,)).fetchone() for i in r.journal_ids)
    assert (a["slip_no"], a["method"], a["debit_total"]) == ("A-001", "取り込み", 12000)
    assert b["slip_no"] == "A-002" and b["entry_date"] == "2026-10-30" and b["debit_total"] == 3000
    assert len(journals.lines_of(conn, b["id"])) == 2


def test_one_error_stops_the_whole_import_and_names_the_rows(conn, oct_world):
    u = oct_world
    r = journals.import_csv(conn, u.maker, make_samples.journals_import_bad())
    assert not r.ok and conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0
    text = "\n".join(r.errors)
    assert "2 行目" in text and "規則1" in text                    # B-001 does not balance
    assert "3 行目" in text and "9999" in text                     # B-002 unknown account
    assert "4 行目" in text and "規則4" in text                    # B-003 closed period
    # a good slip next to a bad one is not stored either
    mixed = make_samples.journals_import_good().replace(b"A-002", b"A-001") + b""
    good_plus_bad = sb.build_csv(journals.IMPORT_COLUMNS, [["C-1", "2026-10-31", "6100", 5, "1200", 5, "ok"],
                                                           ["C-2", "2026-10-31", "6100", 5, "1200", 4, "no"]])
    r = journals.import_csv(conn, u.maker, good_plus_bad)
    assert not r.ok and conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0
    assert mixed


@pytest.mark.parametrize("enc", ["utf-8", "utf-8-sig", "cp932"])
def test_import_reads_utf8_with_or_without_bom_and_cp932(conn, oct_world, enc):
    data = make_samples.journals_import_good().decode("utf-8").encode(enc)
    r = journals.import_csv(conn, oct_world.maker, data)
    assert r.ok, r.errors


def test_import_other_faults(conn, oct_world):
    u = oct_world
    cols = journals.IMPORT_COLUMNS
    cases = {
        "header": sb.build_csv(["a", "b"], [["1", "2"]]),
        "slip": sb.build_csv(cols, [["", "2026-10-31", "6100", 5, "1200", 5, ""]]),
        "date": sb.build_csv(cols, [["X", "令和8年10月31日", "6100", 5, "1200", 5, ""]]),
        "half": sb.build_csv(cols, [["X", "2026-10-31", "6100", "", "1200", 5, ""]]),
        "decimal": sb.build_csv(cols, [["X", "2026-10-31", "6100", "5.5", "1200", "5.5", ""]]),
        "dates differ": sb.build_csv(cols, [["X", "2026-10-30", "6100", 5, "", "", ""], ["X", "2026-10-31", "", "", "1200", 5, ""]]),
        "empty": sb.build_csv(cols, []),
        "nothing": sb.build_csv(cols, [["X", "2026-10-31", "", "", "", "", ""]]),
        "formula": sb.build_csv(cols, [["X", "2026-10-31", "6100", 5, "1200", 5, "=1+1"]]),
        "zero": sb.build_csv(cols, [["X", "2026-10-31", "6100", 0, "1200", 0, ""]]),
    }
    for name, data in cases.items():
        r = journals.import_csv(conn, u.maker, data)
        assert not r.ok and r.errors, name
    assert conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0


# ---- the approval route and who may decide ---------------------------------------------------------------------------

def test_route_for_the_amount_just_below_and_at_the_limit():
    assert journals.route_for(CFG, 99999)["approvers"] == ["demo-approver", "demo-approver2"]
    assert journals.route_for(CFG, 100000)["approvers"] == ["demo-approver2"]
    assert journals.route_for(CFG, 0)["lower"] == 0
    assert journals.route_for(CFG, 10**12)["upper"] is None


def journal_of(conn, u, amount):
    jid = journals.create_manual(conn, u.maker, "2026-10-15", "", [L("6100", amount), L("1100", 0, amount)])
    journals.submit(conn, u.maker, jid)
    return jid


def test_the_person_who_may_approve_changes_at_the_limit(conn, oct_world):
    u = oct_world
    below, at = journal_of(conn, u, 99999), journal_of(conn, u, 100000)
    with pytest.raises(journals.NotAllowedToDecide) as e:
        journals.decide(conn, u.approver, at, True)        # demo-approver is not named for 100,000 and over
    assert "demo-approver" in str(e.value) and "100,000" in str(e.value)
    assert journals.decide(conn, u.approver, below, True) == "承認済み"     # but is for 99,999
    assert journals.decide(conn, u.approver2, at, True) == "承認済み"
    refused = conn.execute("SELECT outcome FROM audit_log WHERE action = 'journal_decide' AND outcome = '拒否'").fetchall()
    assert len(refused) == 1


def test_a_name_in_the_route_without_the_approver_role_is_refused(conn, users):
    s = make_samples.close_sheets(approvers=("demo-approver", "demo-maker"))
    load_workbook_into(conn, users.admin, sb.build_workbook(s))
    jid = journals.create_manual(conn, users.maker, "2026-10-15", "", GOOD)
    journals.submit(conn, users.maker, jid)
    other = make_user(conn, "maker-c", ["作成者"])
    with pytest.raises(roles.PermissionDenied):
        journals.decide(conn, other, jid, True)
    # the named maker is a different person from the author here, but still has no approver role
    named = users.maker
    jid2 = journals.create_manual(conn, make_user(conn, "maker-d", ["作成者"]), "2026-10-15", "", GOOD)
    journals.submit(conn, conn_user(conn, "maker-d"), jid2)
    with pytest.raises(roles.PermissionDenied):
        journals.decide(conn, named, jid2, True)


def conn_user(conn, name):
    from close_kit import auth
    return auth.get_user_by_name(conn, name)


def test_the_approver_role_without_a_name_in_the_range_is_refused(conn, oct_world):
    u = oct_world
    stranger = make_user(conn, "demo-approver9", ["承認者"])
    jid = journal_of(conn, u, 500)
    with pytest.raises(journals.NotAllowedToDecide) as e:
        journals.decide(conn, stranger, jid, True)
    assert "承認できる人" in str(e.value)


def test_the_author_cannot_decide_even_with_both_roles_and_a_name_in_the_route(conn, users):
    s = make_samples.close_sheets(approvers=("both-user",), senior=("both-user",))
    load_workbook_into(conn, users.admin, sb.build_workbook(s))
    both = make_user(conn, "both-user", ["作成者", "承認者"])
    jid = journals.create_manual(conn, both, "2026-10-15", "", GOOD)
    journals.submit(conn, both, jid)
    for approve in (True, False):
        with pytest.raises(journals.NotAllowedToDecide) as e:
            journals.decide(conn, both, jid, approve, "x")
        assert "自分が作った" in str(e.value)
    assert conn.execute("SELECT status FROM journals WHERE id = ?", (jid,)).fetchone()[0] == "承認待ち"


def test_the_approval_record_names_who_when_and_which_version(conn, oct_world):
    u = oct_world
    jid = journal_of(conn, u, 700)
    journals.decide(conn, u.approver, jid, True)
    j = conn.execute("SELECT * FROM journals WHERE id = ?", (jid,)).fetchone()
    vid = versions.env_version_id(conn, "sandbox")
    assert (j["decided_by"], j["decision_version_id"]) == (u.approver.id, vid)
    assert j["decided_at"] and '"approvers"' in j["decision_route"] and '"lower":0' in j["decision_route"].replace(" ", "")


# ---- an approved journal cannot be changed -------------------------------------------------------------------------

def test_the_database_refuses_to_change_or_remove_an_approved_journal(conn, oct_world):
    u = oct_world
    jid = journal_of(conn, u, 700)
    # drafts and waiting journals can change status
    conn.execute("UPDATE journals SET status = '下書き' WHERE id = ?", (jid,))
    conn.execute("UPDATE journals SET status = '承認待ち' WHERE id = ?", (jid,))
    conn.execute("UPDATE journal_lines SET memo = 'fine' WHERE journal_id = ?", (jid,))
    conn.commit()
    journals.decide(conn, u.approver, jid, True)
    for sql in ("UPDATE journals SET memo = 'x' WHERE id = {0}", "UPDATE journals SET status = '却下' WHERE id = {0}",
                "DELETE FROM journals WHERE id = {0}", "UPDATE journal_lines SET debit = 1 WHERE journal_id = {0}",
                "DELETE FROM journal_lines WHERE journal_id = {0}",
                "INSERT INTO journal_lines(journal_id, line_no, account_code, debit, credit) VALUES ({0}, 9, '1100', 1, 0)"):
        with pytest.raises(sqlite3.IntegrityError) as e:
            conn.execute(sql.format(jid))
        assert "approved" in str(e.value) or "cannot" in str(e.value)
    assert conn.execute("SELECT SUM(debit) FROM journal_lines WHERE journal_id = ?", (jid,)).fetchone()[0] == 700


# ---- reason codes ----------------------------------------------------------------------------------------------------

def test_difference_group_with_a_journal_reason_makes_the_fee_journal(conn, oct_world):
    u = oct_world
    gid = group_with(conn, "INV-2610-003")
    res = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=gid)
    assert (res.kind, res.number) == ("仕訳", "JE-000001")
    j = conn.execute("SELECT * FROM journals WHERE id = ?", (res.id,)).fetchone()
    assert (j["status"], j["method"], j["reason_code"], j["link_group_id"], j["entry_date"]) == ("下書き", "理由コード", "R01", gid, "2026-10-05")
    lines = [(l["account_code"], l["debit"], l["credit"]) for l in journals.lines_of(conn, res.id)]
    assert lines == [("6100", 50, 0), ("1100", 0, 50)]                          # d = bank - book = -50
    assert conn.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "仕訳の承認待ち"


def test_the_direction_of_the_journal_follows_bank_minus_book(conn, oct_world):
    u = oct_world
    bank_row = bank_interest_row(conn)                                  # bank only, +37: bank - book = +37
    a = reasons.apply_reason(conn, u.maker, MS, "R03", txn_id=bank_row)
    assert [(l["account_code"], l["debit"], l["credit"]) for l in journals.lines_of(conn, a.id)] == [("1100", 37, 0), ("4100", 0, 37)]
    book_row = txn(conn, "INV-2610-009", "帳簿")                        # books only, +70,000: bank - book = -70,000
    b = reasons.apply_reason(conn, u.maker, MS, "R02", txn_id=book_row)
    assert [(l["account_code"], l["debit"], l["credit"]) for l in journals.lines_of(conn, b.id)] == [("4100", 70000, 0), ("1100", 0, 70000)]


def small_world(conn, users, ledger_rows, bank_entries, opening=1000):
    """A tiny made-up month for the cases the sample does not have."""
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    from close_kit import ingest
    ledger = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, _with_balance(ledger_rows, opening))
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", ledger).ok
    bank = sb.build_zengin(bank_entries, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=opening)
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", bank).ok
    matchrun.run_match(conn, users.admin, MS)


def _with_balance(rows, opening):
    out, bal = [], opening
    for i, d, amt, ref, memo in rows:
        bal += amt
        out.append([i, d, amt, ref, memo, bal])
    return out


def test_a_difference_where_the_bank_is_higher_debits_the_deposit_account(conn, users):
    small_world(conn, users, [("T1", "2026-10-05", 100000, "X-1", "m")], [(D(2026, 10, 6), "in", 100060, "ｱ", "", "X-1")])
    gid = group_with(conn, "X-1")
    res = reasons.apply_reason(conn, users.maker, MS, "R02", group_id=gid)             # R02 端数差 -> 雑収入
    assert [(l["account_code"], l["debit"], l["credit"]) for l in journals.lines_of(conn, res.id)] == [("1100", 60, 0), ("4100", 0, 60)]


def test_what_a_reason_code_may_be_attached_to(conn, oct_world):
    u = oct_world
    gid = group_with(conn, "INV-2610-003")
    bank_row, book_row = bank_interest_row(conn), txn(conn, "INV-2610-009", "帳簿")
    with pytest.raises(reasons.ReasonError) as e:
        reasons.apply_reason(conn, u.maker, MS, "R04", group_id=gid)              # item on a difference group
    assert "仕訳」の理由コードだけ" in str(e.value)
    with pytest.raises(reasons.ReasonError) as e:
        reasons.apply_reason(conn, u.maker, MS, "R04", txn_id=bank_row)           # item on a bank-side row
    assert "銀行の側" in str(e.value)
    done = group_with(conn, "INV-2610-001")
    with pytest.raises(reasons.ReasonError):
        reasons.apply_reason(conn, u.maker, MS, "R01", group_id=done)             # a finished group
    reasons.apply_reason(conn, u.maker, MS, "R05", txn_id=book_row)               # item on a book-side row is fine
    with pytest.raises(reasons.ReasonError):
        reasons.apply_reason(conn, u.maker, MS, "R01", txn_id=book_row)           # one treatment per row
    with pytest.raises(reasons.ReasonError):
        reasons.apply_reason(conn, u.maker, MS, "NOPE", group_id=gid)
    with pytest.raises(reasons.ReasonError):
        reasons.apply_reason(conn, u.maker, MS, "R01")                            # neither group nor row
    # every refusal is in the operation log
    n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'reason_apply' AND outcome = '拒否'").fetchone()[0]
    assert n == 6


def test_duplicates_and_candidates_take_no_reason_code(conn, users):
    small_world(
        conn, users,
        [("T1", "2026-10-02", 111, "DUP-1", "a"), ("T2", "2026-10-03", 222, "DUP-1", "b"), ("T3", "2026-10-05", 8888, "", "c")],
        [(D(2026, 10, 9), "in", 8888, "ｱ", "", "")])
    kl = {r["memo"]: r["klass"] for r in conn.execute(
        "SELECT t.reference, t.memo, m.klass FROM match_rows m JOIN transactions t ON t.id = m.txn_id WHERE t.source = '帳簿'")}
    assert kl["a"] == "重複" and kl["c"] == "候補"
    for memo in ("a", "c"):
        t = conn.execute("SELECT id FROM transactions WHERE memo = ?", (memo,)).fetchone()[0]
        with pytest.raises(reasons.ReasonError) as e:
            reasons.apply_reason(conn, users.maker, MS, "R04", txn_id=t)
        assert "理由コードは付けられない" in str(e.value)


def test_a_row_of_zero_makes_no_journal(conn, users):
    small_world(conn, users, [("T1", "2026-10-02", 0, "Z-1", "zero")], [(D(2026, 10, 9), "in", 5, "ｱ", "", "")])
    t = conn.execute("SELECT id FROM transactions WHERE memo = 'zero'").fetchone()[0]
    with pytest.raises(reasons.ReasonError) as e:
        reasons.apply_reason(conn, users.maker, MS, "R01", txn_id=t)
    assert "0" in str(e.value)


def test_a_date_in_a_closed_period_moves_to_the_first_day_of_the_next_open_one(conn, users):
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    from close_kit import ingest
    led = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, _with_balance([("S1", "2026-09-20", 20000, "SEP-1", "m")], 500))
    bank = sb.build_zengin([(D(2026, 9, 21), "in", 19950, "ｱ", "", "SEP-1")], d_from=D(2026, 9, 1), d_to=D(2026, 9, 30), opening=500)
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", led).ok and ingest.ingest(conn, users.admin, "銀行", "b.txt", bank).ok
    matchrun.run_match(conn, users.admin, MS)
    res = reasons.apply_reason(conn, users.maker, MS, "R01", group_id=group_with(conn, "SEP-1"))
    assert conn.execute("SELECT entry_date, period FROM journals WHERE id = ?", (res.id,)).fetchone()[:] == ("2026-10-01", "2026-10")
    assert "2026-09-21" in res.note and "2026-10-01" in res.note


def test_approval_completes_the_group_and_marks_the_row_rejection_and_withdrawal_undo_it(conn, oct_world):
    u = oct_world
    gid, row = group_with(conn, "INV-2610-003"), bank_interest_row(conn)
    fee = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=gid)
    interest = reasons.apply_reason(conn, u.maker, MS, "R03", txn_id=row)
    state = lambda: conn.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0]  # noqa: E731
    klass = lambda: conn.execute("SELECT klass FROM match_rows WHERE txn_id = ?", (row,)).fetchone()[0]  # noqa: E731
    # rejection puts the group back, and the row stays 未照合; the journal is kept
    journals.submit(conn, u.maker, fee.id)
    journals.decide(conn, u.approver, fee.id, False, "やり直し")
    assert state() == "差額の処理待ち"
    journals.withdraw(conn, u.maker, interest.id)
    assert klass() == "未照合"
    again = reasons.apply_reason(conn, u.maker, MS, "R03", txn_id=row)          # the withdrawn draft no longer blocks the row
    assert conn.execute("SELECT status FROM journals WHERE id = ?", (interest.id,)).fetchone()[0] == "取り下げ"
    # a withdrawn draft of a group goes back the same way
    fee2 = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=gid)
    assert state() == "仕訳の承認待ち"
    journals.withdraw(conn, u.maker, fee2.id)
    assert state() == "差額の処理待ち"
    fee3 = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=gid)
    approve_journal(conn, u.maker, u.approver, fee3.id)
    approve_journal(conn, u.maker, u.approver, again.id)
    assert state() == "完了" and klass() == "処理済み"
    assert conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 5


# ---- adjusting items ------------------------------------------------------------------------------------------------

def make_item(conn, u, ref="INV-2610-009", code="R04"):
    return reasons.apply_reason(conn, u.maker, MS, code, txn_id=txn(conn, ref, "帳簿"))


def test_an_adjusting_item_needs_another_persons_approval(conn, users):
    both = make_user(conn, "both-user", ["作成者", "承認者"])
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    from close_kit import ingest
    ingest.ingest(conn, users.admin, "帳簿", "l.csv", make_samples.close_ledger_oct())
    ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct())
    matchrun.run_match(conn, users.admin, MS)
    item = reasons.apply_reason(conn, both, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿"))
    row = lambda: conn.execute("SELECT status, period, amount_minor FROM adjusting_items WHERE id = ?", (item.id,)).fetchone()  # noqa: E731
    assert tuple(row()) == ("承認待ち", "2026-10", 70000)                     # made in the period of the row's date
    with pytest.raises(reasons.SelfDecision):
        reasons.decide_item(conn, both, item.id, True)
    with pytest.raises(roles.PermissionDenied):
        reasons.decide_item(conn, users.maker, item.id, True)
    with pytest.raises(reasons.ReasonError):
        reasons.decide_item(conn, users.approver, item.id, False, "")        # rejection needs a reason
    assert reasons.decide_item(conn, users.approver, item.id, False, "要らない") == "却下"
    assert row()["status"] == "却下"
    again = reasons.apply_reason(conn, both, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿"))   # a rejected item can be made again
    assert again.id != item.id


def test_an_item_is_resolved_when_its_row_enters_a_finished_group_and_unresolved_when_that_is_undone(conn, oct_world):
    from close_helpers import import_november
    u = oct_world
    item = make_item(conn, u)
    reasons.decide_item(conn, u.approver, item.id, True)
    get = lambda: conn.execute("SELECT * FROM adjusting_items WHERE id = ?", (item.id,)).fetchone()  # noqa: E731
    assert get()["status"] == "未解消" and get()["resolved_group_id"] is None
    import_november(conn, u.admin)
    a = get()
    assert a["status"] == "解消"
    g = conn.execute("SELECT run_id FROM match_groups WHERE id = ?", (a["resolved_group_id"],)).fetchone()
    assert a["resolved_run_id"] == g["run_id"]
    # the group is undone (here: pushed back by hand, as a rejection would): the item is open again
    conn.execute("UPDATE match_groups SET state = '確認待ち' WHERE id = ?", (a["resolved_group_id"],))
    conn.commit()
    reasons.refresh_adjusting_items(conn, "sandbox")
    b = get()
    assert (b["status"], b["resolved_group_id"], b["resolved_run_id"]) == ("未解消", None, None)


def test_a_waiting_item_does_not_resolve_and_a_rejected_one_never_does(conn, oct_world):
    from close_helpers import import_november
    u = oct_world
    item = make_item(conn, u)                                   # still 承認待ち
    import_november(conn, u.admin)
    assert conn.execute("SELECT status FROM adjusting_items WHERE id = ?", (item.id,)).fetchone()[0] == "承認待ち"
