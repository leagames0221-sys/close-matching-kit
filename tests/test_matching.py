from datetime import date

import pytest

from close_kit import ingest, matching, matchrun, roles
from close_kit.matching import Row
from tools import make_samples, sample_builders as sb


def R(side, id, d, amount, ref="", memo=""):
    return Row.make(id, side, d, amount, ref, memo)


def rule(order=10, kind="自動", typ="1対1", keys=(("参照", "参照"),), window=0, tol=0, lb=(), rb=(), pk=None,
         limit=10, name=None, acct=""):
    return {"order": order, "name": name or f"r{order}", "kind": kind, "type": typ,
            "keys": [list(k) for k in keys], "date_window": window, "amount_tolerance_minor": tol,
            "left_bundle": list(lb), "right_bundle": list(rb), "pair_key": pk, "bundle_limit": limit,
            "enabled": True, "diff_account": acct}


AMT_DATE = (("金額", "金額"), ("勘定日", "勘定日"))


def groups_of(res, kind=None):
    return [g for g in res.groups if kind is None or g.kind == kind]


# ---- the 6 rows of the table (ADR 0003, "The six rows") -----------------------------------------

def test_row1_auto_within_window_zero_difference_is_done_and_leaves_the_remainder():
    res = matching.run([R("L", 1, "2026-10-01", 100, "A")], [R("R", 2, "2026-10-02", 100, "A")],
                       [rule(window=1)])
    (g,) = res.groups
    assert (g.kind, g.state, g.reason) == ("自動", "完了", "")
    assert res.klass == {1: "自動", 2: "自動"}


def test_row2_auto_within_tolerance_but_nonzero_difference_waits_for_the_difference_when_the_rule_has_no_account():
    res = matching.run([R("L", 1, "2026-10-01", 1000, "A")], [R("R", 2, "2026-10-01", 970, "A")],
                       [rule(tol=30)])
    (g,) = res.groups
    assert (g.kind, g.state, g.diff, g.diff_account) == ("自動", "差額の処理待ち", 30, "")
    assert res.klass[1] == res.klass[2] == "自動"


def test_row2_auto_within_tolerance_with_an_account_for_the_difference_is_done_and_names_the_account():
    """A difference inside the tolerance whose rule names an account: the match is finished (the author's decision;
    Oracle creates an external transaction in cash management, Odoo reconciles the difference with the account of a
    counterpart entry for underpayments only). The engine only says which account; the journal is drafted by `matchrun`."""
    res = matching.run([R("L", 1, "2026-10-01", 1000, "A")], [R("R", 2, "2026-10-01", 970, "A")],
                       [rule(tol=30, acct="6100")])
    (g,) = res.groups
    assert (g.kind, g.state, g.diff, g.diff_account) == ("自動", "完了", 30, "6100")
    assert res.klass[1] == res.klass[2] == "自動"
    # no difference: nothing to book, so no account is named
    res = matching.run([R("L", 1, "2026-10-01", 1000, "A")], [R("R", 2, "2026-10-01", 1000, "A")],
                       [rule(tol=30, acct="6100")])
    assert (res.groups[0].state, res.groups[0].diff_account) == ("完了", "")
    # outside the tolerance it is still only a candidate, whatever the account
    res = matching.run([R("L", 1, "2026-10-01", 1000, "A")], [R("R", 2, "2026-10-01", 969, "A")],
                       [rule(tol=30, acct="6100")])
    assert (res.groups[0].kind, res.groups[0].diff_account) == ("候補", "")


def test_row3_same_reference_but_amount_outside_tolerance_is_a_candidate_amount_diff():
    res = matching.run([R("L", 1, "2026-10-01", 1000, "A")], [R("R", 2, "2026-10-01", 969, "A")],
                       [rule(tol=30)])
    (g,) = res.groups
    assert (g.kind, g.state, g.reason) == ("候補", "確認待ち", "amount_diff")
    assert res.klass == {1: "候補", 2: "候補"}


