# Splink data: split and rules (written before the second half was run)

## Split
- Tool: `tools/convert_splink.py` (run with `.venv-convert`, the only place pyarrow is used).
- Rule decided before looking at any result: split by the **median origin date** (lower median of the 45,326 origin
  dates) = **2022-04-02**. Origin rows dated on or before it are the first half (h1), later ones the second half (h2).
  Each destination row follows the origin row that has the same `ground_truth` value (every value occurs exactly once on
  each side, checked by the tool).
- h1: 22,892 origin rows, 22,892 destination rows, 22,892 true pairs (origin dates 2022-01-01 to 2022-04-02).
- h2: 22,434 origin rows, 22,434 destination rows, 22,434 true pairs (origin dates 2022-04-03 to 2022-05-10).
- Amounts are converted with `Decimal(str(value)) * 100` to integers (minor units).

## What the first half shows (only h1 was looked at)
- Date delay origin -> destination: 1 or 2 days for 78 percent of the pairs, 3 to 6 days for 15 percent, 7 to 21 days for 7 percent. Never 0, never more than 21.
- Amounts: 25 percent of the true pairs have the same amount; for the rest the destination amount differs by a few percent (median 2.1 percent of the origin amount, 90 percent of them within 5 percent; median absolute difference 7.50, 90th percentile 881.89).

The two lines above are measured on `var/splink/*_h1.csv` (the shares are 77.9 + 15.1 + 7.0 percent). The table below
was run again on 2026-10-26 (after the second half; the rules were not touched) with
`tools/run_splink.run("h1", rules=[...])`, one automatic rule at a time, and every row came out the same.
- There is no reference column. Memos are different strings for the same transaction in most pairs, equal in some.

## Rules tried on h1 (precision = correct auto pairs / auto pairs)
| Rule set (auto rule) | auto pairs | precision | recall |
|---|---|---|---|
| amount equal, date within 1 day | 2,398 | 0.9149 | 0.0958 |
| amount equal, date within 2 days | 4,529 | 0.9523 | 0.1884 |
| amount equal, date within 3 days | 4,730 | 0.9385 | 0.1939 |
| memo equal, amount equal, date within 21 days | 2,088 | 1.0000 | 0.0912 |
| memo equal, amount within 2.00, 21 days | 3,753 | 0.9997 | 0.1639 |
| memo equal, amount within 10.00, 21 days | 5,544 | 0.9982 | 0.2417 |
| memo equal, amount within 30.00, 21 days | 6,172 | 0.9956 | 0.2684 |
| memo equal, amount within 100.00, 21 days | 6,375 | 0.9895 | 0.2756 |

Amount and date alone cannot reach the 0.99 target (the best was 0.9523): when 75 percent of the true partners carry a
different amount, the rows that are left over often find an unrelated row with the same amount.

## Rules fixed (not changed after the second half)
1. 自動: memo equal, amount within 30.00 (`amount_tolerance_minor` 3000), date within 21 days. Chosen as the largest
   tolerance whose h1 precision stays at or above 0.995 (a margin over the target 0.99).
2. 候補: amount equal, date within 6 days (rows not already in a candidate or an automatic match).

h1 result of these two rules: auto 6,172 pairs, 6,145 correct, precision 0.9956, recall 0.2684; auto + candidate recall 0.5792.

*Added on 2026-10-26:* the tool's rules were changed after the above was written (ADR 0003, "Revision of 2026-10-26"); the
two rules were not. The second half was run once more with them; the result is in `results.md` next to the first one.
