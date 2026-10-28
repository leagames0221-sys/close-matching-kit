"""The period-end balance statement: the OJP example, the sample's path to a difference of 0, the period-end point of
view, double counting after a posting, and the cases where the balance cannot be known."""
from datetime import date as D

from close_kit import balance, ingest, matchrun, posting, reasons
from conftest import load_workbook_into
from tools import make_samples, sample_builders as sb
from close_helpers import (MS, PROD, approve_journal, bank_interest_row, group_with, import_november, put_in_production,
                           statement, txn, walk_the_path)

# ---- the figures printed in the OJP guide sheet (Exhibit 1) ------------------------------------------------------------


def test_the_ojp_example_computed_from_its_own_figures():
    # bank 100,000; deposits in transit 20,000; outstanding checks 5,000 + 3,500 + 12,500
    # books 97,600; receivables 2,000 and interest 50 to add; EFT 745 and service charges 45 to deduct
    a = balance.assemble(100000, 97600, [20000, -5000, -3500, -12500], [2000, 50, -745, -45])
    assert a["adjusted_bank"] == 99000                       # the printed 99,000 is right on the bank side
    assert a["adjusted_book"] == 98860                       # 97,600 + 2,050 - 790; the sheet prints 99,000
    assert a["difference"] == 140                            # so the unidentified difference is 140, not 0
    assert (a["transit_in"], a["outstanding_out"], a["book_movements"]) == (20000, 21000, 1260)


def test_zero_difference_when_the_books_side_really_is_99000():
    assert balance.assemble(100000, 97740, [20000, -21000], [2000, 50, -745, -45])["difference"] == 0


# ---- the sample's path ---------------------------------------------------------------------------------------------------

def test_the_four_steps_of_the_sample_give_a_difference_of_zero(conn, oct_world):
    u = oct_world
    before = statement(conn)
    assert before.known and before.difference != 0
    walk_the_path(conn, u, with_item_for_payment=False)
    import_november(conn, u.admin)                       # the November statement pairs the month-end payment
    st = statement(conn)
    assert st.known and st.why_unknown == ""
    assert (st.bank_end, st.book_end) == (make_samples.CLOSE_BANK_OCT_CLOSING, make_samples.CLOSE_LEDGER_OCT_CLOSING)
    assert (st.bank_end, st.book_end) == (1_474_987, 1_504_000)
    assert (st.transit_in, st.outstanding_out, st.book_movements) == (70000, 41000, -13)    # -50 fee, +37 interest
    assert st.adjusted_bank == 1_474_987 + 70000 - 41000 == 1_503_987
    assert st.adjusted_book == 1_504_000 - 50 + 37 == 1_503_987
    assert st.difference == 0
    kinds = sorted((l.kind, l.amount) for l in st.lines)
    assert kinds == [("仕訳", -50), ("仕訳", 37), ("時期のずれ", -41000), ("時期のずれ", 70000)]
    assert not st.unprocessed_book and not st.unprocessed_bank


def test_the_same_zero_with_an_item_for_the_payment_instead_of_the_november_statement(conn, oct_world):
    u = oct_world
    walk_the_path(conn, u, with_item_for_payment=True)
    st = statement(conn)
    assert st.difference == 0 and sorted((l.kind, l.amount) for l in st.lines) == \
        [("仕訳", -50), ("仕訳", 37), ("調整項目", -41000), ("調整項目", 70000)]


