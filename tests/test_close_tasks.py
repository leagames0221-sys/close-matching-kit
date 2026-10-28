"""The task list: due dates counted from the period end (calendar days, weekdays, business days), the holiday table, the
flow with preparer and reviewer, predecessors, completion conditions, and the undoing of a completion."""
import csv
import hashlib
import io
import json
from datetime import date as D, timedelta
from pathlib import Path

import pytest

from close_kit import config, holidays, ingest, matchrun, reasons, roles, tasks, versions
from conftest import load_workbook_into, make_user
from tools import make_samples, sample_builders as sb
from close_helpers import MS, approve_journal, bank_interest_row, group_with, import_november, txn, walk_the_path


def read_holidays() -> dict[D, str]:
    """The tests read the CSV themselves (csv module) - no holiday is written by hand in a test."""
    text = Path(config.HOLIDAY_CSV).read_bytes().decode("cp932")
    out = {}
    for row in list(csv.reader(io.StringIO(text)))[1:]:
        y, m, d = (int(x) for x in row[0].split("/"))
        out[D(y, m, d)] = row[1]
    return out


HOL = read_holidays()
END = D(2026, 10, 31)          # a Saturday


def due(counting, n, end=END, company=()):
    return holidays.due_date(end, counting, n, company_holidays=frozenset(company))


# ---- the holiday table ------------------------------------------------------------------------------------------------

def test_the_table_is_the_downloaded_file_unchanged_and_has_the_stated_range():
    raw = Path(config.HOLIDAY_CSV).read_bytes()
    sources = (Path(__file__).resolve().parent.parent / "docs" / "sources.md").read_text(encoding="utf-8")
    assert hashlib.sha256(raw).hexdigest() in sources and str(len(raw)) in sources.replace(",", "")
    assert raw.count(b"\r\n") == 1068 and raw[:2] != b"\xef\xbb"          # cp932, CRLF, no BOM
    t = holidays.load()
    assert (t.first, t.last) == (D(1955, 1, 1), D(2027, 12, 31))
    assert len(t.names) == len(HOL) == 1067 and min(HOL) == D(1955, 1, 1) and max(HOL) == D(2027, 11, 23)


def test_the_assumptions_of_the_examples_are_facts_of_the_table():
    assert END.weekday() == 5                                   # Saturday
    assert D(2026, 11, 3) in HOL and D(2026, 11, 2) not in HOL and D(2026, 11, 4) not in HOL
    assert D(2027, 1, 1) in HOL and D(2026, 2, 11) in HOL


# ---- counting ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("n", [-1, 0, 1, 5])
def test_calendar_days(n):
    assert due("暦日", n).date == END + timedelta(days=n)


@pytest.mark.parametrize("n, expected", [(-1, D(2026, 10, 30)), (0, END), (1, D(2026, 11, 2)), (5, D(2026, 11, 6)),
                                          (2, D(2026, 11, 3)), (-5, D(2026, 10, 26))])
def test_weekdays_count_monday_to_friday_and_do_not_know_holidays(n, expected):
    assert due("平日", n).date == expected                      # 11-03 is a holiday and still counts as a weekday


@pytest.mark.parametrize("n, expected", [(-1, D(2026, 10, 30)), (0, END), (1, D(2026, 11, 2)), (2, D(2026, 11, 4)),
                                          (5, D(2026, 11, 9))])
def test_business_days_skip_the_national_holiday(n, expected):
    assert due("営業日", n).date == expected                    # 11-03 (a holiday) is skipped


def test_company_holidays_are_skipped_too():
    assert due("営業日", 5, company=[D(2026, 11, 6)]).date == D(2026, 11, 10)
    assert due("営業日", 1, company=[D(2026, 11, 2)]).date == D(2026, 11, 4)
    assert due("暦日", 5, company=[D(2026, 11, 5)]).date == D(2026, 11, 5)         # calendar days ignore them
    assert due("平日", 5, company=[D(2026, 11, 6)]).date == D(2026, 11, 6)


