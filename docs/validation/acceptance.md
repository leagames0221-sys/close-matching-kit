# Acceptance run of the first release (done by the builder against the running server)

Run on 2026-10-24: `python -m close_kit seed-demo`, `python -m close_kit serve` (real server on 127.0.0.1:8765), then an
HTTP client logged in as the demo users, using the made-up files in `samples/`. No browser was used; the person
who owns the machine checks the screens in a browser afterwards. Passwords are in `var/demo_users.txt` (not in git).

| # | Step | Result |
|---|---|---|
| 1 | Start with the one-line command; load `bad_workbook.xlsx` as `demo-admin` | HTTP 400, the page lists the errors with sheet / row / column (for example "パスルール 行3 列「順番」"); nothing stored |
| 1 | Load `sample_workbook.xlsx` | HTTP 200; 「設定」 shows the match set 預金照合, the data sources 帳簿 / 銀行 and the pass rules (including 請求書ごとの合算) |
| 2 | Import a Zengin file whose balance does not add up | HTTP 400, reason "残高が合わない: ..."; nothing stored |
| 2 | Import the real sample bank file, then the same file again | first 200; second 400 "勘定日の範囲 ... が取り込み済みの束 ... と重なる" (and the same sha256) |
| 3 | Splink second half | see `docs/validation/results.md` (precision 0.9959 against the target 0.99) |
| 4 | Reconify evals 001-006, 010 (008 not expressible) | see `docs/validation/reconify_evals.md`: 001, 004, 005, 006, 010 as expected; 003 same pairs with the state 差額の処理待ち; 002 differs on purpose |
| 5 | Match run on the samples | 自動 6 left / 4 right rows, 候補 6 / 6, 未照合 1 / 1, 重複 2 / 0 |
| 5 | `demo-maker` makes a manual group of the two 33,000 candidate rows | state 承認待ち (not 完了) |
| 5 | `demo-maker` tries to approve it | HTTP 403 (no role) |
| 5 | `demo-approver` approves | HTTP 200, state 完了 |
| 5 | A user with both roles approves a group made by themselves | refused (test `test_self_approval_with_both_roles_over_http`) |
| 5 | A group whose two sides differ (7,777 vs -1,100) is approved | state 差額の処理待ち, not 完了 |

The test suite repeats every one of these through the in-process client, and the mutation tests (`mutations.md`) show
that the tests notice when the relevant code is broken.

*Note of 2026-10-26.* The table above is the record of the run against the server on 2026-10-24 and is left as it was. The
rules of the tool were changed on 2026-10-26 (ADR 0003, "Revision of 2026-10-26"); the same steps give, through the tests
(not against the server again): step 4 - 001, 004, 010 as expected, 005 and 006 as expected with the duplicate mark on the
rows of the bundle, 003 the same pairs, now 完了 with the journal of the difference drafted by the tool (the rule names the
account 6100), 002 an ambiguous candidate of three rows (`reconify_evals.md`); step 5 - 自動 3 left / 3 right rows, 候補
9 / 7, 未照合 1 / 1, 重複 2 / 0 (the three instalments of INV-2610-005 are a candidate of the reference rule, which keeps
its rows against the bundle rule behind it).

(The files in `samples/` were replaced by the month-end sample in the second release; the first release's steps above
used the earlier sample files. The tests of the first release still build those earlier files with
`tools/make_samples.py` functions `workbook()`, `ledger_csv()` and `bank_zengin()`.)

---

# Acceptance run of the second release (journals, adjusting items, period-end statement, posting file, task list)

