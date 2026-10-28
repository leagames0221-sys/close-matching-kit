"""Reconify's evals (MIT, tests/external/reconify) run through this repository's rules.

The comparison and the reasons for each difference live in tools/reconify_evals.py (and are written to
docs/validation/reconify_evals.md). Here: the evals that can be expressed must give the expected pairs,
except the ones whose difference is a documented rule of this repository without a source (docs/sources.md).
"""
import pytest

from tools import reconify_evals as ev

SAME = ["001-missing-payment", "004-timing-difference", "010-accounting-negatives"]
BUNDLES = ["005-one-to-many", "006-many-to-many"]


@pytest.mark.parametrize("name", SAME)
def test_expected_pairs_are_reproduced(name, tmp_path):
    r = ev.compare(name, tmp_path)
    assert r["diffs"] == [] and r["notes"] == []


@pytest.mark.parametrize("name", BUNDLES)
def test_bundle_evals_give_the_expected_groups_and_only_the_duplicate_mark_differs(name, tmp_path):
    """Each eval lists one pass in Reconify's reference yaml and is written as that one rule. The rows of a bundle share
    a reference, so this tool marks them as duplicates; Reconify moves its duplicate key away with `group_col`."""
    r = ev.compare(name, tmp_path)
    assert [d for d in r["diffs"] if not d.startswith("duplicates:")] == [] and r["notes"] == []
    assert len(r["diffs"]) == 1 and r["expected"]["duplicates"] == [] and r["ours"]["duplicates"] != []
    assert r["reason"]


def test_003_same_categories_and_the_difference_within_tolerance_is_done_with_its_journal_drafted(tmp_path):
    r = ev.compare("003-settlement-fee", tmp_path)
    assert r["diffs"] == [] and r["notes"] == []          # the pair is 完了, as Reconify's matched
    assert len(r["drafts"]) == 1 and "照合の差額" in r["drafts"][0] and "下書き" in r["drafts"][0]
    assert r["reason"]


def test_002_duplicates_are_matched_with_a_mark_and_two_possible_partners_make_a_candidate(tmp_path):
    r = ev.compare("002-duplicate", tmp_path)
    assert r["ours"]["matched"] == [] and r["ours"]["unmatched_left"] == [] and r["ours"]["unmatched_right"] == []
    assert r["ours"]["ambiguous"] == [((1, 2), (1,))]
    assert r["ours"]["duplicates"] == r["expected"]["duplicates"] == [("L", 1), ("L", 2)]
    assert r["reason"]


def test_008_is_recorded_as_not_expressible(tmp_path):
    r = ev.compare("008-ambiguous-reference", tmp_path)
    assert r["expressible"] is False and "tokens" in r["reason"]


def test_every_fetched_eval_is_covered():
    assert set(ev.all_evals()) == set(SAME) | set(BUNDLES) | {"002-duplicate", "003-settlement-fee",
                                                              "008-ambiguous-reference"}