def test_counting_backwards_over_a_holiday_and_a_period_end_before_a_holiday():
    assert due("営業日", -1, end=D(2026, 11, 4)).date == D(2026, 11, 2)            # 11-03 skipped going back
    assert due("営業日", 1, end=D(2026, 2, 10)).date == D(2026, 2, 12)             # 02-11 (Wed) is a holiday
    assert due("平日", 1, end=D(2026, 2, 10)).date == D(2026, 2, 11)


def test_across_the_new_year():
    assert due("営業日", 1, end=D(2026, 12, 31)).date == D(2027, 1, 4)             # 01-01 holiday, 01-02/03 weekend
    assert due("営業日", 1, end=D(2026, 12, 31), company=[D(2027, 1, 4)]).date == D(2027, 1, 5)
    assert due("営業日", 2, end=D(2026, 12, 28), company=[D(2026, 12, 29), D(2026, 12, 30), D(2026, 12, 31)]).date == D(2027, 1, 5)


def test_the_table_range_is_stated_not_guessed():
    assert due("営業日", 0, end=D(2027, 12, 31)).date == D(2027, 12, 31)           # the last day of the table: in range
    assert due("営業日", 1, end=D(2027, 12, 30)).date == D(2027, 12, 31)
    for end, n in ((D(2028, 1, 31), 0), (D(2027, 12, 30), 2), (D(1954, 12, 31), 0), (D(1955, 1, 1), -1)):
        d = due("営業日", n, end=end)
        assert d.date is None and d.note == "祝日表の範囲外", (end, n)
    assert due("暦日", 5, end=D(2030, 1, 31)).date == D(2030, 2, 5)               # no table needed
    assert due("平日", 5, end=D(2030, 1, 31)).date == D(2030, 2, 7)


# ---- generating the tasks of a period ---------------------------------------------------------------------------------------

def test_generation_makes_each_task_once_with_its_due_date(conn, oct_world):
    u = oct_world
    r = tasks.generate(conn, u.admin, "2026-10")
    assert len(r.created) == 7 and r.existing == 0 and r.warnings == []
    again = tasks.generate(conn, u.admin, "2026-10")
    assert again.created == [] and again.existing == 7
    rows = {x["task_id"]: x for x in conn.execute("SELECT * FROM task_instances")}
    # business day 5 after 10-31 with the company holiday 11-06 in the sample: 11-02, 04, 05, 09, 10
    assert rows["T01"]["due_date"] == "2026-11-10"
    assert rows["T07"]["due_date"] == "2026-11-30"                      # calendar days: 10-31 + 30
    assert rows["T02"]["due_date"] == due("営業日", 7, company=[D(2026, 11, 6)]).date.isoformat()
    assert all(r["status"] == "未着手" for r in rows.values())
    assert conn.execute("SELECT COUNT(*) FROM task_events WHERE action = '作成'").fetchone()[0] == 7


def test_generation_rules(conn, oct_world):
    u = oct_world
    with pytest.raises(tasks.TaskError):
        tasks.generate(conn, u.admin, "2026-09")                     # closed period
    with pytest.raises(tasks.TaskError):
        tasks.generate(conn, u.admin, "2030-01")
    for who in (u.maker, u.approver, u.auditor):
        with pytest.raises(roles.PermissionDenied):
            tasks.generate(conn, who, "2026-10")
    assert conn.execute("SELECT COUNT(*) FROM task_instances").fetchone()[0] == 0


def workbook_with(rows, **kw):
    s = make_samples.close_sheets(**kw)
    s["作業一覧"] = rows
    return sb.build_workbook(s)


def task_row(i, name="名", typ="繰り返し", period="", n=1, pre="", prep="demo-maker", rev="demo-approver", cond="なし", ms="", cnt="営業日"):
    return [i, name, typ, period, cnt, n, pre, prep, rev, cond, ms, "はい"]