def test_an_item_is_not_in_the_statement_before_it_is_approved(conn, oct_world):
    u = oct_world
    reasons.apply_reason(conn, u.maker, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿"))     # waiting, not approved
    st = statement(conn)
    assert not any(l.kind == "調整項目" for l in st.lines)
    assert any(l.ref == "INV-2610-009" for l in st.unprocessed_book)            # it is an unprocessed row, inside the difference


def test_rejecting_one_item_leaves_its_amount_as_the_difference(conn, oct_world):
    u = oct_world
    fee = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=group_with(conn, "INV-2610-003"))
    interest = reasons.apply_reason(conn, u.maker, MS, "R03", txn_id=bank_interest_row(conn))
    approve_journal(conn, u.maker, u.approver, fee.id)
    approve_journal(conn, u.maker, u.approver, interest.id)
    pay = reasons.apply_reason(conn, u.maker, MS, "R05", txn_id=txn(conn, "PAY-2610-02", "帳簿"))
    receipt = reasons.apply_reason(conn, u.maker, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿"))
    reasons.decide_item(conn, u.approver, pay.id, True)
    reasons.decide_item(conn, u.approver, receipt.id, False, "違う")
    st = statement(conn)
    assert st.difference == -70000                                   # the rejected item's amount, nothing hidden
    assert [l.ref for l in st.unprocessed_book] == ["INV-2610-009"]
    reasons.decide_item(conn, u.approver, reasons.apply_reason(conn, u.maker, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿")).id, True)
    assert statement(conn).difference == 0


def test_timing_pairs_across_the_period_end_are_found_automatically(conn, oct_world):
    u = oct_world
    import_november(conn, u.admin)
    st = statement(conn)
    timing = sorted((l.amount, l.ref) for l in st.lines if l.kind == "時期のずれ")
    assert timing == [(-41000, "PAY-2610-02"), (70000, "INV-2610-009")]
    assert st.unprocessed_book == [] and [l.ref for l in st.unprocessed_bank] == [""]          # only the interest is left


def test_events_after_the_period_end_do_not_change_that_periods_statement(conn, oct_world):
    u = oct_world
    walk_the_path(conn, u)
    import_november(conn, u.admin)
    first = statement(conn)
    first_lines = [(l.kind, l.amount, l.date, l.ref) for l in first.lines]
    dec_bank = sb.build_zengin([(D(2026, 12, 3), "in", 999, "ｱ", "", "ZZ-1")], d_from=D(2026, 12, 1), d_to=D(2026, 12, 31),
                               opening=1_523_987)
    dec_ledger = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, [["T201", "2026-12-04", 5, "NOPE", "m", 1_524_005]])
    assert ingest.ingest(conn, u.admin, "銀行", "dec.txt", dec_bank).ok
    assert ingest.ingest(conn, u.admin, "帳簿", "decl.csv", dec_ledger).ok
    matchrun.run_match(conn, u.admin, MS)
    again = statement(conn)
    assert (again.bank_end, again.book_end, again.difference) == (first.bank_end, first.book_end, first.difference)
    assert [(l.kind, l.amount, l.date, l.ref) for l in again.lines] == first_lines
    assert again.unprocessed_bank == first.unprocessed_bank and again.unprocessed_book == first.unprocessed_book


def test_an_unresolved_item_is_carried_into_the_next_period_with_its_age(conn, oct_world):
    u = oct_world
    steps = walk_the_path(conn, u, with_item_for_payment=True)
    # a November with only an unrelated pair: the item for 10-31 is still open
    bank = sb.build_zengin([(D(2026, 11, 10), "in", 500, "ｱ", "", "N-1")], d_from=D(2026, 11, 1), d_to=D(2026, 11, 30),
                           opening=make_samples.CLOSE_BANK_OCT_CLOSING)
    ledger = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, [["T300", "2026-11-09", 500, "N-1", "m", 1_504_500]])
    assert ingest.ingest(conn, u.admin, "銀行", "n.txt", bank).ok and ingest.ingest(conn, u.admin, "帳簿", "n.csv", ledger).ok
    matchrun.run_match(conn, u.admin, MS)
    nov = statement(conn, "2026-11", today=D(2026, 12, 10))
    items = [l for l in nov.lines if l.kind == "調整項目"]
    assert sorted(l.amount for l in items) == [-41000, 70000]
    assert all(l.age_days == (D(2026, 12, 10) - D(2026, 10, 31)).days for l in items)        # from the end of the period it was made in
    assert nov.known and nov.difference == 0
    oct_ = statement(conn, today=D(2026, 12, 10))
    assert oct_.difference == 0 and all(l.age_days == 40 for l in oct_.lines if l.kind == "調整項目")
    assert steps


def test_a_period_with_no_bundle_is_unknown_and_shows_no_difference(conn, oct_world):
    st = statement(conn, "2026-11")
    assert not st.known and st.difference is None and st.adjusted_bank is None
    assert "残高が分からない" in st.why_unknown


def test_a_match_set_not_used_for_the_balance_is_not_reconciled(conn, users):
    s = make_samples.close_sheets()
    s["照合セット"][0][6] = "いいえ"
    s["作業一覧"] = []
    load_workbook_into(conn, users.admin, sb.build_workbook(s))
    st = statement(conn)
    assert not st.known and "いいえ" in st.why_unknown


# ---- posted journals are not counted twice ------------------------------------------------------------------------------------

def test_a_posted_journal_that_came_back_into_the_ledger_is_not_added_twice(conn, oct_prod):
    u = oct_prod                                   # the posting file is written from production only
    steps = walk_the_path(conn, u, env=PROD)
    import_november(conn, u.admin, env=PROD)
    posting.export(conn, u.admin, PROD)
    fee_no, int_no = steps["fee"].number, steps["interest"].number
    # in December the books carry both journals, quoting the journal number as the reference
    dec_ledger = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, [
        ["T102", "2026-12-02", -50, fee_no, "手数料", 1_523_950], ["T103", "2026-12-02", 37, int_no, "利息", 1_523_987]])
    dec_bank = sb.build_zengin([(D(2026, 12, 20), "in", 1, "ｱ", "", "Q-1")], d_from=D(2026, 12, 1), d_to=D(2026, 12, 31),
                               opening=1_523_987)
    assert ingest.ingest(conn, u.admin, "帳簿", "dec.csv", dec_ledger, PROD).ok
    assert ingest.ingest(conn, u.admin, "銀行", "decb.txt", dec_bank, PROD).ok
    matchrun.run_match(conn, u.admin, MS, PROD)
    rows = conn.execute("SELECT t.reference, m.klass FROM match_rows m JOIN transactions t ON t.id = m.txn_id"
                        " WHERE t.reference IN (?, ?) ORDER BY t.reference", (fee_no, int_no)).fetchall()
    assert [(r["reference"], r["klass"]) for r in rows] == [(fee_no, "転記済み"), (int_no, "転記済み")]
    assert conn.execute("SELECT COUNT(*) FROM posted_links").fetchone()[0] == 2
    # October: the ledger rows are dated after the period end, so the journals still stand in for them
    oct_ = statement(conn, env=PROD)
    assert oct_.difference == 0 and oct_.book_movements == -13
    # December: they are inside the book balance and must not be added a second time
    s = make_samples.close_sheets()
    s["会計期間"].append(["2026-12", D(2026, 12, 1), D(2026, 12, 31), "いいえ"])
    put_in_production(conn, sb.build_workbook(s), u.admin, u.admin2, u.approver)       # a new version, by a change request
    dec = statement(conn, "2026-12", env=PROD)
    assert dec.known and not any(l.kind == "仕訳" for l in dec.lines)
    assert (dec.book_end, dec.bank_end) == (1_523_987, 1_523_988)
    assert dec.difference == 1 and [l.amount for l in dec.unprocessed_bank] == [1]      # only the unmatched bank row
    # had the journals been counted again the difference would be 1 - (-13) = 14
    assert dec.book_movements == 0