def test_row3_same_reference_but_date_outside_window_is_a_candidate_timing_diff():
    res = matching.run([R("L", 1, "2026-10-01", 1000, "A")], [R("R", 2, "2026-10-04", 1000, "A")],
                       [rule(window=2)])
    (g,) = res.groups
    assert (g.kind, g.reason) == ("候補", "timing_diff")
    assert res.klass == {1: "候補", 2: "候補"}


def test_row3_both_outside_gives_nothing():
    res = matching.run([R("L", 1, "2026-10-01", 1000, "A")], [R("R", 2, "2026-10-09", 1, "A")], [rule()])
    assert not res.groups and res.klass == {1: "未照合", 2: "未照合"}


def test_row4_candidate_rule_makes_a_candidate_not_an_automatic_match():
    res = matching.run([R("L", 1, "2026-10-01", 500)], [R("R", 2, "2026-10-03", 500)],
                       [rule(kind="候補", keys=AMT_DATE, window=3)])
    (g,) = res.groups
    assert (g.kind, g.state, g.reason) == ("候補", "確認待ち", "候補ルール")


def test_row5_two_possible_partners_is_never_automatic():
    lefts = [R("L", 1, "2026-10-01", 500)]
    rights = [R("R", 2, "2026-10-01", 500), R("R", 3, "2026-10-02", 500)]
    res = matching.run(lefts, rights, [rule(keys=AMT_DATE, window=3)])
    (g,) = res.groups
    assert (g.kind, g.reason, g.left, g.right) == ("候補", "ambiguous", [1], [2, 3])
    assert set(res.klass.values()) == {"候補"}


def test_row6_nothing_matches_is_unmatched():
    res = matching.run([R("L", 1, "2026-10-01", 500, "A")], [R("R", 2, "2026-10-01", 600, "B")], [rule()])
    assert not res.groups and res.klass == {1: "未照合", 2: "未照合"}


# ---- boundaries -----------------------------------------------------------------------------------

@pytest.mark.parametrize("diff,expected_kind", [(0, "自動"), (5, "自動"), (6, "候補")])
def test_amount_tolerance_boundary_is_inclusive(diff, expected_kind):
    res = matching.run([R("L", 1, "2026-10-01", 100, "A")], [R("R", 2, "2026-10-01", 100 - diff, "A")],
                       [rule(tol=5)])
    assert res.groups[0].kind == expected_kind


@pytest.mark.parametrize("days,expected_kind", [(0, "自動"), (3, "自動"), (4, "候補")])
def test_date_window_boundary_is_inclusive(days, expected_kind):
    d2 = date(2026, 10, 1 + days).isoformat()
    res = matching.run([R("L", 1, "2026-10-01", 100, "A")], [R("R", 2, d2, 100, "A")], [rule(window=3)])
    assert res.groups[0].kind == expected_kind


def test_difference_candidates_need_a_reference_or_memo_key():
    """D1: with only amount/date keys a differing amount is not paired at all."""
    res = matching.run([R("L", 1, "2026-10-01", 100)], [R("R", 2, "2026-10-01", 90)],
                       [rule(keys=AMT_DATE, tol=5)])
    assert not res.groups and res.klass == {1: "未照合", 2: "未照合"}
    # with a memo key the same pair becomes an amount_diff candidate
    res = matching.run([R("L", 1, "2026-10-01", 100, memo="x")], [R("R", 2, "2026-10-01", 90, memo="x")],
                       [rule(keys=(("摘要", "摘要"),), tol=5)])
    assert res.groups[0].reason == "amount_diff"


def test_empty_references_never_match_each_other():
    res = matching.run([R("L", 1, "2026-10-01", 100, "")], [R("R", 2, "2026-10-01", 100, "")], [rule()])
    assert not res.groups


# ---- candidate handling -----------------------------------------------------------------------------

def test_earlier_candidate_wins_when_a_later_rule_would_use_the_same_row():
    lefts = [R("L", 1, "2026-10-01", 100, "A")]
    rights = [R("R", 2, "2026-10-01", 90, "A"), R("R", 3, "2026-10-01", 100)]
    res = matching.run(lefts, rights, [rule(10), rule(20, kind="候補", keys=AMT_DATE)])
    (g,) = res.groups
    assert (g.rule_order, g.left, g.right) == (10, [1], [2])
    assert res.klass[3] == "未照合"


