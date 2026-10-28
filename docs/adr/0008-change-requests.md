# 0008. Change requests: moving a version of the settings into production

Status: accepted (2026-10-25)

## Context

The settings are an Excel workbook. A workbook that is read without a fault becomes a **version** (append-only); an
**environment** is a pointer to one version. Until now only the sandbox had a version, and an administrator moved its
pointer by loading a workbook. A tool that closes a month needs the same settings in production, and a change of the
approval route, of a tolerance or of who checks a task is a change of a control. The question is how a version gets
from the sandbox to production, who decides, what is tried first and what is left behind as evidence.

Material read (document names and URLs; the dates of retrieval are in `docs/sources.md`):
- NIST SP 800-128, *Guide for Security-Focused Configuration Management of Information Systems*, section 3.3 (the steps of
  configuration change control: record, decide whether it needs control, analyse the impact, test, approve, implement,
  verify, close out), appendix E (a sample change request), and the passage on independence ("vetted by at least one
  authorized individual who is independent of the requestor"). https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-128.pdf
- Financial Services Agency, internal control standard and implementation standard (令和 5 年): the general controls of
  information technology (a change must be consistent with the existing system and its process must be recorded; access
  must be managed) and the points an auditor looks at (prior approval, sufficient testing, the result approved by the
  department that uses the system and by the IT department, the process recorded and kept).
  https://www.fsa.go.jp/singi/singi_kigyou/kijun/20230407_naibutousei_kansa.pdf
- BlackLine's public material says that a Sandbox environment exists and that releases are deployed to it; it does not
  describe how a customer moves settings from a Sandbox to production (`docs/deliverables/qa-log.md`, question 2). The flow
  below is therefore built from the two standards above.

## Decision

**What is requested.** A version of the settings (schema 2) that an administrator loaded into the sandbox is named by
its number and requested to become production's version. `versions.save_version` only adds a version and moves the
pointer of the **sandbox**; a request for `production` is refused there. The pointer of production is moved by one
function, `changes.apply`, and by nothing else.

**Steps** (NIST steps ii to ix): 下書き (draft) -> 事前承認待ち (put forward, waits for the pre-approval) -> 試験待ち ->
試験済み (tested, waits for the approval of the result) -> 結果の承認済み -> 本番に適用済み -> 確認済み (closed).
A request is closed also by 却下 (rejected) and 取り下げ (withdrawn) until it is applied. If the person who asked cannot
confirm the result, the request goes to 確認できない and is **not closed** (NIST: not closed "until it has been confirmed
that the change was deployed without issues"); a draft of a rollback request can be made from it, to the version of its
back-out plan (item 15). Only the Scheduled
kind of urgency exists. The pre-approval before the test is the author's decision, taken from the Agency's points for the auditor. Those points
are what an auditor checks when a company develops, procures or changes a system for financial reporting (an approval
before, then sufficient testing, then the approval of its result): 「システム、ソフトウェアの開発、調達又は変更について、
事前に経営者又は適切な管理者に所定の承認を得ていること」. The sentence is not written for a change of settings; reading
it so is the author's.

**Form** (NIST appendix E has 16 items). Used: 1 date, 2 title, 3 requester, 4 description of the change (the version
number), 5 justification, 6 urgency (Scheduled only), 7 components to be changed (**computed**: the rows that differ,
sheet by sheet), 8 other components affected (**computed**: the journals waiting for approval whose list of approvers
changes, the unfinished tasks whose completion condition or due date changes, the match sets used), 10 security impact
(**computed**: the controls that change - the approval route, the preparer and reviewer of a task, closing of a period),
11 functional impact, 12 impact of not doing it, 15 back-out plan (the version to go back to; the default is the
version in production when the request is put forward). Not used: 9 personnel, 13 interface issues, 14 changes to other
applications, 16 funding - the tool has no value that would mean anything there. The fields that are computed are
filled when the request is put forward and are frozen with it.

**People** (all checked on the server, whatever the screen shows):
- The requester can never pre-approve, reject, approve a result or apply, whatever roles he holds (NIST says that
  "system administrators, developers, etc., are not given the authority to unilaterally propose and approve changes to the
  configuration of a system", excluding changes the procedures name as exempt; applying this to every requester, whatever
  the role, is the author's decision). Pre-approval, rejection and applying are for an
  administrator who is not the requester.
- The result needs **two people who are not the requester: one holding the role 承認者 and one holding the role 管理者**.
  One person holding both counts once. Either kind may reject the result. *Author's design decision, the reading* (the closest source is the Agency's
  sentence, which names departments this tool does not have):
  the Agency's standard names
  「当該システム、ソフトウェアを利用する部門の適切な管理者及びＩＴ部門の適切な管理者」 (the appropriate manager of the
  department that uses the system and the appropriate manager of the IT department); this tool has no departments, so
  the two are read as the two roles.
- An approval belongs to **the test result** it was given for. A new test voids the approvals of the earlier result.
  The screen sends the number of the result it shows with the approval; if the test ran again after the page was
  opened, the approval is refused, so it never counts for a result the person has not read.
- The requester confirms (*author's design decision;* NIST SP 800-128 step viii: *"Verify that the change was implemented
  correctly"* and *"Configuration change control is not complete and a change request not closed until it has been
  confirmed that the change was deployed without issues"* - it does not say who verifies; here the person who asked looks
  at whether it came out as asked).
- A person whose range of approvable amounts becomes wider through the change, and who approves the result, is marked on
  the screen (*author's design decision;* no source was found for such a mark; it does not stop him).

**Two requests at once.** The test records the version that production had when it ran. Applying is one transaction with
one `UPDATE environments SET version_id = ? WHERE name = 'production' AND version_id IS ?` (the version of the test); if it
changes no row, nothing moves and the request goes back to 試験待ち. The sha256 of the version is computed again from the
stored normalised text at the moment of applying and compared with the value in the test result; the version number
shown on the screen must also be the one the request names.

**Closing and rolling back.** Closing a period of production is done by a request like any other change (the "締め" column
of the workbook). A closed period stays closed, as with Odoo's "Irreversible Lock Date" module (`account_lock`, 17.0,
LGPL-3; idea only): *"Any new All Users Lock Date must be posterior (or equal) to the previous one."* In Odoo the module
is optional; here it is always so. A version that opens a period that production has closed is refused when it is requested and again
when it is applied; the base is **production's** version (the check at the loading of a workbook compares with the
sandbox's version). A rollback is a new version made of the target version's workbook with production's closed periods
added as closed, and a request for that version; putting the old version back as it was would open the periods. A
version of schema 1 cannot be a rollback target. Earlier versions stay in the append-only table (NIST: "retention of
previous versions allows for a rollback").

**The first request.** When production has no version, the "before" is "no version": the diff is the whole workbook and
the test is the sample path only.

**Record.** Every step is a row of `change_request_events` (who, when, in which roles, which environment, which version,
why). Every refused step is a row of the operation log with its reason - the person, the role, the state, an empty
field or a second approval alike. The request row is frozen by a trigger once it leaves 下書き; `change_request_events`, `change_tests` and
`change_approvals` refuse update and delete. `environment_log` learns which request moved a pointer. The list of
requests can be downloaded as CSV by the administrator and the auditor, the same people as the operation log.

**The test** (the sources give no model for what to try - NIST step v says *"Test the proposed change for security and
functional impacts"* - so the content is the author's design decision, taken from that sentence and the test environment of CM-4(1) below). It runs by itself after the pre-approval, on
**throw-away SQLite files**, a test environment apart from production (NIST SP 800-53 Rev. 5, CM-4(1) *"Analyze changes
to the system in a separate test environment before implementation in an operational environment"*; its discussion asks
that activities in the test environment do not impact the operational one and that operational information is not
*inadvertently* transmitted to it - here production's rows are copied on purpose, into a temporary directory that is
removed after the test): production's rows are copied with the standard `sqlite3.Connection.backup`; nothing is
written to the sandbox or to production, and no process-wide value (for example the cost of the password hash) is changed;
the people in the throw-away database have a hash that no password opens. Three parts:
1. production's data, matched again under the version in production and under the requested one: the number of rows
   per class before and after, and every row whose class changes. A group that is done, or waits for its difference, is
   kept by every match run, so only the rows that are not in such a group can change class; this is the same rule the next
   real match run follows.
2. the sample path of `samples/` (files that git holds) under the requested version: the journal of the fee pair (drafted
   by the tool when rule 10 names an account for the difference, else made with a reason code), reason codes, journals and
   their approval, an adjusting item, the next month's files, and the balance statement. A version whose data sources differ from the
   sample's is marked 対象外 (not applicable).
3. what the changed sheets do: for the approval route the people who may approve at the edge of every range (and for the
   journals now waiting in production); for the task list the due date and completion condition of the unfinished tasks;
   for the pass rules the count of part 1.
If the change itself makes a step fail (for example a route that no longer names the sample's approver), the result is
recorded as **失敗（変更の影響）** and goes on to the approval of the result: the people who decide need to see it. Only a
fault of the tool (an exception that is not a refusal of the domain) leaves the request at 試験待ち and records nothing
as a result. The result stores both version numbers and sha256 values, the time of the copy of production, and the
sha256 of every file in `samples/`. The Splink data and the Reconify evaluation are not part of this test: each builds its
own workbook and never uses the requested version; they remain checks of the code (pytest and `docs/validation/`).

**Environments.** The environment is part of the path (`/sandbox/...`, `/production/...`; the paths without a head are
the sandbox) and every POST carries it in a hidden field; the server checks on every POST that the two are the same. The
session does not hold it, so a tab opened earlier cannot write into the environment another tab switched to. One module
(`close_kit/envs.py`) decides the environment of a request. A production page that needs a version stops with
"本番の版が無い（変更の申請で上げる）" while production has none. The posting file is written from production only; the
sandbox screen says it is for trying things out.

**Making a user.** Users are made at the command line (`adduser`, `seed-demo`). The operation log gets a row with the action `user_create`
and the name of the person logged in to the PC (`getpass.getuser()`), and the screen of a request shows the date on which
each approver's user was made and marks one made after the request.

**Trust boundary.** Whoever can log in to this PC as its operating-system user can open the database file and change it,
including the triggers. The rules above bind the people who use the screens; they do not protect against the owner of the
file. This is the same reason single sign-on is not built (ADR 0005). The marks above make a user created after the
request visible; they do not prevent it.

**Migration 3** adds `change_requests`, `change_request_events`, `change_tests`, `change_approvals`, the column
`environment_log.change_request_id`, and the triggers; it only adds, and a second run changes nothing.

## Alternatives considered

- **Approval only after the test.** NIST SP 800-128 lists the steps record, decide whether control is needed, analyse
  the security impact, test, approve, implement, verify and close; the approval comes after the test. A pre-approval
  before the test is added from the Agency's points for the auditor (author's decision, above).
- **One person approving the result.** The Agency's standard names the manager of the department that uses the system
  and the manager of the IT department. This tool has no departments, so two roles are asked for: 承認者 and 管理者.
- **Putting the old version back unchanged as the rollback.** Not done: it would open the periods that production has
  closed. A rollback is a new version with those periods added as closed (Closing and rolling back).
- **An optional lock on closed periods.** Odoo's `account_lock` module (idea only) is optional in Odoo; here it always
  applies.
- **Running the Splink data in the sandbox and comparing the rates before and after.** The first design of the test. It was
  replaced because the Splink and Reconify runs build their own workbook and never use the requested version; the test now
  copies production into throw-away files and runs the sample path (The test, above).
- **A separate flow for urgent changes, and minutes of a change control board.** Not built (ADR 0005). NIST SP 800-128
  has a sample charter of a change control board in an appendix, and the Digital Agency's practice guidebook (DS-120)
  describes a meeting that decides on change requests.
- **All 16 items of NIST's sample change request.** Four are not used because the tool has no value that would mean
  anything there (Form, above).

## Consequences

- A change of a control (a tolerance, a threshold, who checks a task) has, in one place, who asked, what changes, what it
  touches, what was tried, who approved the result and who applied it.
- The test cannot say that a change is right, only what it does to the data of production and to the sample path. A
  change whose effect is on rows that production does not hold yet shows nothing in part 1.
- Two administrators and one approver are needed to put a version into production. A one-person installation cannot do
  it; that is the intended price of the separation.
- The tool does not copy data between environments; the sandbox and production keep their own rows.
- An emergency change (a different flow) is not built (ADR 0005).
