"""Screen details of the period-end features: which reason codes a row is offered, thousands separators on every
amount that a person reads, and the account names beside the account codes of a journal."""
import re
from types import SimpleNamespace

from close_kit import manual, roles, tasks, versions
from close_helpers import MS, txn
from test_close_roles_web import names_workbook
from test_web import env, login, post  # noqa: F401  (env is a fixture)
from tools import make_samples, sample_builders as sb


def month_end_world(env):
    """The workbook, the October files and a match run, through the screens. Returns the clients by name."""
    app, conn, _users = env
    admin, maker, approver = (login(app, n) for n in ("admin", "maker", "approver"))
    assert post(admin, "/workbook", files={"file": ("w.xlsx", names_workbook())}).status_code == 200
    for src, name, data in (("帳簿", "l.csv", make_samples.close_ledger_oct()), ("銀行", "b.txt", make_samples.close_bank_oct())):
        assert post(admin, "/ingest", {"source": src}, {"file": (name, data)}, "/ingest").status_code == 200
    assert post(admin, "/match/run", {"match_set": MS}, path_for_token="/match").status_code == 200
    return SimpleNamespace(app=app, conn=conn, admin=admin, maker=maker, approver=approver)


def table_rows(html: str) -> list[str]:
    return re.findall(r"<tr[ >].*?</tr>", html, flags=re.S)


def offered_codes(row_html: str) -> list[str]:
    select = re.search(r'<select name="reason_code">(.*?)</select>', row_html, flags=re.S)
    return re.findall(r'<option value="([^"]+)"', select.group(1)) if select else []


def num_cells(html: str) -> list[str]:
    return re.findall(r'<td class="num">([^<]*)</td>', html)


# ---- 1. a row of the bank's side is offered the reason codes whose action is a journal only -------------------

def test_a_bank_side_row_is_not_offered_the_reason_codes_that_make_an_adjusting_item(env):
    w = month_end_world(env)
    _vid, cfg = versions.load_config(w.conn, "sandbox")
    journal_codes = [r["code"] for r in cfg["reason_codes"] if r["enabled"] and r["action"] == "仕訳"]
    all_codes = [r["code"] for r in cfg["reason_codes"] if r["enabled"]]
    assert "R04" in all_codes and "R04" not in journal_codes                # the premise: R04 / R05 are adjusting items
    page = w.maker.get("/reasons?ms=" + MS).text
    section = page[page.index("<h2>未照合の行</h2>"):]
    bank = [r for r in table_rows(section) if "<td>銀行</td>" in r]
    book = [r for r in table_rows(section) if "<td>帳簿</td>" in r]
    assert bank and book
    for r in bank:
        assert offered_codes(r) == journal_codes, r                          # no R04, no R05
    for r in book:
        assert offered_codes(r) == all_codes, r                              # the book's side keeps every code


def test_the_server_still_refuses_an_adjusting_item_on_a_bank_side_row(env):
    w = month_end_world(env)
    interest = w.conn.execute("SELECT id FROM transactions WHERE source = '銀行' AND amount_minor = 37").fetchone()["id"]
    r = post(w.maker, "/reasons/apply", {"match_set": MS, "reason_code": "R04", "txn_id": str(interest)},
             path_for_token="/reasons")
    assert r.status_code == 400 and "銀行の側の行に調整項目は作れない" in r.text
    assert w.conn.execute("SELECT COUNT(*) FROM adjusting_items").fetchone()[0] == 0


# ---- 2. amounts with thousands separators ------------------------------------------------------------------------

def test_the_totals_and_the_difference_of_a_group_have_thousands_separators(env):
    w = month_end_world(env)
    page = w.maker.get("/match/groups?ms=" + MS).text
    cells = num_cells(page)
    assert {"330,000", "150,000", "149,950", "50", "-5,000"} <= set(cells)
    assert not [c for c in cells if re.fullmatch(r"-?\d{4,}", c)], cells        # no bare number of 4 digits or more