def test_csv_of_the_statement_neutralises_formula_text(conn, users):
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    rows = [["T1", "2026-10-31", 10, "E-1", "=HYPERLINK(\"http://x\")", 1010]]
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, rows)).ok
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", sb.build_zengin([], d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=1000)).ok
    st = statement(conn)
    text = balance.to_csv(st)
    assert "'=HYPERLINK" in text and '"=HYPERLINK' not in text
    assert text.startswith('"照合セット"')


def test_the_walk_of_the_sample_reproduces_the_path_to_zero(tmp_path):
    res = make_samples.walk(tmp_path)
    oct_, nov = res["2026-10"], res["2026-11"]
    assert (oct_.bank_end, oct_.transit_in, oct_.outstanding_out, oct_.adjusted_bank) == (1_474_987, 70000, 41000, 1_503_987)
    assert (oct_.book_end, oct_.book_movements, oct_.adjusted_book, oct_.difference) == (1_504_000, -13, 1_503_987, 0)
    assert (nov.bank_end, nov.book_end, nov.difference) == (1_523_987, 1_524_000, 0)


# ---- a pair with a difference: whose period it is, and what is listed ---------------------------------------------------

def test_a_difference_waiting_for_its_journal_is_listed_as_unprocessed(conn, oct_world):
    u = oct_world
    st = statement(conn)                                              # the fee pair (bank 149,950 / books 150,000)
    assert [(l.amount, l.ref, l.status) for l in st.unprocessed_groups] == \
        [(-50, f"グループ {group_with(conn, 'INV-2610-003')}", "差額の処理待ち")]
    assert "照合の組の差額" in balance.to_csv(st)
    fee = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=group_with(conn, "INV-2610-003"))
    assert [l.status for l in statement(conn).unprocessed_groups] == ["仕訳の承認待ち"]      # still not settled
    approve_journal(conn, u.maker, u.approver, fee.id)
    assert statement(conn).unprocessed_groups == []                   # the approved journal (dated 10-05) settles it


