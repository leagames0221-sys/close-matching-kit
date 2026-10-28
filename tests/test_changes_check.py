"""The test that runs by itself after the pre-approval of a change request: production's data before and after, the sample
path, and what the changed sheets do. It runs on throw-away databases and needs nothing but what git holds."""
import hashlib
import json
import subprocess

import pytest

from close_kit import auth, changecheck, changes, config, journals, versions
from conftest import load_workbook_into
from close_helpers import PROD
from tools import make_samples, sample_builders as sb

MS = "預金照合"


def sheets_with(rows_edit=None, **names):
    s = make_samples.close_sheets(**names)
    if rows_edit:
        rows_edit(s)
    return s


def run_request(conn, users, wb: bytes, *, title="試験"):
    """A request for `wb`, put forward and pre-approved; returns (request id, the stored test result)."""
    vid = load_workbook_into(conn, users.admin, wb)
    rid = changes.create_draft(conn, users.admin, title=title, to_version_id=vid, justification="試験の準備")
    changes.submit(conn, users.admin, rid)
    changes.pre_decide(conn, users.admin2, rid, True)
    t = changes.latest_test(conn, rid)
    return rid, t, json.loads(t["result_json"])


def dump(conn):
    """Every row of every table, as text (the data of both environments and everything else)."""
    return "\n".join(conn.iterdump())


def tolerance_zero(s):
    s["パスルール"][0][7] = 0          # 参照が同じ: amount tolerance 100 -> 0 (the fee of 50 is no longer inside it)


# ---- 1. production's data, before and after -----------------------------------------------------------------------------

def import_only(conn, users):
    """The October files imported into production and not matched yet (a match run would settle most of them: a group that
    is done, or waits for its difference, is kept by every later run, with the old rules or the new)."""
    from close_kit import ingest
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", make_samples.close_ledger_oct(), PROD).ok
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct(), PROD).ok


def test_a_pass_rule_change_moves_rows_between_classes_in_a_copy_of_production(conn, prod_empty):
    u = prod_empty
    import_only(conn, u)
    rows_before = conn.execute("SELECT COUNT(*) FROM match_rows").fetchone()[0]
    wb = sb.build_workbook(sheets_with(tolerance_zero))
    rid, t, res = run_request(conn, u, wb)
    s1 = res["step1"]
    assert s1["status"] == "実施" and s1["rows"] == 9
    assert s1["before"]["自動"] == 6 and s1["after"]["自動"] == 4 and s1["after"]["候補"] == 2   # the fee of 50 needs the tolerance
    assert s1["before"]["候補"] == 0 and s1["before"]["未照合"] == s1["after"]["未照合"] == 3
    assert sum(s1["before"].values()) == sum(s1["after"].values()) == 9      # the same rows, other classes
    assert s1["changed_count"] == 2 and len(s1["changed"]) == 2
    row = s1["changed"][0]
    assert {"match_set", "txn_id", "source", "date", "amount_minor", "reference", "before", "after"} <= set(row)
    assert (row["before"], row["after"]) == ("自動", "候補") and row["reference"] == "INV-2610-003"
    # the stored result is the one the screen shows, and it names both versions
    assert t["base_version_id"] == versions.env_version_id(conn, PROD) and t["to_version_id"] != t["base_version_id"]
    assert res["step3"]["changed_sheets"] == ["パスルール"]
    assert res["step3"]["pass_rules"]["changed_count"] == 2
    assert conn.execute("SELECT COUNT(*) FROM match_rows").fetchone()[0] == rows_before == 0   # production was not matched


def test_a_group_that_is_settled_in_production_is_not_reclassified_by_the_test_as_a_match_run_would_not(conn, oct_prod):
    u = oct_prod                                                            # matched: the fee pair waits for its difference
    _rid, _t, res = run_request(conn, u, sb.build_workbook(sheets_with(tolerance_zero)))
    s1 = res["step1"]
    assert s1["status"] == "実施" and s1["changed_count"] == 0 and s1["before"] == s1["after"]


def test_a_change_that_touches_nothing_in_the_data_leaves_every_class_as_it_was(conn, oct_prod):
    u = oct_prod
    s = make_samples.close_sheets()
    s["説明"][0][1] = "社名だけ変えた会社（架空）"
    _rid, _t, res = run_request(conn, u, sb.build_workbook(s))
    s1 = res["step1"]
    assert s1["status"] == "実施" and s1["before"] == s1["after"] and s1["changed_count"] == 0 and s1["changed"] == []
    assert res["step3"]["pass_rules"] is None


