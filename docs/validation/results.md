# Second-half result (Splink data), next to the targets

Run once on 2026-10-24 with `tools/run_splink.py h2 --final` (a marker file blocks a second run). The rules are the two
fixed in `docs/validation/splink_rules.md` (written and committed before this run) and were not changed afterwards.
Data: second half = origin rows dated 2022-04-03 to 2022-05-10 and their destination partners: 22,434 + 22,434 rows,
22,434 true pairs. Environment: sandbox, one version of the workbook.

| Measure | Target (`targets.md`) | Result | Reached? |
|---|---|---|---|
| Auto precision | >= 0.99 (the author's design decision; no source gives a figure) | **0.9959** (5,541 correct of 5,564 auto pairs) | yes, against that value |
| Auto recall | recorded only | 0.2470 (5,541 of 22,434 true pairs) | - |
| Auto + candidate recall | recorded only | 0.5752 (12,904 of 22,434) | - |
| Rows in class 重複 | recorded only | 0 left, 0 right (the data has no reference column) | - |
| Rows in class 未照合 | recorded only | 7,050 left, 7,491 right | - |

Rows by class (left = origin, right = destination):

| Class | Left | Right |
|---|---|---|
| 自動 | 5,564 | 5,564 |
| 候補 | 9,820 | 9,379 |
| 未照合 | 7,050 | 7,491 |
| 重複 | 0 | 0 |

Groups: 自動/完了 1,862 · 自動/差額の処理待ち 3,702 · 候補/確認待ち 6,793 (ambiguous 2,399, amount_diff 2,331,
timing_diff 8, candidate rule 2,055). Match run time 0.6 s for 44,868 rows (import 0.3 s).

For comparison, the first half with the same rules: precision 0.9956, recall 0.2684, auto + candidate recall 0.5792.
The two halves agree to within 0.002 on precision, so the precision target is not just a first-half fit.

## Run once more on 2026-10-26, after the rules of the tool changed

The matching rules of the tool were set against the documents of other products (ADR 0003, "Revision of 2026-10-26"):
bundles no longer use an amount tolerance and compare dates only when the date is a bundle key (Oracle), duplicate rows take
part in matching (Reconify `flag`), a candidate keeps its rows against later rules (the author's decision; Odoo says only that the first model in the
sequence is applied), and a pair inside the tolerance whose rule names an account is finished with a journal draft (the
author's decision; Oracle creates an external transaction in cash management, and Odoo reconciles the difference with the
account of a counterpart entry, for underpayments only). Because the rules changed, the second half was
run once more with the **same two pass rules** of `splink_rules.md` (not touched), the same files (sha256 as in
`docs/sources.md`), through `tools.run_splink.run("h2")` from a script (the command line's marker file blocks a second
run of the command). The earlier result above is kept as it is.

| Measure | Run of 2026-10-24 | Run of 2026-10-26 |
|---|---|---|
| Auto pairs / correct | 5,564 / 5,541 | 5,564 / 5,541 |
| Auto precision | 0.9959 | 0.9959 |
| Auto recall | 0.2470 | 0.2470 |
| Auto + candidate recall | 0.5752 | 0.5752 |
| Rows 自動 (left / right) | 5,564 / 5,564 | 5,564 / 5,564 |
| Rows 候補 | 9,820 / 9,379 | 9,820 / 9,379 |
| Rows 未照合 | 7,050 / 7,491 | 7,050 / 7,491 |
| Rows 重複 | 0 / 0 | 0 / 0 |
| Groups | 自動/完了 1,862 · 自動/差額の処理待ち 3,702 · 候補/確認待ち 6,793 | the same |
| Candidate reasons | ambiguous 2,399 · amount_diff 2,331 · timing_diff 8 · candidate rule 2,055 | the same |
| Match run time | 0.6 s | 0.7 s |

Nothing moved, and the reason is in the rules, not in a hidden change: both rules are 1対1 on the memo, amount and date;
the data has no reference column, so no row is a duplicate; the second rule is a 候補 rule, which never used the rows of an
earlier candidate; no bundle rule is in the set; and the first rule names no account for the difference, so the 3,702 pairs
with a difference stay in 差額の処理待ち as before. The new behaviour is covered by the tests of `tests/test_matching.py` and
`tests/test_tool_drafts.py`, not by this data.

## Why the recall is low (suspected causes; not tuned away)
- About 75 percent of the true pairs have a different amount on the two sides (differences of a few percent: median 2.0
  percent, 90 percent of them within 5 percent; measured on
  `var/splink/*_h2.csv`) and the
  memos are mostly different strings. The comparison is "equal" only, so apart from a tolerance on the amount nothing
  can link such a pair; there is no reference column to key on.
- The memo rule needs an equal memo; a rule on amount and date alone had a precision of 0.95 on the first half and was
  therefore only allowed to make candidates.
- The reference figure in the Sensiba article (80% to 90% of an account's transactions in a given period are in the
  "matched transactions" category, which holds the transactions whose key details such as the check number and amount match
  general ledger data precisely) is not a figure for this test data. The article does not say what data it describes.
  Here, the true pairs mostly have different amounts and different memos, so exact rules cannot link them and the recall
  is not comparable. No attempt was made to raise it after seeing this result.
- Candidates reach 0.5752: the possible partner is shown to a person, who confirms it. The largest part (ambiguous and
  amount_diff candidates) comes from memos that many rows share.
