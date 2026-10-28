# Validation targets

Written and committed before any matching run on the Splink data. Not changed afterwards.

| Measure | Target | Kind |
|---|---|---|
| Auto precision (correct auto pairs / auto pairs) | >= 0.99 | Target. A wrong automatic match becomes an accounting error. The value 0.99 is this project's own choice. |
| Auto recall (correct auto pairs / all true pairs) | none | Recorded only. Reference: the Sensiba article says that transactions for which key details (such as the check number and amount) match general ledger data precisely are "matched transactions", and "In most instances, this will include 80% to 90% of an account’s transactions in a given period." |
| Auto + candidate recall | none | Recorded only. |
| Number of rows in class 重複 (duplicate) | none | Recorded only. |
| Number of rows in class 未照合 (unmatched) | none | Recorded only. |

Formulas are in `docs/validation/metrics.md`. A denominator of 0 is reported as 該当なし (not applicable).

Rules on how the Splink data is used:

- The data is split by date into a first half and a second half (details in `docs/sources.md`).
- The rules are written looking only at the first half.
- The second half is run once. The result is written to `docs/validation/results.md` next to this file and the rules are not changed afterwards to fit it.
- If a target is not reached, the figure is written as it is, with the suspected cause.

Note added on 2026-10-26 (the targets above are those committed before the first run): no source gives a figure for the value 0.99
(BlackLine, Sensiba, Reconify, hyperswitch and Splink give no precision figure). It is the author's design decision
(`docs/sources.md`); "reached" in `results.md` means reached against that value only.