def test_with_no_data_in_production_the_result_says_so(conn, prod_empty):
    s = make_samples.close_sheets()
    s["説明"][0][1] = "社名だけ変えた会社（架空）"
    _rid, _t, res = run_request(conn, prod_empty, sb.build_workbook(s))
    assert res["step1"]["status"] == "データなし" and res["step1"]["rows"] == 0


def test_the_first_request_runs_the_sample_path_only(conn, users):
    _rid, t, res = run_request(conn, users, make_samples.close_workbook())
    assert res["base_version_id"] is None and t["base_version_id"] is None and t["base_sha256"] == ""
    assert res["step1"]["status"] == "対象外" and res["step3"]["status"] == "対象外"
    s2 = res["step2"]
    assert s2["status"] == "実施" and t["result_status"] == "成功"
    assert s2["statements"]["2026-10"]["difference"] == 0 and s2["statements"]["2026-11"]["difference"] == 0
    assert s2["approved_journals"] == 2 and s2["adjusting_items"] == 1


# ---- 2. the sample path ----------------------------------------------------------------------------------------------------

def test_a_version_whose_data_sources_differ_from_the_sample_is_not_walked(conn, users):
    s = make_samples.close_sheets()
    s["データ源"][0][4] = 100                           # another unit: the files of samples/ no longer fit
    s["データ源"][1][4] = 100
    _rid, _t, res = run_request(conn, users, sb.build_workbook(s))
    assert res["step2"]["status"] == "対象外" and "multiplier" in res["step2"]["note"]


def test_a_change_that_stops_the_sample_path_is_a_result_named_failure_by_the_change(conn, users):
    """The route no longer names the sample's approver: the path stops at his approval. That is the change's effect, so
    it is recorded as a result and waits for the decision (the person who decides needs to see it)."""
    wb = make_samples.close_workbook(approvers=("demo-approver2",), senior=("demo-approver2",))
    rid, t, res = run_request(conn, users, wb)
    assert changes.state_of(conn, rid) == config.CR_TESTED                  # not stuck at 試験待ち
    assert t["result_status"] == "失敗（変更の影響）" == changecheck.STATUS_FAIL
    s2 = res["step2"]
    assert s2["status"] == changecheck.STATUS_FAIL and "承認する" in s2["step"] and s2["note"]
    changes.decide_result(conn, users.approver, rid, True)                  # it can still be approved (or rejected)
    assert changes.decide_result(conn, users.admin2, rid, True) == config.CR_RESULT_OK


def test_a_fault_of_the_tool_is_not_a_result_and_the_test_can_be_run_again(conn, users, monkeypatch):
    vid = load_workbook_into(conn, users.admin, make_samples.close_workbook())
    rid = changes.create_draft(conn, users.admin, title="t", to_version_id=vid, justification="r")
    changes.submit(conn, users.admin, rid)
    real = changecheck.walk_sample

    def broken(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(changecheck, "walk_sample", broken)
    with pytest.raises(changecheck.ToolError):
        changes.pre_decide(conn, users.admin2, rid, True)
    assert changes.state_of(conn, rid) == config.CR_WAIT_TEST                # approved before the test, but not tested
    assert conn.execute("SELECT COUNT(*) FROM change_tests").fetchone()[0] == 0
    with pytest.raises(changes.ChangeError):
        changes.decide_result(conn, users.approver, rid, True)               # nothing to approve
    monkeypatch.setattr(changecheck, "walk_sample", real)
    assert changes.retest(conn, users.admin2, rid) == config.CR_TESTED
    assert changes.latest_test(conn, rid)["result_status"] == "成功"


def test_the_test_is_written_down_with_the_versions_the_copy_time_and_the_sample_files(conn, oct_prod):
    u = oct_prod
    s = make_samples.close_sheets()
    s["説明"][0][1] = "社名だけ変えた会社（架空）"
    _rid, t, _res = run_request(conn, u, sb.build_workbook(s))
    base = conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (t["base_version_id"],)).fetchone()[0]
    to = conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (t["to_version_id"],)).fetchone()[0]
    assert t["base_sha256"] == hashlib.sha256(base.encode("utf-8")).hexdigest()
    assert t["to_sha256"] == hashlib.sha256(to.encode("utf-8")).hexdigest()
    assert t["prod_copy_at"] and t["run_at"]
    samples = json.loads(t["samples_json"])
    assert samples == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(changecheck.SAMPLES_DIR.iterdir())
                       if p.is_file()}
    assert "sample_workbook.xlsx" in samples and "ledger_2026-10.csv" in samples