def test_a_later_automatic_rule_does_not_use_a_row_of_an_earlier_candidate():
    """Odoo says only that the first model in the sequence is applied. That the earlier rule's candidate stays whole is
    the author's decision; the row 1 that it holds is not offered to rule 20, so R3 (which rule 20 would have paired with it) stays unmatched."""
    lefts = [R("L", 1, "2026-10-01", 100, "A")]
    rights = [R("R", 2, "2026-10-01", 90, "A"), R("R", 3, "2026-10-01", 100)]
    res = matching.run(lefts, rights, [rule(10), rule(20, keys=AMT_DATE)])
    (g,) = res.groups
    assert (g.kind, g.rule_order, g.reason, g.left, g.right) == ("候補", 10, "amount_diff", [1], [2])
    assert res.klass == {1: "候補", 2: "候補", 3: "未照合"}


def test_a_later_automatic_rule_still_takes_rows_that_no_candidate_holds():
    lefts = [R("L", 1, "2026-10-01", 100, "A"), R("L", 4, "2026-10-01", 50)]
    rights = [R("R", 2, "2026-10-01", 90, "A"), R("R", 3, "2026-10-01", 50)]
    res = matching.run(lefts, rights, [rule(10), rule(20, keys=AMT_DATE)])
    assert sorted((g.kind, g.rule_order, tuple(g.left), tuple(g.right)) for g in res.groups) == \
        [("候補", 10, (1,), (2,)), ("自動", 20, (4,), (3,))]


# ---- bundles ------------------------------------------------------------------------------------------

def test_many_to_one_one_to_many_and_many_to_many_totals():
    # 多対1: left bundled
    res = matching.run([R("L", 1, "2026-10-01", 40, "P"), R("L", 2, "2026-10-01", 60, "P")],
                       [R("R", 3, "2026-10-01", 100, "P")],
                       [rule(typ="多対1", keys=(), lb=["参照"], pk="参照")])
    (g,) = res.groups
    assert (g.kind, g.left, g.right, g.state) == ("自動", [1, 2], [3], "完了")
    # 1対多: right bundled
    res = matching.run([R("L", 1, "2026-10-01", 100, "P")],
                       [R("R", 2, "2026-10-01", 50, "P"), R("R", 3, "2026-10-02", 50, "P")],
                       [rule(typ="1対多", keys=(), rb=["参照"], pk="参照", window=2)])
    assert res.groups[0].right == [2, 3] and res.groups[0].kind == "自動"
    # 多対多
    res = matching.run([R("L", 1, "2026-10-01", 40, "P"), R("L", 2, "2026-10-01", 60, "P")],
                       [R("R", 3, "2026-10-01", 75, "P"), R("R", 4, "2026-10-01", 25, "P")],
                       [rule(typ="多対多", keys=(), lb=["参照"], rb=["参照"], pk="参照")])
    assert res.groups[0].left == [1, 2] and res.groups[0].right == [3, 4]


def test_bundle_with_different_total_is_a_candidate_not_automatic():
    res = matching.run([R("L", 1, "2026-10-01", 40, "P"), R("L", 2, "2026-10-01", 60, "P")],
                       [R("R", 3, "2026-10-01", 90, "P")],
                       [rule(typ="多対1", keys=(), lb=["参照"], pk="参照")])
    (g,) = res.groups
    assert (g.kind, g.reason, g.diff) == ("候補", "amount_diff", 10)


