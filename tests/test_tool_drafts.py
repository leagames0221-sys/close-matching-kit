"""A pair inside the tolerance whose rule names an account for the difference (差額の相手勘定): the match is 完了 at
once and the tool drafts the journal of the difference itself (the author's decision; Odoo says, of its underpaid-invoice model,
"The difference is reconciled with the account indicated in the counterpart entries tab"; Oracle: "any amount difference
within tolerance is automatically created as an external transaction"). The journal goes through the ordinary approval: its maker is the tool's own user, so every
approver is a person other than the maker (Financial Services Agency: approval by someone other than the maker)."""
import pytest

from close_kit import auth, balance, config, db, ingest, journals, matchrun, reasons, roles, tasks, versions, workbook
from conftest import load_workbook_into, make_user
from close_helpers import MS, approve_journal, bank_interest_row, group_with, statement, txn
from tools import make_samples, sample_builders as sb

ACCOUNT_WB = make_samples.close_workbook(diff_account="6100")


@pytest.fixture()
def tool_world(conn, users):
    """The closing sample as it is shipped (rule 10 names 6100), October files imported and matched."""
    load_workbook_into(conn, users.admin, ACCOUNT_WB)
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", make_samples.close_ledger_oct()).ok
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct()).ok
    summary = matchrun.run_match(conn, users.admin, MS)
    return users, summary


def tool_journal(conn):
    return conn.execute("SELECT * FROM journals WHERE method = '照合の差額'").fetchall()


def test_the_pair_is_done_at_once_and_the_tool_drafts_the_journal_of_its_difference(conn, tool_world):
    u, summary = tool_world
    gid = group_with(conn, "INV-2610-003")
    g = conn.execute("SELECT * FROM match_groups WHERE id = ?", (gid,)).fetchone()
    assert (g["kind"], g["state"], g["diff_minor"]) == ("自動", "完了", 50)       # 照合は完了, not 差額の処理待ち
    (j,) = tool_journal(conn)
    assert (j["status"], j["method"], j["link_group_id"], j["reason_code"], j["entry_date"], j["match_set"]) == \
        ("下書き", "照合の差額", gid, "", "2026-10-05", MS)
    assert [(l["account_code"], l["debit"], l["credit"]) for l in journals.lines_of(conn, j["id"])] == \
        [("6100", 50, 0), ("1100", 0, 50)]                   # bank - book = -50: the fee account is debited
    assert (summary.drafted, summary.notes) == (1, [])
    assert summary.groups["自動/完了"] == 3 and "自動/差額の処理待ち" not in summary.groups   # INV-001, INV-003, PAY-01


def test_the_maker_is_the_tool_and_nobody_can_log_in_as_it(conn, tool_world):
    u, _ = tool_world
    (j,) = tool_journal(conn)
    maker = conn.execute("SELECT id, name, roles, pw_hash FROM users WHERE id = ?", (j["created_by"],)).fetchone()
    assert maker["name"] == config.SYSTEM_USER_NAME and maker["roles"] == ""
    detail = journals.journal_detail(conn, u.auditor, j["id"])[0]
    assert detail["created_name"] == config.SYSTEM_USER_NAME
    assert auth.authenticate(conn, config.SYSTEM_USER_NAME, "!") is None
    assert auth.authenticate(conn, config.SYSTEM_USER_NAME, "") is None
    with pytest.raises(ValueError):
        auth.create_user(conn, config.SYSTEM_USER_NAME, "password-abcdef", ["承認者"])
    log = conn.execute("SELECT user_name, detail FROM audit_log WHERE action = 'journal_draft_auto'").fetchone()
    assert log["user_name"] == config.SYSTEM_USER_NAME and "JE-000001" in log["detail"] and u.admin.name in log["detail"]


def test_a_second_run_does_not_draft_the_journal_again(conn, tool_world):
    u, _ = tool_world
    again = matchrun.run_match(conn, u.admin, MS)
    assert again.drafted == 0
    assert len(tool_journal(conn)) == 1 and conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 1


def test_a_person_with_the_role_maker_submits_it_and_an_approver_decides_it(conn, tool_world):
    u, _ = tool_world
    (j,) = tool_journal(conn)
    with pytest.raises(roles.PermissionDenied):
        journals.submit(conn, u.approver, j["id"])                       # the role 作成者 is still needed
    journals.submit(conn, u.maker, j["id"])
    assert journals.get(conn, j["id"], "sandbox")["status"] == "承認待ち"
    assert journals.decide(conn, u.approver, j["id"], True) == "承認済み"
    gid = group_with(conn, "INV-2610-003")
    assert conn.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "完了"
    assert conn.execute("SELECT created_by, decided_by FROM journals WHERE id = ?", (j["id"],)).fetchone()["created_by"] \
        != u.approver.id