def test_project_and_ad_hoc_tasks_exist_only_in_their_period_and_a_missing_predecessor_warns(conn, users):
    load_workbook_into(conn, users.admin, workbook_with([
        task_row("A"), task_row("P", typ="プロジェクト", period="2026-11", pre="A"), task_row("B", pre="P")]))
    oct_ = tasks.generate(conn, users.admin, "2026-10")
    assert sorted(r["task_id"] for r in conn.execute("SELECT task_id FROM task_instances")) == ["A", "B"]
    assert len(oct_.warnings) == 1 and "P" in oct_.warnings[0] and "前の作業なし" in oct_.warnings[0]
    # B can be finished although P does not exist in this period
    tasks.prepare(conn, users.maker, conn.execute("SELECT id FROM task_instances WHERE task_id = 'B'").fetchone()[0])
    nov = tasks.generate(conn, users.admin, "2026-11")
    assert sorted(r["task_id"] for r in conn.execute("SELECT task_id FROM task_instances WHERE period = '2026-11'")) == ["A", "B", "P"]
    assert nov.warnings == []


def test_a_task_keeps_the_definition_it_was_made_with(conn, users):
    load_workbook_into(conn, users.admin, workbook_with([task_row("A", prep="demo-maker")]))
    tasks.generate(conn, users.admin, "2026-10")
    vid_old = versions.env_version_id(conn, "sandbox")
    make_user(conn, "maker-new", ["作成者"])
    load_workbook_into(conn, users.admin, workbook_with([task_row("A", prep="maker-new", n=3)]))
    old = json.loads(conn.execute("SELECT definition_json FROM task_instances WHERE period = '2026-10'").fetchone()[0])
    assert (old["preparer"], old["offset"]) == ("demo-maker", 1)
    assert conn.execute("SELECT version_id FROM task_instances").fetchone()[0] == vid_old
    tasks.generate(conn, users.admin, "2026-11")
    new = json.loads(conn.execute("SELECT definition_json FROM task_instances WHERE period = '2026-11'").fetchone()[0])
    assert (new["preparer"], new["offset"]) == ("maker-new", 3)
    # the old one still wants its old preparer
    iid = conn.execute("SELECT id FROM task_instances WHERE period = '2026-10'").fetchone()[0]
    with pytest.raises(tasks.TaskError):
        tasks.prepare(conn, conn_user(conn, "maker-new"), iid)
    tasks.prepare(conn, users.maker, iid)


def conn_user(conn, name):
    from close_kit import auth
    return auth.get_user_by_name(conn, name)


# ---- the flow -------------------------------------------------------------------------------------------------------------

def iid(conn, task_id, period="2026-10"):
    return conn.execute("SELECT id FROM task_instances WHERE task_id = ? AND period = ?", (task_id, period)).fetchone()[0]


def status(conn, task_id, period="2026-10"):
    return conn.execute("SELECT status FROM task_instances WHERE task_id = ? AND period = ?", (task_id, period)).fetchone()[0]


def test_a_task_cannot_be_prepared_before_its_predecessor_is_complete(conn, oct_world):
    u = oct_world
    tasks.generate(conn, u.admin, "2026-10")
    with pytest.raises(tasks.TaskError) as e:
        tasks.prepare(conn, u.maker, iid(conn, "T03"))               # T03 follows T02 which follows T01
    assert "T02" in str(e.value) and "前の作業" in str(e.value)
    tasks.prepare(conn, u.maker, iid(conn, "T01"))
    with pytest.raises(tasks.TaskError):
        tasks.prepare(conn, u.maker, iid(conn, "T02"))               # T01 is 作成完了, not 完了
    tasks.complete(conn, u.approver, iid(conn, "T01"))
    assert status(conn, "T01") == "完了"
    log = conn.execute("SELECT outcome FROM audit_log WHERE action = 'task_prepare' AND outcome = '拒否'").fetchall()
    assert len(log) == 2