def test_the_totals_of_a_group_waiting_for_a_hand_match_decision_have_thousands_separators(env):
    w = month_end_world(env)
    maker = roles.User(id=w.conn.execute("SELECT id FROM users WHERE name = 'maker'").fetchone()["id"],
                       name="maker", roles=frozenset(["作成者"]))
    left = txn(w.conn, "INV-2610-009", "帳簿")                                   # 70,000 in the book
    right = w.conn.execute("SELECT id FROM transactions WHERE source = '銀行' AND amount_minor = 37").fetchone()["id"]
    manual.create_manual(w.conn, maker, MS, [left], [right], "確かめ")
    cells = num_cells(w.maker.get("/manual?ms=" + MS).text)
    assert {"70,000", "37", "69,963"} <= set(cells)
    assert not [c for c in cells if re.fullmatch(r"-?\d{4,}", c)], cells


def test_the_approval_range_of_a_journal_has_thousands_separators(env):
    w = month_end_world(env)
    gid = w.conn.execute("SELECT id FROM match_groups WHERE state = '差額の処理待ち'").fetchone()["id"]
    assert post(w.maker, "/reasons/apply", {"match_set": MS, "reason_code": "R01", "group_id": str(gid)},
                path_for_token="/reasons").status_code == 200
    page = w.maker.get("/journals/1").text
    assert "0 以上 100,000 未満" in page and "100000" not in page