Run on 2026-10-24 against a **real server** (`uvicorn` on 127.0.0.1:8765, started with a throw-away database; the
database of the person who owns the machine was not touched) with an HTTP client that logged in as the demo users
(`demo-admin`, `demo-maker`, `demo-approver`, `demo-approver2`, `demo-auditor`) and used the files in `samples/`.
48 checks, all passed (the client script and its output were not kept in the repository; the tables below are the
record). No browser was used; the screens are looked at in a browser afterwards by the person who owns the
machine. The same story is repeated without a server by
`tests/test_close_roles_web.py::test_the_month_end_story_over_http`, and the figures by
`.venv\Scripts\python tools\make_samples.py walk`.

## The path to a difference of 0 (the figures were fixed first; the sample was built to reach them)

The bank (全銀, account 1234567) opens at 1,000,000 on 2026-10-01 and closes at 1,474,987; the ledger CSV opens at
1,000,000 and closes at 1,504,000. Every row below is in the sample files.

| Sample row | What it is | How it is settled | Where it stands in the statement of 2026-10 |
|---|---|---|---|
| ledger 150,000 (INV-2610-003, 10-04) and bank 149,950 (10-05) | a fee of 50 | rule 10 names the account 6100 for the difference (since 2026-10-26; before, the reason code 振込手数料 made the same journal): the pair is 完了 and the tool drafts the journal (支払手数料 50 / 普通預金 50, maker 照合の道具) -> submitted by a maker -> approval | book side: movement of the bank the books lack, -50 |
| bank only, interest 37 on 10-25 | not in the books | reason code 受取利息 -> journal (普通預金 37 / 雑収入 37) -> approval | book side: +37 |
| ledger only, +70,000 on 10-31 (INV-2610-009) | the bank shows it on 11-02 | reason code 未達の入金 -> adjusting item -> approval by another person | bank side: in the books, not yet at the bank, +70,000 |
| ledger only, -41,000 on 10-29 (PAY-2610-02) | the bank debits it on 11-02 | the November statement is imported and matched: it pairs automatically as a timing item | bank side: in the books, not yet at the bank, -41,000 |

| 2026-10 | Amount |
|---|---|
| Bank balance at the period end | 1,474,987 |
| + deposits in the books, not yet at the bank | 70,000 |
| - payments in the books, not yet at the bank | 41,000 |
| **Adjusted bank balance** | **1,503,987** |
| Book balance at the period end | 1,504,000 |
| +/- movements of the bank the books do not have yet (-50 + 37) | -13 |
| **Adjusted book balance** | **1,503,987** |
| **Unidentified difference** | **0** |

Before the November statement is imported, the same statement shows 41,000 as an unidentified difference in red. For
2026-11 the figures are bank 1,523,987 / books 1,524,000 / movements -13 / difference 0.

## Items 6 to 10 (what was run, what happened)

| # | Step | Result |
|---|---|---|
| 6 | Reason code 振込手数料 on the group with a difference of 50 | journal draft JE-000001, Dr 6100 50 / Cr 1100 50; the group waits in 仕訳の承認待ち |
| 6 | Reason code 未達の入金 on the unmatched ledger row of 70,000 | adjusting item (承認待ち); the maker cannot approve it (403); `demo-approver` approves it; it is 未解消 |
| 6 | Statement for the next period (2026-11) before November's bank file exists | lists the item (carried over) |
| 7 | Typed journal Dr 10 / Cr 9; unknown account 9999; a date in the closed period 2026-09 | each refused with the rule number, line and value; nothing stored |
| 7 | CSV `journals_import_bad.csv` (the same three faults) | whole file refused, reasons with row numbers (2, 3, 4); journal count unchanged |
| 7 | CSV `journals_import_sample.csv` | two drafts stored |
| 8 | Journals of 99,999 and 100,000 | `demo-approver` approves 99,999 and is refused for 100,000 (not named for that range); `demo-approver2` approves 100,000 |
| 8 | The maker POSTs the approval directly | 403 |
| 8 | A person with both roles, named in the route, POSTs the approval of their own journal | 403 "自分が作った...", the journal stays 承認待ち |
| 9 | Statement 2026-10 before November: difference shown in red | yes (41,000) |
| 9 | Task 預金の残高一致 (condition: difference 0) tried while the difference exists | refused, with the difference |
| 9 | After the November files: statement 2026-10 | adjusted bank 1,503,987 = adjusted books 1,503,987, difference 0; the task can now be prepared and completed |
| 10 | Task T02 tried before T01 is 完了 | refused, naming T01 |
| 10 | Due dates | T01 (business day 5 after 10-31, national holiday 11-03 and company holiday 11-06 skipped) = 2026-11-10; T07 (calendar days, +30) = 2026-11-30 |
| 10 | Auditor | reads every screen and the CSVs; every changing operation is 403 |
| 10 | Operation log CSV | has who, when, what, the settings version, and the refusals with their reasons |
| 10 | Posting file | written by the administrator; first line `# 環境: sandbox ...`; then `BUKRS,BLART,BLDAT,BUDAT,WAERS,XBLNR,BKTXT,HKONT,WRSOL,WRHAB`<br>**Note (third release):** this row is the record of that day. In the later version the posting file comes from production only; the sandbox screen says it is for trying things out and writes nothing (see the third-release record below). |