def test_only_the_named_preparer_and_reviewer_may_act(conn, oct_world):
    u = oct_world
    tasks.generate(conn, u.admin, "2026-10")
    other_maker = make_user(conn, "maker-z", ["作成者"])
    with pytest.raises(tasks.TaskError):
        tasks.prepare(conn, other_maker, iid(conn, "T01"))           # has the role, is not the preparer of this task
    tasks.prepare(conn, u.maker, iid(conn, "T01"))
    with pytest.raises(tasks.TaskError):
        tasks.complete(conn, u.approver2, iid(conn, "T01"))          # has the role, is not the reviewer of this task
    with pytest.raises(roles.PermissionDenied):
        tasks.complete(conn, u.maker, iid(conn, "T01"))              # the preparer has no reviewer role
    with pytest.raises(roles.PermissionDenied):
        tasks.prepare(conn, u.approver, iid(conn, "T02"))
    assert status(conn, "T01") == "作成完了"


def test_preparer_and_reviewer_must_differ_on_the_operation_too(conn, users):
    both = make_user(conn, "both-user", ["作成者", "承認者"])
    load_workbook_into(conn, users.admin, workbook_with([task_row("A")]))
    tasks.generate(conn, users.admin, "2026-10")
    # a definition edited behind the workbook's back (the workbook refuses it) is still refused here
    d = json.loads(conn.execute("SELECT definition_json FROM task_instances").fetchone()[0])
    d["preparer"] = d["reviewer"] = "both-user"
    conn.execute("UPDATE task_instances SET definition_json = ?", (json.dumps(d, ensure_ascii=False),))
    conn.commit()
    with pytest.raises(tasks.TaskError) as e:
        tasks.prepare(conn, both, 1)
    assert "同じ人" in str(e.value)


def test_send_back_needs_a_reason_and_returns_the_task(conn, oct_world):
    u = oct_world
    tasks.generate(conn, u.admin, "2026-10")
    tasks.prepare(conn, u.maker, iid(conn, "T01"))
    with pytest.raises(tasks.TaskError):
        tasks.send_back(conn, u.approver, iid(conn, "T01"), "")
    with pytest.raises(tasks.TaskError):
        tasks.send_back(conn, u.approver2, iid(conn, "T01"), "x")      # not the reviewer
    tasks.send_back(conn, u.approver, iid(conn, "T01"), "明細が足りない")
    row = conn.execute("SELECT status, note FROM task_instances WHERE id = ?", (iid(conn, "T01"),)).fetchone()
    assert row["status"] == "未着手" and "明細が足りない" in row["note"]
    ev = [(e["action"], e["user_name"], e["detail"]) for e in conn.execute("SELECT * FROM task_events WHERE instance_id = ? ORDER BY id", (iid(conn, "T01"),))]
    assert [a for a, _u, _d in ev] == ["作成", "作成完了", "差し戻し"] and ev[2][1] == "demo-approver"


def run_chain(conn, u, upto):
    for t in ("T01", "T02", "T03", "T04", "T05", "T06")[:upto]:
        tasks.prepare(conn, u.maker, iid(conn, t))
        tasks.complete(conn, u.approver, iid(conn, t))


# ---- completion conditions ---------------------------------------------------------------------------------------------------

def test_the_balance_condition_blocks_until_the_difference_is_zero(conn, oct_world):
    u = oct_world
    tasks.generate(conn, u.admin, "2026-10")
    run_chain(conn, u, 1)
    with pytest.raises(tasks.TaskError) as e:
        tasks.prepare(conn, u.maker, iid(conn, "T02"))
    assert "残高一致の差" in str(e.value) and "41000" not in str(e.value) or "差が" in str(e.value)
    assert status(conn, "T02") == "未着手"
    walk_the_path(conn, u, with_item_for_payment=True)
    tasks.prepare(conn, u.maker, iid(conn, "T02"))
    tasks.complete(conn, u.approver, iid(conn, "T02"))
    assert status(conn, "T02") == "完了"


def test_the_condition_is_checked_again_at_completion(conn, oct_world):
    u = oct_world
    tasks.generate(conn, u.admin, "2026-10")
    run_chain(conn, u, 1)
    steps = walk_the_path(conn, u, with_item_for_payment=True)
    tasks.prepare(conn, u.maker, iid(conn, "T02"))
    conn.execute("UPDATE adjusting_items SET status = '却下' WHERE id = ?", (steps["pay"].id,))      # the difference reappears
    conn.commit()
    with pytest.raises(tasks.TaskError) as e:
        tasks.complete(conn, u.approver, iid(conn, "T02"))
    assert "残高一致の差" in str(e.value)