def test_bundles_use_no_amount_tolerance_and_compare_dates_only_when_the_date_is_a_bundle_key():
    """Oracle: "No reconciliation amount tolerances are allowed in one to many, many to one, or many to many matching
    scenarios", and a date is a matching criterion "only after you select these as grouping attributes"."""
    lefts = [R("L", 1, "2026-10-01", 40, "P"), R("L", 2, "2026-10-01", 60, "P")]
    cand = rule(kind="候補", typ="多対1", keys=(), lb=["参照"], pk="参照", tol=5, window=2)
    # date is not a bundle key: 3 days apart does not matter, equal totals are enough
    res = matching.run(lefts, [R("R", 3, "2026-10-04", 100, "P")], [cand])
    (g,) = res.groups
    assert (g.kind, g.state, g.reason, g.left, g.right) == ("候補", "確認待ち", "候補ルール", [1, 2], [3])
    # the tolerance of the rule is ignored: a total off by 3 (inside tol=5) is not a hit
    res = matching.run(lefts, [R("R", 3, "2026-10-02", 97, "P")], [cand])
    assert not res.groups and set(res.klass.values()) == {"重複", "未照合"}
    # date is a bundle key: now the window counts (latest date of each bundle)
    dated = rule(kind="候補", typ="多対1", keys=(), lb=["参照", "勘定日"], pk="参照", tol=5, window=2)
    res = matching.run(lefts, [R("R", 3, "2026-10-03", 100, "P")], [dated])
    assert len(res.groups) == 1                                   # 2 days <= 2
    res = matching.run(lefts, [R("R", 3, "2026-10-04", 100, "P")], [dated])
    assert not res.groups                                         # 3 days > 2


def test_an_automatic_bundle_with_a_total_off_by_one_is_a_candidate_even_inside_the_rules_tolerance():
    lefts = [R("L", 1, "2026-10-01", 40, "P"), R("L", 2, "2026-10-01", 60, "P")]
    res = matching.run(lefts, [R("R", 3, "2026-10-01", 99, "P")],
                       [rule(typ="多対1", keys=(), lb=["参照"], pk="参照", tol=5, window=2)])
    (g,) = res.groups
    assert (g.kind, g.reason, g.diff, g.diff_account) == ("候補", "amount_diff", 1, "")


def test_bundle_over_the_row_limit_is_not_made_and_rows_go_back_to_the_remainder():
    lefts = [R("L", i, "2026-10-01", 10, "P") for i in (1, 2, 3)]
    rights = [R("R", 4, "2026-10-01", 30, "P")]
    res = matching.run(lefts, rights, [rule(typ="多対1", keys=(), lb=["参照"], pk="参照", limit=2)])
    assert not res.groups
    assert res.klass[4] == "未照合" and {res.klass[i] for i in (1, 2, 3)} == {"重複"}
    assert res.skipped_bundles and "上限 2" in res.skipped_bundles[0]
    res = matching.run(lefts, rights, [rule(typ="多対1", keys=(), lb=["参照"], pk="参照", limit=3)])
    assert res.groups[0].left == [1, 2, 3]


# ---- duplicates ------------------------------------------------------------------------------------------

def test_duplicate_rows_take_part_in_matching_and_two_possible_partners_make_a_candidate():
    """Reconify (`duplicate_policy` default `flag`): all rows participate in matching, duplicates are flagged for review.
    Oracle "Ambiguous" / NetSuite "can't choose one": more than one possible partner is never automatic."""
    lefts = [R("L", 1, "2026-10-01", 100, "A"), R("L", 2, "2026-10-01", 100, "A")]
    rights = [R("R", 3, "2026-10-01", 100, "A")]
    res = matching.run(lefts, rights, [rule(), rule(20, keys=AMT_DATE)])
    (g,) = res.groups
    assert (g.kind, g.state, g.reason, g.left, g.right) == ("候補", "確認待ち", "ambiguous", [1, 2], [3])
    assert res.klass == {1: "候補", 2: "候補", 3: "候補"}
    assert res.dup_ids == {1, 2}                      # the engine reports them; the screens show the mark