## Other checks run by the builder
- A copy of a real database of the first release (26 transactions, 10 groups) was migrated: every row kept,
  `PRAGMA integrity_check` ok, foreign key check empty, versions 1 and 2 recorded. (Run again on 2026-10-26 on a new
  copy of the same file (kept under `var/`), with the code of that day: 26 and 10 rows kept, migrations 1, 2
  and 3 recorded, integrity ok, foreign key check empty. The file is not in git.)
- The real server was stopped after the run (port 8765 is free).

## A figure that disagrees with its source
- The Office of Justice Programs sheet prints an adjusted book balance of 99,000, but its own items give
  97,600 + 2,050 - 790 = 98,860 (a difference of 140). The tests use the calculated figures (ADR 0006).


# Acceptance run of the third release (change requests, two environments, the documents)

Run on 2026-10-25 with an **in-process HTTP client** (`fastapi.testclient`) against the application on a throw-away
database. The server on 127.0.0.1:8765 and the database in `var/` were not touched. The users were made the way `seed-demo`
makes them (`auth.create_user(..., by_os_user=...)`, so the operation log has the rows with the action `user_create`): `demo-admin`,
`demo-admin2` (administrators), `demo-maker`, `demo-approver`, `demo-approver2`, `demo-auditor`. The files are the ones in
`samples/` that git holds. The same flows are repeated by pytest (`tests/test_changes_web.py`, `tests/test_changes.py`,
`tests/test_changes_check.py`). No browser was used: the three cases a person walks in a browser are TC-18 to TC-20 of
`docs/deliverables/test-cases.md` (held in the summary report).

## Item 11 (the flow of a change)