# ---- 3. what the changed sheets do -------------------------------------------------------------------------------------------

def test_the_people_who_may_approve_before_and_after_at_every_edge_of_the_ranges(conn, oct_prod):
    u = oct_prod
    new = make_samples.close_workbook(threshold=200000)                         # the edge moves from 100,000 to 200,000
    # a journal waiting for approval in production that the move concerns: 150,000
    j = journals.create_manual(conn, u.maker, "2026-10-20", "大きな仕訳", [
        {"account": "6100", "debit": 150000, "credit": 0, "memo": ""},
        {"account": "1200", "debit": 0, "credit": 150000, "memo": ""}], env=PROD)
    journals.submit(conn, u.maker, j, PROD)
    number = conn.execute("SELECT number FROM journals WHERE id = ?", (j,)).fetchone()[0]
    _rid, _t, res = run_request(conn, u, new)
    rt = res["step3"]["approval_route"]
    by = {b["amount"]: (b["before"], b["after"]) for b in rt["boundaries"]}
    assert {0, 99999, 100000, 199999, 200000} <= set(by)
    assert by[99999] == (["demo-approver", "demo-approver2"], ["demo-approver", "demo-approver2"])
    assert by[100000] == (["demo-approver2"], ["demo-approver", "demo-approver2"])        # only the senior before
    assert by[199999] == (["demo-approver2"], ["demo-approver", "demo-approver2"])
    assert by[200000] == (["demo-approver2"], ["demo-approver2"])
    assert [(x["journal"], x["before"], x["after"]) for x in rt["open_journals"]] == [
        (number, ["demo-approver2"], ["demo-approver", "demo-approver2"])]


def test_the_unfinished_tasks_of_production_show_their_due_date_and_condition_before_and_after(conn, oct_prod):
    from close_kit import tasks
    u = oct_prod
    tasks.generate(conn, u.admin, "2026-10", PROD)
    s = make_samples.close_sheets()
    s["作業一覧"][0][5] = 8                                                         # the first task: due 8 days after the period end
    _rid, _t, res = run_request(conn, u, sb.build_workbook(s))
    moved = res["step3"]["tasks"]
    assert len(moved) == 1 and moved[0]["task_id"] == s["作業一覧"][0][0]
    assert moved[0]["due_before"] != moved[0]["due_after"] and moved[0]["period"] == "2026-10"


# ---- nothing live is touched ------------------------------------------------------------------------------------------------------

def test_the_test_changes_no_row_of_sandbox_or_production_and_no_global_value(conn, oct_prod):
    u = oct_prod
    load_workbook_into(conn, u.admin, make_samples.close_workbook())            # something in the sandbox as well
    vid = load_workbook_into(conn, u.admin, sb.build_workbook(sheets_with(tolerance_zero)))
    scrypt = auth.SCRYPT_N
    before = dump(conn)
    out = changecheck.run(conn, u.admin2, vid)
    assert out.status in ("成功", changecheck.STATUS_FAIL)
    assert dump(conn) == before                                                  # every table, both environments
    assert auth.SCRYPT_N == scrypt
    assert out.samples and out.prod_copy_at


def test_only_what_git_holds_is_used(conn, users):
    """The test reads `samples/` only: every file in it is tracked by git, so a PC with a clone has all of it (the data
    of the Splink check is not needed and not read)."""
    digest = changecheck.samples_digest()
    try:
        tracked = set(subprocess.run(["git", "ls-files", "samples"], cwd=changecheck.SAMPLES_DIR.parent, capture_output=True,
                                     text=True, check=True).stdout.split())
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available here")
    assert {f"samples/{n}" for n in digest} <= tracked
    src = (changecheck.__file__ and open(changecheck.__file__, encoding="utf-8").read())
    assert "external" not in src and "splink" not in src.lower().replace("run_splink", "")
    assert changecheck.SAMPLE_FILES and all((changecheck.SAMPLES_DIR / n).is_file() for n in changecheck.SAMPLE_FILES.values())