def nov_bank_with_a_short_receipt():
    """November: the month-end payment arrives exactly, the month-end receipt 50 short (the payer deducted a fee)."""
    rows = [(D(2026, 11, 2), "out", 41000, "ｶﾒｲﾌﾞｯｻﾝ", "ﾌﾘｺﾐ", "PAY-2610-02"),
            (D(2026, 11, 2), "in", 69950, "ｻﾝﾌﾟﾙｼｮｳﾃﾝ2", "ﾆｭｳｷﾝ", "INV-2610-009")]
    return sb.build_zengin(rows, d_from=D(2026, 11, 1), d_to=D(2026, 11, 30), opening=make_samples.CLOSE_BANK_OCT_CLOSING)


def test_a_reason_code_on_a_later_pair_changes_neither_the_earlier_period_nor_the_items_it_settles(conn, oct_world):
    u = oct_world
    walk_the_path(conn, u, with_item_for_payment=True)
    first = statement(conn)
    assert first.difference == 0
    assert ingest.ingest(conn, u.admin, "銀行", "n.txt", nov_bank_with_a_short_receipt()).ok
    assert ingest.ingest(conn, u.admin, "帳簿", "n.csv", make_samples.close_ledger_nov()).ok
    matchrun.run_match(conn, u.admin, MS)
    g = group_with(conn, "INV-2610-009")
    assert conn.execute("SELECT state FROM match_groups WHERE id = ?", (g,)).fetchone()[0] == "差額の処理待ち"
    assert [r[0] for r in conn.execute("SELECT status FROM adjusting_items ORDER BY id")] == ["解消", "解消"]
    fee = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=g)        # the fee of the November pair
    matchrun.run_match(conn, u.admin, MS)                                   # any later event re-reads the items
    # the pair is not undone, so the item it settles stays settled
    assert [r[0] for r in conn.execute("SELECT status FROM adjusting_items ORDER BY id")] == ["解消", "解消"]
    oct_ = statement(conn)
    # October as of 10-31 is unchanged (an adjusting item may only have become the timing pair that settles it)
    assert oct_.difference == 0 and sorted((l.amount, l.ref) for l in oct_.lines) == \
        sorted((l.amount, l.ref) for l in first.lines)
    assert oct_.unprocessed_book == oct_.unprocessed_bank == oct_.unprocessed_groups == []
    nov = statement(conn, "2026-11")
    assert nov.known and not any(l.kind == "調整項目" for l in nov.lines)  # the settled item is not counted again
    assert [(l.amount, l.status) for l in nov.unprocessed_groups] == [(-50, "仕訳の承認待ち")]
    assert [l.amount for l in nov.unprocessed_book] == [20000]              # the November sale the bank has not paid
    assert nov.difference == -50 - 20000
    approve_journal(conn, u.maker, u.approver, fee.id)
    nov = statement(conn, "2026-11")
    assert nov.unprocessed_groups == [] and nov.difference == -20000
    assert statement(conn).difference == 0