def test_the_completion_condition_sentence_has_thousands_separators(env):
    w = month_end_world(env)
    assert post(w.admin, "/tasks/generate", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    t = {x["task_id"]: x["id"] for x in w.conn.execute("SELECT id, task_id FROM task_instances")}
    assert post(w.maker, f"/tasks/{t['T01']}/prepare", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    assert post(w.approver, f"/tasks/{t['T01']}/complete", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    sentence = "残高一致の差が -29,013（調整後の銀行 1,474,987、調整後の帳簿 1,504,000）"      # before any journal
    r = post(w.maker, f"/tasks/{t['T02']}/prepare", {"period": "2026-10"}, path_for_token="/tasks")      # the refusal
    assert r.status_code == 400 and sentence in r.text
    assert sentence in w.maker.get("/tasks?period=2026-10").text                                         # the list
    assert not re.search(r"\d{5,}", re.search(r"<p class=\"notice\">.*?</p>", r.text).group(0))      # no bare number


def test_the_sentence_uses_the_multiplier_of_the_match_set_like_the_other_screens(monkeypatch):
    cfg = {"periods": [{"name": "2026-10", "end": "2026-10-31"}],
           "match_sets": [{"name": "預金照合", "left": "帳簿", "right": "銀行", "reconcile": True}],
           "sources": [{"name": "帳簿", "multiplier": 100}, {"name": "銀行", "multiplier": 100}]}
    st = SimpleNamespace(known=True, difference=4100000, adjusted_bank=154498700, adjusted_book=150398700)
    monkeypatch.setattr(tasks.balance, "statement", lambda *a, **k: st)
    d = {"condition": "残高一致の差 0", "condition_match_set": "預金照合"}
    assert tasks.check_condition(None, "sandbox", cfg, d, "2026-10") == [
        "残高一致の差が 41,000.00（調整後の銀行 1,544,987.00、調整後の帳簿 1,503,987.00）"]
    from close_kit.web import fmt_amount                                         # the filter the other screens use
    assert fmt_amount(4100000, 100) == "41,000.00"


# ---- 3. the names of the accounts ----------------------------------------------------------------------------------

def fee_journal(w) -> int:
    gid = w.conn.execute("SELECT id FROM match_groups WHERE state = '差額の処理待ち'").fetchone()["id"]
    assert post(w.maker, "/reasons/apply", {"match_set": MS, "reason_code": "R01", "group_id": str(gid)},
                path_for_token="/reasons").status_code == 200
    return w.conn.execute("SELECT id FROM journals").fetchone()["id"]


def test_a_journal_shows_the_name_of_each_account_beside_its_code(env):
    w = month_end_world(env)
    page = w.maker.get(f"/journals/{fee_journal(w)}").text
    assert "<td>6100 支払手数料</td>" in page and "<td>1100 普通預金</td>" in page


def test_a_code_that_is_not_in_the_chart_of_accounts_is_shown_as_the_code_only(env):
    w = month_end_world(env)
    jid = fee_journal(w)
    w.conn.execute("UPDATE journal_lines SET account_code = '9999' WHERE journal_id = ? AND line_no = 1", (jid,))
    w.conn.commit()
    page = w.maker.get(f"/journals/{jid}").text
    assert "<td>9999</td>" in page and "<td>1100 普通預金</td>" in page


def test_the_journal_csv_keeps_the_code_column_and_adds_the_name_in_a_last_column(env):
    w = month_end_world(env)
    fee_journal(w)
    rows = [l.split('","') for l in w.maker.get("/journals.csv").text.lstrip("﻿").strip().splitlines()]
    header, first = rows[0], rows[1]
    assert header[13] == "勘定コード" and header[-1].rstrip('"') == "勘定科目名"
    assert first[13] in ("6100", "1100") and first[-1].rstrip('"') in ("支払手数料", "普通預金")
    assert dict(zip(["6100", "1100"], ["支払手数料", "普通預金"]))[first[13]] == first[-1].rstrip('"')


# ---- the reasons that a balance is unknown or a file is refused write amounts the same way --------------------------

def test_the_reason_that_a_balance_is_unknown_writes_the_amounts_with_separators(conn, users):
    from close_kit import balance, ingest
    from conftest import load_workbook_into
    from test_close_foundation import csv_bundle
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    data = csv_bundle([("T1", "2026-10-31", 100000, None), ("T2", "2026-11-02", 200000, None),
                       ("T3", "2026-10-30", 50000, None)])
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", data).ok
    amount, why = balance.period_end_balance(conn, "sandbox", "帳簿", "2026-10-01", "2026-10-31")
    assert amount is None
    assert "CSV の行の残高 351,000 が、取引前残高 1,000 + 日付が 2026-10-31 までの行の合計 150,000 = 151,000 と合わない" in why
    assert balance.period_end_balance(conn, "sandbox", "帳簿", "2026-10-01", "2026-10-31", 100)[1].count("1,510.00") == 1


def test_the_reason_a_bundle_does_not_continue_the_last_one_writes_the_amounts_with_separators(conn, users):
    from close_kit import ingest
    from conftest import load_workbook_into
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct()).ok
    from tools import sample_builders as sb
    from datetime import date as D
    nov = sb.build_zengin([(D(2026, 11, 5), "in", 100, "ｱ", "", "S1")], d_from=D(2026, 11, 1), d_to=D(2026, 11, 30), opening=2000)
    r = ingest.ingest(conn, users.admin, "銀行", "nov.txt", nov)
    assert not r.ok and any(f"取引前残高 2,000 が" in x and f"{make_samples.CLOSE_BANK_OCT_CLOSING:,}" in x for x in r.reasons), r.reasons


# ---- 4. a duplicate takes part in matching and is shown with a mark -----------------------------------------------------

def test_the_screens_show_the_duplicate_mark_and_the_run_message_names_the_drafts(env):
    app, conn, _users = env
    admin = login(app, "admin")
    sheets = sb.default_sheets()                                       # the first sample: 15 ledger rows, twin entries
    sheets["パスルール"][0] = sheets["パスルール"][0][:13] + ["6100"]       # rule 10 names the account of the difference
    assert post(admin, "/workbook", files={"file": ("w.xlsx", sb.build_workbook(sheets))}).status_code == 200
    for src, name, data in (("帳簿", "l.csv", make_samples.ledger_csv()), ("銀行", "b.txt", make_samples.bank_zengin())):
        assert post(admin, "/ingest", {"source": src}, {"file": (name, data)}, "/ingest").status_code == 200
    r = post(admin, "/match/run", {"match_set": "預金照合"}, path_for_token="/match")
    assert r.status_code == 200 and "仕訳を 1 件、下書きに作った" in r.text and "照合の道具" in r.text
    rows = admin.get("/match/rows?ms=" + MS + "&klass=候補").text
    marked = [t for t in table_rows(rows) if "INV-2610-009" in t]
    assert len(marked) == 3 and sum("<td>重複</td>" in t for t in marked) == 2        # the two ledger rows, not the bank row
    groups = admin.get("/match/groups?ms=" + MS).text
    assert groups.count("［重複］") == 3 + 2                    # the instalments of INV-2610-005 and the twins of -009
    left_over = admin.get("/match/rows?ms=" + MS + "&klass=重複").text
    assert left_over.count("<td>重複</td>") == 2
    journals_page = admin.get("/journals").text
    assert "照合の道具" in journals_page and "照合の差額" in journals_page
    detail = login(app, "maker").get("/journals/1").text                       # the maker of the tool's draft is the tool
    assert "照合の道具（照合の差額）" in detail and "申請する" in detail and "取り下げる" in detail
    assert "申請する" not in login(app, "approver").get("/journals/1").text
