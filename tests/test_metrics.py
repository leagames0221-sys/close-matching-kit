import pytest

from close_kit import metrics


def P(*pairs):
    return {(a, b) for a, b in pairs}


def test_formulas_match_a_hand_calculation():
    truth = P(("a", "1"), ("b", "2"), ("c", "3"), ("d", "4"))      # 4 true pairs
    auto = P(("a", "1"), ("b", "2"), ("c", "9"))                    # 3 auto pairs, 2 correct
    cand = P(("d", "4"), ("e", "5"))                                # 2 candidate pairs, 1 correct
    m = metrics.compute(auto, cand, truth, unmatched_rows=7, duplicate_rows=2)
    assert (m.auto_pairs, m.auto_correct) == (3, 2)
    assert m.precision == pytest.approx(2 / 3)
    assert m.recall == pytest.approx(2 / 4)
    assert m.recall_with_candidates == pytest.approx(3 / 4)
    assert (m.unmatched_rows, m.duplicate_rows) == (7, 2)


def test_zero_denominators_are_not_applicable():
    m = metrics.compute(set(), set(), P(("a", "1")))
    assert m.precision is None and m.recall == 0 and m.recall_with_candidates == 0
    m = metrics.compute(P(("a", "1")), set(), set())
    assert m.recall is None and m.recall_with_candidates is None and m.precision == 0


def test_groups_expand_to_every_left_right_pair():
    pairs = metrics.expand_groups({1: (["l1", "l2"], ["r1", "r2", "r3"]), 2: (["l3"], ["r4"])})
    assert len(pairs) == 7 and ("l2", "r3") in pairs
    truth = P(*[(l, r) for l in ("l1", "l2") for r in ("r1", "r2", "r3")])
    m = metrics.compute(pairs, set(), truth)
    assert m.precision == pytest.approx(6 / 7) and m.recall == 1


def test_truth_file_format():
    assert metrics.parse_truth_csv("左の行,右の行\nA,B\nC,D\n".encode()) == P(("A", "B"), ("C", "D"))
    with pytest.raises(metrics.TruthError):
        metrics.parse_truth_csv(b"a,b\n1,2\n")


def test_from_database_uses_automatic_groups_only_for_precision(conn, admin, loaded):
    from close_kit import ingest, matchrun
    from tools import make_samples
    assert ingest.ingest(conn, admin, "帳簿", "l.csv", make_samples.ledger_csv()).ok
    assert ingest.ingest(conn, admin, "銀行", "b.txt", make_samples.bank_zengin()).ok
    matchrun.run_match(conn, admin, "預金照合")
    # truth: ledger T001 <-> bank row 1 (ext id of a zengin row is its record number: 2), T003 <-> 4 is wrong on purpose
    truth = P(("T001", "2"), ("T003", "5"))
    m = metrics.from_database(conn, "sandbox", "預金照合", truth)
    # auto pairs: three 1-to-1 pairs (the instalments of INV-2610-005 are a candidate held by rule 10, so no bundle)
    assert m.auto_pairs == 3 and m.auto_correct == 1
    assert m.precision == pytest.approx(1 / 3) and m.recall == pytest.approx(1 / 2)
    assert m.unmatched_rows == 2 and m.duplicate_rows == 2


def test_from_database_refuses_an_ext_id_shared_by_two_rows(conn, admin, loaded):
    from datetime import date
    from close_kit import ingest, matchrun
    from tools import make_samples, sample_builders as sb
    assert ingest.ingest(conn, admin, "帳簿", "l.csv", make_samples.ledger_csv()).ok
    assert ingest.ingest(conn, admin, "銀行", "b.txt", make_samples.bank_zengin()).ok
    nxt = sb.build_zengin([(date(2026, 11, 2), "in", 1, "ｱ", "", "X")], d_from=date(2026, 11, 1),
                          d_to=date(2026, 11, 5), opening=2040740)
    assert ingest.ingest(conn, admin, "銀行", "n.txt", nxt).ok        # its data record is number 2 as well
    matchrun.run_match(conn, admin, "預金照合")
    with pytest.raises(metrics.TruthError, match="右 2"):
        metrics.from_database(conn, "sandbox", "預金照合", P(("T001", "2")))
