# How the rates are counted

Used by `close_kit/metrics.py`, the "answer file" box on the match result screen, and `docs/validation/results.md`.

- **True pairs (T)**: the pairs in the external answer. A bundle (several rows on one side or both) is expanded
  to every pair (left row, right row) inside it, in the answer and in the tool's own groups alike.
- **Auto pairs (A)**: pairs inside groups of kind 自動 (state 完了 or 差額の処理待ち).
- **Candidate pairs (C)**: pairs inside groups of kind 候補 (state 確認待ち). A candidate that contains several
  possible partners (ambiguous) counts every pair inside it.
- Manual groups (kind 手) are not part of any rate: the answer file is meant to measure the rules, not people.

| Measure | Formula |
|---|---|
| Auto precision | \|A ∩ T\| / \|A\| |
| Auto recall | \|A ∩ T\| / \|T\| |
| Auto + candidate recall | \|(A ∪ C) ∩ T\| / \|T\| |
| Unmatched, duplicates | number of rows (not pairs); the screen shows the total, `results.md` shows left and right from the class counts |

When a denominator is 0 the figure is reported as 該当なし (not applicable), never as 0 or 1.

The answer file for the screen is a CSV with the header `左の行,右の行`; each line holds the `ext_id` of a left row
and of a right row. For CSV data sources `ext_id` is the row key (the columns named in the data source sheet), or the
line number when there is none; for Zengin it is the record number; for camt.052 it is `date|reference number`.
An `ext_id` names a row only within its side. When one used by the answer file or by a group is shared by two
rows of the match set (for example the record numbers of two Zengin files, or the line numbers of two CSV files
without a row key), the rates are refused with the names, instead of being counted against the wrong row.