def test_every_approver_is_a_person_other_than_the_maker_even_the_one_who_submitted(conn, users):
    both = make_user(conn, "both-user", ["作成者", "承認者"])
    load_workbook_into(conn, users.admin, make_samples.close_workbook(approvers=("both-user",), senior=("both-user",),
                                                                       diff_account="6100"))
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", make_samples.close_ledger_oct()).ok
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct()).ok
    matchrun.run_match(conn, users.admin, MS)
    (j,) = tool_journal(conn)
    journals.submit(conn, both, j["id"])
    assert journals.can_decide(versions.load_config(conn, "sandbox")[1], both, j["created_by"], j["debit_total"]) is None
    assert journals.decide(conn, both, j["id"], True) == "承認済み"


def test_the_sample_path_with_the_tool_drafted_fee_journal_reaches_a_difference_of_zero(conn, users, tmp_path):
    from close_kit import changecheck
    files = {"ledger_oct": make_samples.close_ledger_oct(), "bank_oct": make_samples.close_bank_oct(),
             "ledger_nov": make_samples.close_ledger_nov(), "bank_nov": make_samples.close_bank_nov()}
    w = changecheck.walk_sample(ACCOUNT_WB, files, tmp_path)
    assert "道具の下書き" in w.steps[3] or any("道具の下書き" in s for s in w.steps)
    oct_, nov = w.statements["2026-10"], w.statements["2026-11"]
    assert (oct_.bank_end, oct_.adjusted_bank, oct_.adjusted_book, oct_.difference) == \
        (1_474_987, 1_503_987, 1_503_987, 0)
    assert nov.difference == 0
    assert (w.approved_journals, w.adjusting_items) == (2, 1)            # the fee (tool) and the interest (reason code)
    # the same figures when the rule names no account and a person uses the reason code R01
    w2 = changecheck.walk_sample(make_samples.close_workbook(), files, tmp_path / "2")
    assert (w2.statements["2026-10"].difference, w2.approved_journals, w2.adjusting_items) == (0, 2, 1)


def test_while_the_draft_waits_the_statement_lists_the_difference_and_the_condition_counts_it(conn, tool_world):
    u, _ = tool_world
    st = statement(conn)
    assert [(l.amount, l.status) for l in st.unprocessed_groups] == [(-50, "完了（仕訳 JE-000001 は下書き）")]
    _vid, cfg = versions.load_config(conn, "sandbox")
    counts = tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")
    assert counts["仕訳の承認待ち"] == 1
    (j,) = tool_journal(conn)
    journals.submit(conn, u.maker, j["id"])
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")["仕訳の承認待ち"] == 1      # waiting now
    journals.decide(conn, u.approver, j["id"], True)
    assert tasks.unprocessed_counts(conn, "sandbox", cfg, MS, "2026-10-31")["仕訳の承認待ち"] == 0
    assert statement(conn).unprocessed_groups == []


def test_a_rejected_or_withdrawn_draft_gives_the_pair_back_to_the_reason_code(conn, tool_world):
    u, _ = tool_world
    gid = group_with(conn, "INV-2610-003")
    (j,) = tool_journal(conn)
    journals.submit(conn, u.maker, j["id"])
    assert journals.decide(conn, u.approver, j["id"], False, "勘定が違う") == "却下"
    assert conn.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "差額の処理待ち"
    res = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=gid)             # the person's route is open again
    assert res.kind == "仕訳"
    approve_journal(conn, u.maker, u.approver, res.id)
    assert conn.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "完了"
    assert statement(conn).unprocessed_groups == []
    assert [r["id"] for r in tool_journal(conn)] == [j["id"]]                     # no second tool draft


def test_a_draft_can_be_withdrawn_by_any_maker_and_the_pair_waits_again(conn, tool_world):
    u, _ = tool_world
    gid = group_with(conn, "INV-2610-003")
    (j,) = tool_journal(conn)
    with pytest.raises(roles.PermissionDenied):
        journals.withdraw(conn, u.approver, j["id"])
    journals.withdraw(conn, u.maker, j["id"])
    assert journals.get(conn, j["id"], "sandbox")["status"] == "取り下げ"
    assert conn.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "差額の処理待ち"
    # the next run does not draft it again (the group is fixed in 差額の処理待ち)
    assert matchrun.run_match(conn, u.admin, MS).drafted == 0