def test_a_twin_far_away_in_date_still_counts_as_a_possible_partner_through_the_shared_reference():
    """L1 and L2 share a reference (duplicates). R3 fits L1 exactly; L2 has the same reference and amount but is 19 days
    away (a timing_diff). R3 therefore has two possible partners, so the three rows are one candidate, not an
    automatic pair."""
    lefts = [R("L", 1, "2026-10-01", 100, "A"), R("L", 2, "2026-10-20", 100, "A")]
    rights = [R("R", 3, "2026-10-01", 100, "A")]
    res = matching.run(lefts, rights, [rule(window=2)])
    (g,) = res.groups
    assert (g.kind, g.reason, g.left, g.right) == ("候補", "ambiguous", [1, 2], [3])
    assert res.dup_ids == {1, 2}


def test_duplicate_rows_without_a_partner_stay_in_the_class_duplicate():
    res = matching.run([R("L", 1, "2026-10-01", 5, "A"), R("L", 2, "2026-10-01", 5, "A")], [], [rule()])
    assert not res.groups
    assert res.klass == {1: "重複", 2: "重複"}


def test_duplicates_on_both_sides_make_one_candidate_of_all_their_rows():
    lefts = [R("L", 1, "2026-10-01", 100, "A"), R("L", 2, "2026-10-01", 100, "A")]
    rights = [R("R", 3, "2026-10-01", 100, "A"), R("R", 4, "2026-10-01", 100, "A")]
    res = matching.run(lefts, rights, [rule()])
    (g,) = res.groups
    assert (g.kind, g.reason, g.left, g.right) == ("候補", "ambiguous", [1, 2], [3, 4])


def test_empty_reference_is_not_a_duplicate():
    res = matching.run([R("L", 1, "2026-10-01", 1), R("L", 2, "2026-10-01", 2)], [], [rule()])
    assert res.dup_ids == set()


# ---- database level --------------------------------------------------------------------------------------

def prepared(conn, admin):
    assert ingest.ingest(conn, admin, "帳簿", "l.csv", make_samples.ledger_csv()).ok
    assert ingest.ingest(conn, admin, "銀行", "b.txt", make_samples.bank_zengin()).ok


def test_sample_dataset_gives_the_expected_classes(conn, admin, loaded):
    prepared(conn, admin)
    s = matchrun.run_match(conn, admin, "預金照合")
    # Rows of a candidate stay in it (the author's decision; Odoo says only that the first model in the sequence is applied), so the three instalments of
    # INV-2610-005 and the twin entries of INV-2610-009 are candidates of rule 10 and rule 40 never sees them; the
    # twin entries INV-2610-010 have no partner and stay duplicates.
    assert s.by_class["自動"] == {"L": 3, "R": 3}
    assert s.by_class["候補"] == {"L": 9, "R": 7}
    assert s.by_class["未照合"] == {"L": 1, "R": 1}
    assert s.by_class["重複"] == {"L": 2, "R": 0}
    assert s.groups == {"自動/完了": 2, "自動/差額の処理待ち": 1, "候補/確認待ち": 6}
    reasons = sorted(r[0] for r in conn.execute("SELECT reason FROM match_groups WHERE kind='候補'"))
    assert reasons == ["ambiguous", "ambiguous", "ambiguous", "amount_diff", "timing_diff", "候補ルール"]
    dup_marked = {tuple(r) for r in conn.execute(
        "SELECT t.reference, m.klass FROM match_rows m JOIN transactions t ON t.id = m.txn_id"
        " WHERE t.reference IN ('INV-2610-009', 'INV-2610-010') AND t.source = '帳簿'")}
    assert dup_marked == {("INV-2610-009", "候補"), ("INV-2610-010", "重複")}
    g = conn.execute("SELECT * FROM match_groups WHERE state='差額の処理待ち'").fetchone()
    assert (g["env"], g["version_id"], g["period"], g["diff_minor"]) == ("sandbox", loaded, "2026-10", 50)


