# 0005. What is not built

Status: accepted (2026-10-24)

## Context

The tool takes three ideas from a commercial close-management product (task list, transaction matching, journal
entries with approval) and builds all three on one foundation (workbook versions, environments, log, roles). BlackLine is a product name of BlackLine
Inc.; this repository has no relation to that company and does not claim to be the same product.

## Decision

Not built, and said openly:
- **A finished free product that has task list + matching + journal approval in one** is not known to the author
  (this is the author's assumption: there was no systematic search, and no list of search terms was kept). The free
  products the author looked at:
  - ERPNext (GPL-3.0): its source tree (https://github.com/frappe/erpnext, default branch `develop`) has the DocTypes
    `task` and `task_depends_on`, `accounting_period`, `bank_reconciliation_tool` and `bank_transaction_rule` (with
    `bank_transaction_rule_accounts`). The author did not find in that list: due dates counted from period end with
    maker / checker records, pass-rule order with automatic / suggested, reason codes leading to journal entries or
    adjusting items, a settings workbook with sandbox -> production change control. That is a reading of the names
    of the DocTypes, not a test of ERPNext's behaviour; `bank_transaction_rule` may overlap with rules that lead to
    journal entries. GPL: ideas only, nothing copied.
  - OCA account-reconcile (AGPL-3.0): matching only.
  - Reconify (MIT): matching only (it also does one-to-many and many-to-many).
- Direct connection to an ERP (import and export are files); an audit certification; multi-currency, company codes and
  row-level view restrictions; scoring-based matching (Fellegi-Sunter, the Splink model) and AI suggestions;
  reading PDF bank statements; single sign-on (the login is a local one: passwords hashed, sessions on the server);
  three-way matching; subset-sum search.
- Not claimed: "the same as", or "as good as", the commercial product. What is claimed: the matching rules, the
  classes and the controls described in these ADRs, with the public material they were taken from.

Also not built in the second release:
- Several approval levels (one level, by amount range). Foreign currency and several company codes. An automatic
  reversing journal (a correction is a new journal made by hand).
- Bank errors as adjusting items (something the bank has to correct).
- Uploading the posting file to SAP: there is no SAP here, so the file's acceptance there was not tested; the heading
  text of SAP's own template and its date format are not verified (ADR 0006).
- A rule that makes a task impossible to start before its predecessors are done (only 作成完了 is blocked); a free
  repeat frequency for recurring tasks (monthly only); time of day.
- (The posting file used to be written from either environment. It is written from production only since the change
  requests exist, ADR 0008.)

## Alternatives considered

- **Take a finished free product as the base.** ERPNext (GPL-3.0), OCA account-reconcile (AGPL-3.0) and Reconify (MIT)
  were looked at (above). None is used as a whole. The author's reading is that ERPNext has the parts separately but
  not the combination listed above, and that OCA account-reconcile and Reconify do matching only. For GPL and AGPL code
  only ideas are taken, nothing is copied. From Reconify (MIT) the names and meanings and eight evaluation fixtures are
  used (`docs/sources.md`).
- **Score-based matching (Fellegi-Sunter, Splink's model) and AI suggestions.** Not built: the tool runs with no
  statistical model and no LLM (ADR 0001, Context). Splink is used only for test data (ADR 0003, D8).
- **Reading PDF bank statements.** Not built. The one package looked at that reads PDFs sends them to an LLM (ADR 0001,
  item 5).
- **A connection to an ERP.** Not built; import and export are files. There is no SAP here, so even the posting file was
  not tested against it (ADR 0006).
- **The other items under "Not built".** The records show no comparison for them (single sign-on, three-way
  matching, foreign currency, and so on). Subset-sum search is in ADR 0003, approval levels in ADR 0006, and single
  sign-on is mentioned again in ADR 0008 (trust boundary).

## Consequences

- An interviewer can see what is deliberately absent and why.
- Journal entries and the period-end task list now attach to the same version / environment / run columns (ADR 0006,
  0007). Change approval from the sandbox to production is ADR 0008.

Also not built with the change requests (ADR 0008):
- A separate flow for urgent or unscheduled changes (only Scheduled exists), and the minutes of a change control board.
- Four items of the sample change request form (personnel, interface issues, changes to other applications, funding): the
  tool has no value that would mean anything there.
- Copying data (rows, matches, journals) between the sandbox and production. The test of a change copies production into a
  throw-away file; the two environments keep their own rows.
- Single sign-on, and a backup tool for the database (the administrator's manual says how to copy the SQLite file).
- Protection against someone who is logged in to the PC as its operating-system user and edits the database file itself.
  The rules bind the people who use the screens (ADR 0008, trust boundary).
