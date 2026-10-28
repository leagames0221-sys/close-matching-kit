# 0002. Data shape: environments, versions, runs

Status: accepted (2026-10-24)

## Context

Settings (data sources, match sets, pass rules, reason codes) are edited by people in a workbook, and a match result
must always be explainable by the exact settings that produced it. A change to the settings must never rewrite
history. Later features (journal entries, change approval, promotion to production) will hang on the same shape.

## Decision

- **Workbook in, versions out.** An xlsx workbook (nine sheets, columns in Japanese; the 説明 sheet holds the schema
  version) is read with openpyxl `read_only=True`. Formula cells are refused. Every error is returned with sheet,
  row, column and reason, and one error refuses the whole workbook (nothing is stored partially).
- **A version** is a row in `config_versions`: normalised JSON (keys sorted), its sha256, the original bytes, schema
  version, who loaded it and when. SQLite triggers refuse UPDATE and DELETE on that table. Loading the same workbook
  twice makes two versions.
- **Environments** `sandbox` and `production` are rows that point at a version id. Changing the pointer is the only
  mutable thing, and each change is appended to `environment_log` (also append-only). In this release the workbook can
  only be loaded into `sandbox`; `production` stays empty, because the promotion flow is a later feature (it came with the
  change requests of the third release: a version reaches `production` only through an applied request, ADR 0008).
- **Runs.** Every import and every matching run is a row in `runs` (kind, environment, version id, accounting period,
  who, when). Transactions and groups carry environment, run id, version id and period. Batches carry environment,
  run id and version id (a file may span periods; its rows have theirs). The per-row result table (`match_rows`)
  carries environment and run id; its version comes from the run and its period from the transaction.
- **Operation log** (`audit_log`): who, when, what, environment, version, outcome (成功 / 拒否) and the reason for a
  refusal, including failed logins. UPDATE and DELETE are refused by triggers. The CSV export takes the list of
  characters from OWASP's "CSV Injection" page (https://owasp.org/www-community/attacks/CSV_Injection): a cell that starts
  with `=`, `+`, `-`, `@`, tab, CR, LF or a full-width `＝ ＋ － ＠` gets a leading single quote, and every field is quoted.
  The page lists these two as sanitization ("Prepend each cell field with a single quote", "Wrap each cell field in double
  quotes") and says: *"The above techniques are not reliable in Microsoft Excel after saving and re-opening the CSV file."*
  For Excel it offers a tab character inside the quoted field. The single quote is the author's choice; the tab prefix is
  not built.
- **Amounts** are integers in the smallest unit; the data source sheet has the multiplier (yen = 1, dollars = 100,
  the idea of Reconify's `multiplier`). No floating point anywhere.
- **SQLite**, parameters only (no string-built SQL), one connection per request.

### Numbered migrations (second release)
- `schema_migrations` holds the numbers that have run. Migration 1 is the first release's schema, frozen as `db.SCHEMA`;
  migration 2 is the second release's. SQLite cannot change a CHECK list, and the second release needs wider ones
  (group state 仕訳の承認待ち; row class 処理済み / 転記済み), so migration 2 builds `match_groups` and `match_rows` anew,
  copies every row with its id, removes the old table and renames the new one (the procedure of the SQLite documentation,
  https://www.sqlite.org/lang_altertable.html, with foreign keys off and a foreign key check before the commit). It also adds balances to `batches` and
  `transactions` and creates the tables of journals, adjusting items, the posting records and the task list.
  Running it again changes nothing; a test builds a database of the first release, migrates it twice, and compares rows,
  ids and the schema text. The same code was run on a copy of a database made by the first release on the builder's PC
  (26 transactions, 10 groups; the record is in `docs/validation/acceptance.md`, "Other checks run by the builder"). That
  database is under `var/` and is not in git.
- Migration 4 (2026-10-26) widens the CHECK list of `journals.method` by the value 照合の差額 (a journal the tool drafted for
  the difference of a pair inside the tolerance, ADR 0003 D6). The table is rebuilt the same way; its two triggers and three
  indexes are made again, and `PRAGMA legacy_alter_table` is on for the rename because the triggers of `journal_lines` name
  `journals`. A test builds a database of migration 3 with an approved journal, its lines and a posting record, migrates it,
  and checks the rows, the links, the foreign key check, that the triggers still refuse a change, and that a second run changes nothing.
- Every new table carries the environment name. Append-only by trigger: `posting_files`, `posting_log`, `posted_links`,
  `task_events`. Immutable once approved: journals and their lines (a trigger with `WHEN OLD.status = '承認済み'`).
- A workbook has a schema version in its 説明 sheet; version 1 workbooks are refused as too old, and a stored
  configuration of version 1 makes the new screens stop with a message to read the workbook again in version 2. A new
  version that turns a closed period back into an open one is refused, so one administrator cannot reopen a period with
  a new workbook.

## Alternatives considered

- **A tab character in front of risky cells in the CSV export.** OWASP's "CSV Injection" page gives it as the mitigation
  for Excel (the tab goes inside the quoted field). It is not built (see "Operation log" above). No reason beyond the
  author's choice of the single quote is recorded, and the same page says that the single quote and the double quotes
  are not reliable in Excel after the file is saved and opened again.
- **`ALTER TABLE` for the wider CHECK lists of migrations 2 and 4.** The SQLite documentation says that the only schema
  changes it supports directly are rename table, rename column, add column and drop column, and describes building a new
  table, copying the rows, dropping the old table and renaming the new one for any other change. The migrations do that.
- **Loading a workbook straight into production.** Not allowed in this release: the workbook can only be loaded into
  `sandbox`, and `production` stays empty until a change request puts a version there (ADR 0008).
- **No other option was compared** for holding the settings (a workbook), for the database (SQLite) or for amounts
  (integers in the smallest unit; Reconify's `multiplier` is the only model the records name).

## Consequences

- Any result can be traced to the sha256 of the settings it used, and the original workbook can be re-opened.
- Append-only is enforced in the database, not only in code, but the file itself can still be replaced by someone with
  file access; this is not a tamper-proof ledger.
- History grows; there is no purge (by design).
