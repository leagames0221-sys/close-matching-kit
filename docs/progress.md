# Progress

Date: 2026-10-25. The first release (matching), the second release (journals, adjusting items, period-end statement,
posting file, task list) and the third release (change requests, two environments, the documents under
`docs/deliverables/`) are implemented and committed.

| Stage | State |
|---|---|
| First release: foundation, import, matching, manual match, screens, 13 mutation sites | done, tested |
| Second release 0: numbered migration, balances in the import, continuity of bundles, schema 2 workbook | done, tested |
| Second release 1: reason codes -> journal drafts and adjusting items | done, tested |
| Second release 2: journal validation (3 moments), CSV import, approval route, immutability after approval | done, tested |
| Second release 3: period-end statement (OJP shape, as of the period end) | done, tested; the sample reaches a difference of 0 |
| Second release 4: posting file (SAP F2548 field names), release, double-count guard | done, tested; not tried in SAP |
| Second release 5: task list (due dates, flow, conditions, undoing a completion), auditor | done, tested |
| Mutation tests: 25 places | done, `docs/validation/mutations.md` |
| Acceptance against the real server (48 checks; the client script was not kept) | done, `docs/validation/acceptance.md` |
| Third release: change requests (form, states, people, test on throw-away files, apply, rollback), environments in the path and the hidden field, posting file from production only | done, tested (`tests/test_changes*.py`) |
| Third release: the documents (test cases, summary report from pytest's junit, two manuals, question log, feature map) | done; TC-18 to TC-20 were walked in headless Edge on 2026-10-25 and the screenshots are in `docs/deliverables/images/`; the report counts them as held until a person looks |
| Mutation tests: 34 places | done, `docs/validation/mutations.md` |
| Rules set against the documents of other reconciliation products (2026-10-26): a difference inside the tolerance finished by the tool (「差額の相手勘定」, journal drafted by the tool), duplicates take part in matching, a candidate keeps its rows, bundles without amount tolerance, the range of days a workbook setting | done, tested (`tests/test_tool_drafts.py`, `tests/test_matching.py`); the Splink second half was run once more with the same rules and the numbers did not move (`docs/validation/results.md`) |
| Mutation tests: 36 places | done, `docs/validation/mutations.md` (sites 2, 3, 8, 35, 36 were run again after the rules changed) |

Tests: `.venv\Scripts\python -m pytest -q`.

## Left for the person who owns the machine
- Look at the screens yourself in a browser (the screenshots in `docs/deliverables/images/` were taken by the builder).
- `LICENSE` is MIT with the git author's name as the copyright holder, put there as a default before publishing; change it if you choose another license or name.
- `README.md` lists the install commands; they are typed by a person (the code never installs anything).

## Known limits (also in the README and the ADRs)
- The posting file has not been uploaded to SAP; the heading text of SAP's template and its date format are not verified.
- Equality-only matching rules: auto recall on the Splink data is 0.247 (precision 0.996). Not tuned after the second half.
- Reconify eval 008 (token similarity) cannot be expressed; eval 002 differs on purpose (the duplicates make one ambiguous candidate with a mark).
- camt.052 reports with zero balances at both ends and entries are refused; EBCDIC Zengin files are refused.
- One level of approval; no foreign currency; no urgent flow for change requests; data is not copied between environments.
- Someone logged in to the PC as its OS user can edit the database file itself (`docs/adr/0008`, trust boundary).
- The cases TC-18 to TC-20 of `docs/deliverables/test-cases.md` were walked by the builder in a headless browser; they stay
  "held" in the summary report until a person walks them.
- The business-day count (day 0 = the period end) follows Oracle's Close Manager "Day Zero"; the range of days is a setting
  of the workbook (Oracle: the days a template tracks before and after the Zero day); the default -20..40 is the author's
  design decision (`docs/sources.md`).