def test_the_duplicate_mark_is_shown_for_rows_that_took_part_in_matching(conn, admin, loaded):
    from close_kit import queries
    prepared(conn, admin)
    matchrun.run_match(conn, admin, "預金照合")
    cands = queries.rows_in_class(conn, admin, "預金照合", "候補")
    assert {(r["source"], r["reference"]) for r in cands if r["dup"]} == {("帳簿", "INV-2610-005"), ("帳簿", "INV-2610-009")}
    assert not any(r["dup"] for r in cands if r["source"] == "銀行")           # the bank has each reference once
    left_over = queries.rows_in_class(conn, admin, "預金照合", "重複")
    assert [(r["reference"], r["dup"]) for r in left_over] == [("INV-2610-010", True)] * 2
    gid = conn.execute("SELECT g.id FROM match_groups g JOIN match_group_rows gr ON gr.group_id = g.id"
                       " JOIN transactions t ON t.id = gr.txn_id WHERE t.reference = 'INV-2610-009'").fetchone()[0]
    marks = [(r["side"], r["dup"]) for r in queries.group_rows(conn, admin, gid)]
    assert marks == [("L", True), ("L", True), ("R", False)]


def test_every_result_carries_environment_run_version_and_period(conn, admin, loaded):
    prepared(conn, admin)
    s = matchrun.run_match(conn, admin, "預金照合")
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (s.run_id,)).fetchone()
    assert (run["kind"], run["env"], run["version_id"]) == ("match", "sandbox", loaded)
    for table in ("match_groups", "match_rows"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (s.run_id,)).fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM match_groups WHERE period IS NULL OR version_id != ?",
                        (loaded,)).fetchone()[0] == 0


def test_rerun_keeps_fixed_groups_and_their_ids_and_recomputes_the_rest(conn, admin, loaded):
    prepared(conn, admin)
    matchrun.run_match(conn, admin, "預金照合")
    fixed_before = {r["id"]: tuple(sorted(x["txn_id"] for x in conn.execute(
        "SELECT txn_id FROM match_group_rows WHERE group_id = ?", (r["id"],))))
        for r in conn.execute("SELECT id FROM match_groups WHERE state IN ('完了', '差額の処理待ち')")}
    cand_before = {r["id"] for r in conn.execute("SELECT id FROM match_groups WHERE state = '確認待ち'")}
    assert len(fixed_before) == 3 and len(cand_before) == 6
    # next month: one new ledger line and the matching bank line arrive
    hdr = ["取引ID", "日付", "金額", "参照", "摘要"]
    assert ingest.ingest(conn, admin, "帳簿", "n.csv", sb.build_csv(hdr, [["T101", "2026-11-02", 777, "INV-2611-001", ""]])).ok
    bank = sb.build_zengin([(date(2026, 11, 3), "in", 777, "ｱ", "", "INV-2611-001")], d_from=date(2026, 11, 1),
                           d_to=date(2026, 11, 30), opening=2040740)
    assert ingest.ingest(conn, admin, "銀行", "n.txt", bank).ok
    s = matchrun.run_match(conn, admin, "預金照合")
    fixed_after = {r["id"]: tuple(sorted(x["txn_id"] for x in conn.execute(
        "SELECT txn_id FROM match_group_rows WHERE group_id = ?", (r["id"],))))
        for r in conn.execute("SELECT id FROM match_groups WHERE state IN ('完了', '差額の処理待ち') AND id IN (%s)"
                              % ",".join(map(str, fixed_before)))}
    assert fixed_after == fixed_before            # same group ids, same rows
    new = conn.execute("SELECT * FROM match_groups WHERE run_id = ? AND kind = '自動'", (s.run_id,)).fetchall()
    assert len(new) == 1 and new[0]["period"] == "2026-11"
    now_cand = {r["id"] for r in conn.execute("SELECT id FROM match_groups WHERE state = '確認待ち'")}
    assert len(now_cand) == 6 and not (now_cand & cand_before)   # candidates were recomputed (new ids)
    assert s.by_class["自動"] == {"L": 4, "R": 4}
    # no row is in two classes / groups
    assert conn.execute("SELECT COUNT(*) FROM (SELECT txn_id FROM match_group_rows GROUP BY txn_id HAVING COUNT(*) > 1)"
                        ).fetchone()[0] == 0


def test_match_run_needs_the_role_on_the_server(conn, admin, loaded, approver):
    prepared(conn, admin)
    with pytest.raises(roles.PermissionDenied):
        matchrun.run_match(conn, approver, "預金照合")