| # | Step | Result |
|---|---|---|
| 11 (1) | `demo-admin` loads `samples/sample_workbook.xlsx` into the sandbox (version 1) and makes the request; puts it forward | HTTP 200; state 事前承認待ち; production has no version yet |
| 11 (1) | `demo-admin` (the requester) POSTs the pre-approval | **403** |
| 11 (1) | `demo-admin2` pre-approves | HTTP 200; the test ran by itself: state 試験済み; result 成功; step 1 対象外 (nothing in production), step 2 実施: balance statement difference 0 for 2026-10 and 2026-11, 2 journals approved, 1 adjusting item, step 3 対象外 |
| 11 (1) | `demo-admin` POSTs approve and reject of the result | **403** and **403** |
| 11 (1) | `demo-approver` approves the result; `demo-admin2` then tries to apply | state stays 試験済み (one of the two people); apply -> **400** |
| 11 (1) | `demo-admin2` approves the result | state 結果の承認済み |
| 11 (1) | `demo-admin` POSTs apply | **403**; production still has no version |
| 11 (1) | `demo-admin2` applies | HTTP 200; production = version 1; `environment_log` row (production, version 1, request 1) |
| 11 (1) | `demo-admin2` POSTs the confirmation | **403** (only the requester confirms) |
| 11 (1) | `demo-admin` confirms | state 確認済み. Eight rows of history: 作成, 申請, 事前承認, 試験 (environment 使い捨ての DB), 結果の承認 (承認者), 結果の承認 (管理者), 適用, 確認, each with who, when and the roles at that time |
| 11 (2) | The October ledger and bank files of `samples/` imported through `/production/ingest` | HTTP 200 and 200; production holds 9 rows, the sandbox 0 |
| 11 (2) | The same upload to the production path with the hidden field `env=sandbox` | **403**; nothing stored |
| 11 (3) | A workbook with the tolerance of the first pass rule 100 -> 0 and the approval threshold 100,000 -> 200,000 is loaded and requested; pre-approved | the test result (below) |
| 11 (3) | `demo-admin` POSTs the approval of the result of his own request, then the application | **403** and **403** |
| 11 (3) | `demo-approver`, `demo-admin2` approve; `demo-admin2` applies; `demo-admin` confirms | production = version 2; 確認済み |
| 11 (4) | A request that closes the last period; applied and confirmed | production = version 3 |
| 11 (4) | A rollback request to version 1 | a **new version 4** was made: periods 2026-09 closed, 2026-10 open, **2026-11 closed** (version 1 had 2026-11 open); the tolerance is 100 again. Applied and confirmed; production = version 4 |
| 11 (4) | A request for version 1 as it is (it would open 2026-11) | **400**, "締めた期間" in the message |

The result of the request of (3) (production held the October rows of the sample, not matched yet):