def test_the_unprocessed_condition_counts_what_is_left(conn, oct_world):
    u = oct_world
    tasks.generate(conn, u.admin, "2026-10")
    conn.execute("UPDATE task_instances SET status = '完了' WHERE task_id IN ('T01', 'T02', 'T03', 'T04', 'T05')")
    conn.commit()                                                     # the earlier tasks are not what this test is about
    with pytest.raises(tasks.TaskError) as e:
        tasks.prepare(conn, u.maker, iid(conn, "T06"))
    msg = str(e.value)
    assert "未照合・重複・候補の行" in msg and "差額の処理待ち が 1 件" in msg
    walk_the_path(conn, u, with_item_for_payment=True)
    tasks.prepare(conn, u.maker, iid(conn, "T06"))
    tasks.complete(conn, u.approver, iid(conn, "T06"))
    assert status(conn, "T06") == "完了"


def test_waiting_approvals_count_as_unprocessed(conn, oct_world):
    u = oct_world
    cfg = versions.load_config(conn, "sandbox")[1]
    base = tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")
    assert base["差額の処理待ち"] == 1 and base["未照合・重複・候補の行（調整項目も仕訳も無い）"] == 3
    fee = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=group_with(conn, "INV-2610-003"))
    item = reasons.apply_reason(conn, u.maker, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿"))
    c = tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")
    assert c["仕訳の承認待ち"] == 1 and c["差額の処理待ち"] == 0 and c["調整項目の承認待ち"] == 1
    assert c["未照合・重複・候補の行（調整項目も仕訳も無い）"] == 3     # a waiting (unapproved) item does not cover its row
    approve_journal(conn, u.maker, u.approver, fee.id)
    reasons.decide_item(conn, u.approver, item.id, True)
    c = tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")
    assert c["仕訳の承認待ち"] == 0 and c["調整項目の承認待ち"] == 0 and c["未照合・重複・候補の行（調整項目も仕訳も無い）"] == 2
    # rows after the period end are not this period's business
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-01")["未照合・重複・候補の行（調整項目も仕訳も無い）"] == 0


def ledger_rows(rows, opening):
    out, bal = [], opening
    for i, d, amt, ref, memo in rows:
        bal += amt
        out.append([i, d, amt, ref, memo, bal])
    return sb.build_csv(make_samples.CLOSE_LEDGER_HEADER, out)


def test_a_completed_task_goes_back_when_a_later_import_breaks_its_condition(conn, users):
    u = users
    load_workbook_into(conn, u.admin, make_samples.close_workbook())
    head = make_samples._CLOSE_LEDGER_OCT[:3]
    tail = make_samples._CLOSE_LEDGER_OCT[3:]
    assert ingest.ingest(conn, u.admin, "帳簿", "a.csv", ledger_rows(head, make_samples.CLOSE_OPENING)).ok
    assert ingest.ingest(conn, u.admin, "銀行", "b.txt", make_samples.close_bank_oct()).ok
    matchrun.run_match(conn, u.admin, MS)
    fee = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=group_with(conn, "INV-2610-003"))
    interest = reasons.apply_reason(conn, u.maker, MS, "R03", txn_id=bank_interest_row(conn))
    approve_journal(conn, u.maker, u.approver, fee.id)
    approve_journal(conn, u.maker, u.approver, interest.id)
    tasks.generate(conn, u.admin, "2026-10")
    run_chain(conn, u, 6)
    assert all(status(conn, t) == "完了" for t in ("T01", "T02", "T03", "T04", "T05", "T06"))
    # the rest of October's ledger arrives later: the difference and the unprocessed rows are back
    opening = make_samples.CLOSE_OPENING + sum(r[2] for r in head)
    assert ingest.ingest(conn, u.admin, "帳簿", "b.csv", ledger_rows(tail, opening)).ok
    assert status(conn, "T02") == "作成完了" and status(conn, "T06") == "作成完了"
    assert status(conn, "T01") == "完了" and status(conn, "T03") == "完了"           # tasks without a condition stay
    note = conn.execute("SELECT note FROM task_instances WHERE task_id = 'T02'").fetchone()[0]
    assert "完了の条件が崩れた" in note and "残高一致の差" in note
    ev = conn.execute("SELECT user_name, action, detail FROM task_events WHERE instance_id = ? ORDER BY id DESC LIMIT 1",
                      (iid(conn, "T02"),)).fetchone()
    assert (ev["user_name"], ev["action"]) == ("(システム)", "完了の取り消し") and "残高一致の差" in ev["detail"]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'task_decertify'").fetchone()[0] == 2
    items = tasks.list_instances(conn, u.admin, today=D(2026, 11, 20))
    t2 = next(i for i in items if i["def"]["id"] == "T02")
    assert t2["condition_problems"] and t2["row"]["note"]
    # fixing the cause lets the reviewer complete it again
    matchrun.run_match(conn, u.admin, MS)
    reasons.decide_item(conn, u.approver, reasons.apply_reason(conn, u.maker, MS, "R05", txn_id=txn(conn, "PAY-2610-02", "帳簿")).id, True)
    reasons.decide_item(conn, u.approver, reasons.apply_reason(conn, u.maker, MS, "R04", txn_id=txn(conn, "INV-2610-009", "帳簿")).id, True)
    tasks.complete(conn, u.approver, iid(conn, "T02"))
    assert status(conn, "T02") == "完了"