def test_a_person_made_journal_still_can_be_submitted_only_by_its_maker(conn, oct_world):
    u = oct_world
    res = reasons.apply_reason(conn, u.maker, MS, "R01", group_id=group_with(conn, "INV-2610-003"))
    other = make_user(conn, "maker2", ["作成者"])
    with pytest.raises(journals.JournalError):
        journals.submit(conn, other, res.id)
    with pytest.raises(journals.JournalError):
        journals.withdraw(conn, other, res.id)


def test_a_pair_inside_the_tolerance_of_a_rule_without_an_account_waits_as_before(conn, oct_world):
    u = oct_world                                                   # the tests' default sample names no account
    g = conn.execute("SELECT state FROM match_groups WHERE id = ?", (group_with(conn, "INV-2610-003"),)).fetchone()
    assert g["state"] == "差額の処理待ち" and tool_journal(conn) == []


def _closed_all_periods():
    s = make_samples.close_sheets(diff_account="6100")
    s["会計期間"] = [[p[0], p[1], p[2], "はい"] for p in s["会計期間"]]
    return sb.build_workbook(s)


def test_when_no_journal_can_be_drafted_the_pair_waits_for_a_person_and_the_run_says_why(conn, users):
    load_workbook_into(conn, users.admin, _closed_all_periods())
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", make_samples.close_ledger_oct()).ok
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct()).ok
    summary = matchrun.run_match(conn, users.admin, MS)
    gid = group_with(conn, "INV-2610-003")
    assert conn.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "差額の処理待ち"
    assert tool_journal(conn) == [] and summary.drafted == 0
    assert len(summary.notes) == 1 and "仕訳の下書きを作れなかった" in summary.notes[0] and "締めてあり" in summary.notes[0]


def test_the_rule_with_an_account_is_checked_when_the_workbook_is_loaded():
    def errors(changes=None):
        s = make_samples.close_sheets(diff_account="6100")
        for (row, col), v in (changes or {}).items():
            s["パスルール"][row] = list(s["パスルール"][row])
            s["パスルール"][row] += [""] * (14 - len(s["パスルール"][row]))
            s["パスルール"][row][col] = v
        return [str(e) for e in workbook.parse_workbook(sb.build_workbook(s)).errors]
    assert errors() == []
    assert any("勘定科目に無いコード" in e for e in errors({(0, 13): "9999"}))
    assert any("預金の勘定" in e for e in errors({(0, 13): "1100"}))
    assert any("数式のセルは使えない" in e for e in errors({(0, 13): "=1+1"}))        # the workbook's own rule for formulas
    assert any("自動" in e and "1対1" in e for e in errors({(2, 13): "6100"}))            # rule 30 is a 候補 rule
    assert any("1対1" in e for e in errors({(3, 13): "6100"}))                           # rule 40 is a bundle rule


def test_a_workbook_made_before_the_column_existed_is_still_read():
    s = make_samples.close_sheets()
    hdr = list(sb.HEADERS["パスルール"])
    hdr.remove("差額の相手勘定")
    old = sb.build_workbook(s, headers={"パスルール": hdr})
    res = workbook.parse_workbook(old)
    assert res.ok and all(r["diff_account"] == "" for r in res.config["pass_rules"])
    hdr2 = [h for h in sb.HEADERS["説明"] if not h.startswith("期日の日数")]
    res2 = workbook.parse_workbook(sb.build_workbook({"説明": [r[:6] for r in s["説明"]]}, headers={"説明": hdr2}))
    assert res2.ok and (res2.config["description"]["task_offset_min"], res2.config["description"]["task_offset_max"]) \
        == (config.TASK_OFFSET_MIN, config.TASK_OFFSET_MAX)


def test_the_tool_does_not_change_the_group_ids_or_the_rows_of_a_fixed_pair(conn, tool_world):
    u, _ = tool_world
    gid = group_with(conn, "INV-2610-003")
    rows = {r["txn_id"] for r in conn.execute("SELECT txn_id FROM match_group_rows WHERE group_id = ?", (gid,))}
    matchrun.run_match(conn, u.admin, MS)
    assert group_with(conn, "INV-2610-003") == gid
    assert {r["txn_id"] for r in conn.execute("SELECT txn_id FROM match_group_rows WHERE group_id = ?", (gid,))} == rows