| Class | before (production's version) | after (the requested version) |
|---|---|---|
| 自動 | 6 | 4 |
| 候補 | 0 | 2 |
| 未照合 | 3 | 3 |
| 重複・手・処理済み・転記済み | 0 | 0 |

Rows whose class changed: 2 of 9. The ledger row `INV-2610-003` (150,000 on 2026-10-04) and the bank row `INV-2610-003`
(149,950 on 2026-10-05): 自動 -> 候補 (the fee of 50 is outside a tolerance of 0).

Who may approve at the edges of the ranges (amount: before -> after): 0 and 99,999: `demo-approver`, `demo-approver2`
-> the same; 100,000 and 199,999: `demo-approver2` only -> `demo-approver`, `demo-approver2`; 200,000: `demo-approver2` -> `demo-approver2`.

Step 2 of the same result (the sample path under the requested version): **失敗（変更の影響）**, stopped at 「理由コード R01
（振込手数料）を手数料の差の組に付ける」: the group is 候補, and a reason code can only be attached to an automatic or manual group that waits
for its difference. The result is stored with that status and went on to the approval of the result (the people who decide see it).

## Items 12 and 13

| # | Step | Result |
|---|---|---|
| 12 | The cases in `docs/deliverables/test-cases.md` and the rows of `docs/deliverables/test-summary-report.md` | 20 and 20, the same numbers in the same order |
| 12 | TC-12 (15 pytest names) run again by name | 15 passed; the report says 合格 |
| 12 | `tools/test_report.py check` | OK: the commit in the report is HEAD at the time the report was made. The report is committed after it is made, so HEAD moves by that commit; the check also accepts later commits that touch only documents (`docs/` and the top-level `.md` files) |
| 13 | The six questions of `docs/deliverables/qa-log.md` | each has what was found, whether it can be done, the alternative and the source with a URL (`test_the_question_log_has_six_questions_with_every_column_filled` passes) |

## Changed after the run above (review of the change requests)

The record above stays as it was run. Later the same day, a review changed four things that the steps above touch:
- the approval of a result is sent with the number of the test result the page shows; a page that was opened before the
  test ran again cannot approve the newer result (409, "最新ではない"). On the screens nothing else changes: the form of
  the page carries the number;
- every refused step of a request (the person, the state, an empty field, a second approval) is a row of the operation log;
- the draft of a rollback made from a request that cannot be confirmed goes to that request's back-out plan (item 15);
- `tools/test_report.py check` also refuses a report whose case rows no longer match `test-cases.md` one to one.
The pytest flows named in the cases (TC-11, TC-13, TC-14, TC-15) were run again with these changes; the summary report was
made again from that run.

## The three cases walked on the screens (TC-18, TC-19, TC-20)

2026-10-25. Done by a script driving a headless Microsoft Edge and HTTP, on the demo database (`var/`), against the server at
127.0.0.1:8765. A person's look at the screens is a separate check and is not recorded here. The record of each case is in
the section of the case in `docs/deliverables/test-cases.md`.

| Case | What was done | Result |
|---|---|---|
| TC-18 (3.2.1 of the user manual) | demo-approver: the band says 「環境: production（本番）」, 「仕訳」, state 「承認待ち」 and 「絞り込む」, JE-000004, the detail shows the date and period, the debit total 50, the account names (6100 支払手数料, 1100 普通預金) and the approval route 「0 以上 100,000 未満：demo-approver、demo-approver2」, 「承認」 gives 「承認した（状態: 承認済み）。」, state 承認済み, decided by demo-approver. A journal with a debit total of 120,000 (JE-000007; only demo-approver2 may approve 100,000 and over) shows 「この仕訳はあなたは決められない: …」; pressing 「承認」 shows the same sentence and the state stays 承認待ち | the names, buttons and sentences of the manual are those of the screens |
| TC-19 | production = version 1 through request 1, then the October ledger and bank file, matching (automatic 3 of 3, one group waiting for its difference), R01, R04, R03, the journals JE-000004 (50) and JE-000005 (37) approved by demo-approver, the November files, matching again; the balance table says 「原因の分からない差は無い」 for 2026-10 (1,503,987 on both sides after the adjustment) and 2026-11; the production posting file has 2 journals and 4 lines and starts with 「# 環境: production（本番）」; the sandbox posting screen is a 409 with 「試験用。転記ファイルは production から出す（sandbox では出さない）」 | passed |
| TC-20 | request 1 (the sample workbook into production) through every step, production = version 1, the requester's screen has no pre-approval button; request 2 (tolerance 100 to 0 and the approval-route edge 100,000 to 200,000): the test result is 失敗（変更の影響）, automatic 6 to 4, candidate 0 to 2, unmatched 3 to 3, two rows changed class (INV-2610-003, 自動 to 候補), the approvers at 100,000 and 199,999 go from demo-approver2 to demo-approver and demo-approver2; the requester's direct POST of a result approval is a 403 | passed |

In the test summary report the three cases stay 保留: the rule of `tools/test_report.py` is that a case a person walks in a
browser is held, whatever the record says; the numbers are 20 cases = 17 passed + 0 failed + 3 held.

Three things found on these screens were fixed and each has a test that is red on the code before the fix:
- the history of a request said 「本番の版 None -> 1」 when the first request was applied (no earlier version). It now says
  「本番の版: 版なし -> 版 1」, as the other screens say 版なし; the operation log has the same form;
- the rows that change (item 7 of the request) named some columns by the internal name of the field. They are now named
  by the header of that column in the sheet of the workbook (期間の名前, 勘定コード, 種類 of a task, the CSV columns one by
  one). The two columns of the pass rules, `date_window` and `amount_tolerance_minor`, are the English words in the
  workbook itself, so they read the same as before. The diff is computed as before and the sha256 of a version is unchanged;
  a request put forward earlier keeps the names it was stored with;
- the stop screen of the sandbox posting file said to load a workbook of version 2 even when the sandbox had such a
  version. It now shows the way to production; the advice for a sandbox that really has no version is as before.