def test_events_after_the_period_end_do_not_undo_the_tasks_of_that_period(conn, oct_world):
    u = oct_world
    walk_the_path(conn, u, with_item_for_payment=True)
    tasks.generate(conn, u.admin, "2026-10")
    run_chain(conn, u, 6)
    import_november(conn, u.admin)                                    # new bundles, a new run, resolved items: all after 10-31
    assert all(status(conn, t) == "完了" for t in ("T01", "T02", "T03", "T04", "T05", "T06"))
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'task_decertify'").fetchone()[0] == 0


def test_overdue_tasks_are_flagged(conn, oct_world):
    u = oct_world
    tasks.generate(conn, u.admin, "2026-10")
    items = {i["def"]["id"]: i for i in tasks.list_instances(conn, u.auditor, today=D(2026, 11, 11))}
    assert items["T01"]["overdue"] and not items["T06"]["overdue"]              # T01 is due 11-10, T06 later


def test_a_difference_of_a_pair_paid_after_the_period_end_does_not_undo_that_periods_tasks(conn, oct_world):
    """The month-end receipt arrives in November 50 short: the difference is November's (its journal takes the bank
    row's date), so October's conditions stay as they were."""
    from test_close_statement import nov_bank_with_a_short_receipt
    u = oct_world
    walk_the_path(conn, u, with_item_for_payment=True)
    tasks.generate(conn, u.admin, "2026-10")
    run_chain(conn, u, 6)
    assert ingest.ingest(conn, u.admin, "銀行", "n.txt", nov_bank_with_a_short_receipt()).ok
    matchrun.run_match(conn, u.admin, MS)
    cfg = versions.load_config(conn, "sandbox")[1]
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")["差額の処理待ち"] == 0
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-11-30")["差額の処理待ち"] == 1
    reasons.apply_reason(conn, u.maker, MS, "R01", group_id=group_with(conn, "INV-2610-009"))
    matchrun.run_match(conn, u.admin, MS)
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")["仕訳の承認待ち"] == 0
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-11-30")["仕訳の承認待ち"] == 1
    assert all(status(conn, t) == "完了" for t in ("T01", "T02", "T03", "T04", "T05", "T06"))
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'task_decertify'").fetchone()[0] == 0


def test_a_difference_of_a_pair_inside_the_period_still_holds_it_up(conn, oct_world):
    cfg = versions.load_config(conn, "sandbox")[1]
    # the fee pair: books 10-04, bank 10-05 - its difference is October's
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")["差額の処理待ち"] == 1
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-04")["差額の処理待ち"] == 0