def test_a_bank_row_the_books_record_next_period_with_a_difference_adds_up_to_zero(conn, users):
    """Bank 149,950 on 10-30, books 150,000 on 11-01 (the payer took a fee): the fee journal is dated 10-30 (the bank
    row), so it is October's; the later booking adds the book side (150,000), not the bank row once more."""
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    ledger_oct = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, [["L1", "2026-10-01", 1000, "X-1", "m", 1000]])
    ledger_nov = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, [["L2", "2026-11-01", 150000, "X-2", "m", 151000]])
    bank_oct = sb.build_zengin([(D(2026, 10, 1), "in", 1000, "ｱ", "", "X-1"), (D(2026, 10, 30), "in", 149950, "ｲ", "", "X-2")],
                               d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=0)
    assert ingest.ingest(conn, users.admin, "帳簿", "a.csv", ledger_oct).ok
    assert ingest.ingest(conn, users.admin, "帳簿", "b.csv", ledger_nov).ok
    assert ingest.ingest(conn, users.admin, "銀行", "c.txt", bank_oct).ok
    matchrun.run_match(conn, users.admin, MS)
    g = group_with(conn, "X-2")
    before = statement(conn)
    assert [l.amount for l in before.lines if l.kind == "翌期に記帳"] == [150000]     # the book side of the pair
    assert before.difference == -50 and [l.amount for l in before.unprocessed_groups] == [-50]
    fee = reasons.apply_reason(conn, users.maker, MS, "R01", group_id=g)
    assert conn.execute("SELECT entry_date FROM journals WHERE id = ?", (fee.id,)).fetchone()[0] == "2026-10-30"
    approve_journal(conn, users.maker, users.approver, fee.id)
    st = statement(conn)
    assert (st.bank_end, st.book_end) == (150950, 1000)
    assert st.adjusted_book == 1000 + 150000 - 50 == st.adjusted_bank and st.difference == 0
    assert st.unprocessed_groups == []


def test_a_posted_journal_is_linked_when_the_ledger_is_imported_not_only_at_the_next_match(conn, oct_prod):
    u = oct_prod
    steps = walk_the_path(conn, u, with_item_for_payment=True, env=PROD)
    posting.export(conn, u.admin, PROD)
    fee_no, int_no = steps["fee"].number, steps["interest"].number
    nov_ledger = sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, [
        ["T102", "2026-11-02", -50, fee_no, "手数料", 1_503_950], ["T103", "2026-11-02", 37, int_no, "利息", 1_503_987]])
    res = ingest.ingest(conn, u.admin, "帳簿", "nov.csv", nov_ledger, PROD)
    assert res.ok and any("転記済み" in n for n in res.notes)
    rows = conn.execute("SELECT t.reference, m.klass FROM match_rows m JOIN transactions t ON t.id = m.txn_id"
                        " WHERE t.reference IN (?, ?) ORDER BY t.reference", (fee_no, int_no)).fetchall()
    assert [(r["reference"], r["klass"]) for r in rows] == [(fee_no, "転記済み"), (int_no, "転記済み")]
    nov_bank = sb.build_zengin([], d_from=D(2026, 11, 1), d_to=D(2026, 11, 30), opening=make_samples.CLOSE_BANK_OCT_CLOSING)
    assert ingest.ingest(conn, u.admin, "銀行", "nov.txt", nov_bank, PROD).ok
    nov = statement(conn, "2026-11", env=PROD)                          # no match run in between
    assert nov.known and not any(l.kind == "仕訳" for l in nov.lines) and nov.book_movements == 0
    assert not any(l.ref in (fee_no, int_no) for l in nov.unprocessed_book)
    matchrun.run_match(conn, u.admin, MS, PROD)                         # the match run keeps them as they are
    assert conn.execute("SELECT COUNT(*) FROM posted_links").fetchone()[0] == 2
    assert statement(conn, "2026-11", env=PROD).difference == nov.difference
