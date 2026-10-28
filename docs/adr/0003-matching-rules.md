# 0003. Matching rules

Status: accepted (2026-10-24); revised 2026-10-26, when the rules were set against the documents of other reconciliation products (the revision is
listed at the end of this file)

## Context

Two statements (a ledger side on the left, a bank side on the right) are compared by pass rules, applied in the order
of their "順番" column. A wrong automatic match becomes an accounting error, so the rules must say exactly what
becomes automatic, what is only suggested, and what is left for a person. The names and meanings come from the
public material of several projects and products; where the meaning is not the same the text says so.

Sources (all public; details and licenses in `docs/sources.md`):
- Reconify (MIT), https://github.com/ReconifyHQ/reconify: `matched` / `amount_diff` / `timing_diff` / `unmatched`,
  `date_window`, `amount_tolerance_minor`, duplicates (`duplicate_policy`), one-to-many and many-to-many passes, the evals.
- hyperswitch-reconciliation (Apache-2.0), https://github.com/juspay/hyperswitch-reconciliation: the comparison
  operator is only "equals"; a `grouping_field`.
- Oracle Fusion Cloud Financials (Cash Management, Receivables Credit to Cash): tolerance rules, matching rules, the
  reconciliation exceptions. Odoo documentation 17.0 "Reconciliation models" (CC BY-SA 4.0). NetSuite, "Custom Matching
  Rules". Ideas and one sentence each only.
- BlackLine's public blog and overview (ideas only): automatic and suggested pass rules; reason codes.

## Decision

### The rows (the first three terms are Reconify's; the table as a whole is the author's composition of the sources below)

| How the rows meet | Class | State | Stays in the remainder? |
|---|---|---|---|
| Automatic rule; date inside `date_window`; amount difference 0 | 自動 | 完了 | no |
| Automatic 1対1 rule; date inside; amount difference inside `amount_tolerance_minor` but not 0; the rule names an account for the difference (「差額の相手勘定」) | 自動 | 完了; the tool drafts the journal of the difference (below) | no |
| The same, but the rule names no account | 自動 | 差額の処理待ち | no |
| Automatic rule; the key fields are equal but the amount is outside the tolerance (amount_diff) or the date is outside the window (timing_diff) | 候補 | 確認待ち | yes |
| A rule of kind 候補 hit | 候補 | 確認待ち | yes |
| More than one possible partner on a side (ambiguous) - duplicates included | 候補 | 確認待ち (never automatic) | yes |
| No rule hit | 未照合 (or 重複: the row shares its reference with another row of its data source) | - | - |

- Reconify's `matched` / `amount_diff` / `timing_diff` are taken with the same meaning. Its two documents name the
  first one differently (the README says `match`, the matching page says `matched`); this tool uses its own Japanese
  names and keeps Reconify's words only for the candidate reasons (`amount_diff`, `timing_diff`, `ambiguous`).
- Amount difference is `left total - right total`, tolerance and window are inclusive (`<=`).

### D6. A difference inside the tolerance is finished by the tool, through a journal that another person approves (accepted 2026-10-26)
Closest sources: Oracle Fusion Cash Management, "Overview of Tolerance Rules": *"In this scenario of a one to one type match,
any amount difference within tolerance is automatically created as an external transaction in cash management."*;
Odoo documentation 17.0, "Reconciliation models", said of the model "Invoices/Bills partial match if underpaid": *"The
difference is reconciled with the account indicated in the counterpart entries tab."* The same page says of that model's
payment tolerance: *"It is disregarded when an overpayment is received."* Oracle's sentence creates an external transaction
in cash management, not a journal; Odoo's is about underpayments. Neither page says that a journal is drafted for a second
person to approve. That the tool drafts the journal when the rule names an account, and that it does so for a difference in
either direction, is the author's decision.
- The pass-rule sheet has a column 「差額の相手勘定」. A 1対1 automatic rule that names an account makes a pair inside the
  tolerance **完了** at once, and the run drafts the journal of the difference: the deposit account of the match set against
  that account, the direction of ADR 0006 (d = bank side - book side). The journal is an ordinary journal in 下書き, linked
  to the group, with the way it was made `照合の差額` and a maker that is the tool's own user 「照合の道具」 (a user with no role,
  whose password hash matches nothing, so nobody logs in as it). It goes through the approval of ADR 0006 unchanged: any
  person with the role 承認者 who is in the amount range approves it, and every such person is someone other than the
  maker (Financial Services Agency: approval by a person other than the one who made it). A person with the role 作成者
  submits or withdraws it (the maker is not a person, so "only the maker" cannot apply); that person is not the maker
  either.
- If the journal cannot be drafted (the date has no open period, an account is missing, the memo starts with a formula
  character) the pair is left in 差額の処理待ち and the run says why; a person then uses a reason code as before.
- A rejected or withdrawn tool draft gives the pair back to 差額の処理待ち, so the reason-code route (ADR 0006) stays open.
- The account is only for 1対1 automatic rules (the workbook refuses it elsewhere): bundles have no amount tolerance
  (below), a 候補 rule makes no match to book.
- **Author's design decision.** A rule without 「差額の相手勘定」 keeps the earlier behaviour: 差額の処理待ち, and a person
  attaches a reason code. Looked for in Odoo's and Oracle's pages: what they do when no account is given is not
  described. A journal needs an account, so with none named nothing can be drafted and the pair waits for a person; the
  reason-code route stays for this case and for manual matches.
- BlackLine's Transaction Matching overview lists *"Create reconciling items and adjusting journal entries"* and
  *"Required adjustments or timing items"* (the earlier reason for waiting; it is kept as the reason for the reason-code route).
  BlackLine's public example also puts a date condition inside an automatic rule (claims paid within 30 days of the claim
  date); the `date_window` itself is Reconify's.

### D1. Difference candidates need an identifier field in the rule (accepted)
`amount_diff` and `timing_diff` candidates are made only by rules whose equality fields include 参照 or 摘要, that is,
a field other than 金額 and 勘定日. Source (Reconify, matching page, "Reference matching"): *"When a left and right
transaction share the same reference: [...] They are `amount_diff` when the date is acceptable but the amount exceeds
tolerance. They are `timing_diff` when the amount is acceptable but the date is outside the date window."* ([...] marks the first bullet, left out here: *"They are `matched` when amount and date
are within the configured rules."*) Without a
shared identifier, two rows with different amounts have nothing that says they belong together, so they are not paired.

### D3. Comparison is equality only; Reconify eval 008 is not expressible (accepted)
Rules compare fields for equality only (the hyperswitch rule type: `/// Match operator for field comparison
(currently only equals)`). Reconify eval 008 needs `name_mode: tokens` (Jaccard similarity of the words of the
description, threshold 0.5), which is not equality. It is recorded as "not expressible", not forced. The directory
keeps Reconify's name `008-ambiguous-reference`; its content is token matching, not an ambiguous reference.

### Candidates
- Possible partners are found as connected sets of "could be a partner" links inside one rule. A set with one left
  and one right row and a full link is an automatic match; any larger set, or a set whose only link is a partial one,
  becomes **one** candidate holding all its rows. Source for "more than one possible partner is not decided by the tool":
  Oracle Fusion, "Overview of Reconciliation Exceptions": *"Ambiguous : This exception occurs when either there is more than
  one application transactions that could match to the line or the transaction could match to more than one statement
  line."*; NetSuite, "Custom Matching Rules": *"If a rule finds two or more possible matches, NetSuite can't choose one, so
  you need to pick and match the correct transaction yourself."*
- **A candidate keeps its rows (author's decision).** If a rule has made a candidate, a later rule - automatic or not -
  does not use any row of it; the candidate stays whole until a person decides it. Closest source (Odoo documentation
  17.0, "Reconciliation models", CC BY-SA 4.0): *"If a record matches with several reconciliation models, the first one in
  the sequence of models is applied."* That sentence says only which model applies; that a candidate keeps its rows against
  later rules is the author's decision (before 2026-10-26 a later automatic rule dissolved the candidate of an earlier rule;
  that is gone). A
  consequence the person who writes the rules has to know: a 1対1 rule on a reference in front of a bundle rule on the same
  reference holds the rows of an ambiguous bundle (for example three instalments and one payment) as a candidate, and the
  bundle rule never sees them; a bundle rule meant to take such rows goes in front.
- **Author's design decision:** a candidate larger than 20 rows is not made; its rows stay in the remainder. Looked for in
  Reconify, hyperswitch, Oracle and BlackLine material: no document gives a size limit for a candidate. The number only
  bounds what a person is asked to read at once.

### Bundles (types 1対1, 1対多, 多対1, 多対多)
- Named after Reconify's `one_to_many` ("one aggregate row against several rows sharing a group key") and
  `many_to_many` ("groups on both sides whose totals should be compared"); the grouping key is also hyperswitch's
  `grouping_field`.
- **D4.** The type says which side is bundled, exactly as in the eval files: 1対多 = the left has one row, the right is
  bundled (Reconify's reference config for eval 005 sets `group_col` on the *right* source); 多対1 = the left is
  bundled. The workbook refuses a rule whose type does not agree with the bundle keys that are filled: 1対1 none,
  1対多 right only, 多対1 left only, 多対多 both. Left and right are kept as in the eval files so that expected
  results compare directly.
- Rows are bundled by the key fields of that side, the two bundles are paired by the *pairing key* (the value must be
  the same in every row of a bundle, and there must be exactly one bundle per side for that value).
- **D7. No amount tolerance for bundles; the date only when it groups (2026-10-26).** Oracle Fusion Cash Management,
  "Overview of Tolerance Rules": *"No reconciliation amount tolerances are allowed in one to many, many to one, or many to
  many matching scenarios."* and, "Reconciliation Matching Rules": *"You can use date, transaction type, and reconciliation
  reference as matching criteria only after you select these as grouping attributes."* So: the totals of the two bundles
  must be **equal** (the rule's `amount_tolerance_minor` is not used by a bundle rule - Oracle's page says the tolerance of
  a rule that is not one to one "is ignored"); and the dates of the two bundles are compared (through each bundle's latest
  date, within `date_window`) **only if 勘定日 is one of the bundle keys** of the rule; otherwise the date is not a
  criterion of that rule. *Author's design decision:* the latest date of a bundle as the date of the bundle - the Oracle pages
  do not say which date stands for a group.
- A bundle larger than the rule's row limit is not made; its rows return to the remainder and the run reports it.
  Subset-sum search (Reconify's `subset_sum`) is **not** built: no combination is searched for.
- Totals that differ, with 参照 or 摘要 as the pairing key, give a candidate (amount_diff / timing_diff) as for rows.

### D5. Duplicates take part in matching and are shown with a mark (accepted; changed 2026-10-26)
A duplicate is a row whose non-empty reference is shared with another row of the same data source (empty references
are never duplicates, as in Reconify). Reconify's documents disagree about what happens next. Matching page: *"Only the
first occurrence participates in matching."* README: *"Duplicate detection is an annotation pass. It reports duplicate
groups but does not discard rows or prevent them from participating in matching."* Reconify's duplicates guide adds the
setting `duplicate_policy` with the default `flag`: *"all rows participate in matching, and duplicates are surfaced via
`WriteDuplicate` events for review."* This tool follows `flag`: **duplicate rows take part in every rule like any other
row, and the screens show a duplicate mark (「重複」) on them.** When a duplicate row has more than one possible partner, or
its partner has more than one (the case of two ledger entries of one payment), the rows are one candidate (ambiguous,
see above), never an automatic match, and a person decides. A duplicate row that no rule took keeps the class 重複 (it is
still reachable by a manual match). Reconify's expected result for eval 002 (first row matched, second unmatched) differs
on purpose; `docs/validation/reconify_evals.md` shows it. The mark is a fact about the reference (it is also shown on the
rows of a bundle, which share a reference by design; Reconify avoids that with its `group_col` for the duplicate key, which
this tool does not have).

### Manual match and approval
Source: BlackLine's Transaction Matching overview, *"Manual matches can be made subject to a workflow approval
process"*, and its Account Reconciliations sheet, *"Role-based workflow approval process with built-in segregation of
duties"*. In BlackLine the approval can be switched on; here it is always on (`docs/deliverables/qa-log.md` Q-06).
Rows of class 候補 / 未照合 / 重複 can be grouped by hand by a 作成者. The group waits in 承認待ち; another user with
the role 承認者 approves it (完了, or 差額の処理待ち when the two sides differ) or rejects it (back to 候補). The person
who made the group cannot decide it: checked with the user id on the server, whatever roles that user has.

### Other decisions
- **Two data sources only.** A bank reconciliation compares two records, the books and the bank: the OJP sheet,
  *"The first step of the bank reconciliation process is to compare the business records deposits to the bank
  statements deposits, and mark the items that are located on both records."*; Oracle Fusion Cash Management, matching
  rules *"help you match bank statement lines and system transactions"*. BlackLine's public material states no limit on
  the number of data sources; matching three or more sources in one rule set is not built (see ADR 0005).
- **Re-run.** Groups in 完了 / 差額の処理待ち / 承認待ち are fixed with their ids (later features attach journal entries
  to them). Everything else is recomputed from the remaining rows, including a rejected manual group: rejecting sends
  it back to 候補 / 確認待ち, and candidates are not fixed. The rejection itself stays in the operation log. *Author's
  design decision;* no model describes how a rerun treats groups that were decided earlier - the groups a person or a
  journal depends on keep their ids, which is what the journal's link needs.
- **Accounting period.** Every group has a period (that of its latest booking date). A match across a period end
  (ledger on 3/31, bank on 4/2) is a normal automatic match; what it means at the period end is decided in the balance
  statement (ADR 0006): a book row whose bank partner is dated after the period end is a timing item of that period.
- **Second release additions.** The re-run also fixes groups in 仕訳の承認待ち. Rows that carry a journal, and rows in the
  classes 処理済み / 転記済み, leave the remainder; a row with an adjusting item stays in it. Manual matches may not use a
  row with a journal. The statement of ADR 0006 is built over these classes. The OJP sheet used for the statement prints
  a total that does not add up (book side 98,860 calculated, 99,000 printed); the tests use the calculated figure and the
  difference of 140 is recorded in ADR 0006.
- **Volume.** Rows are indexed by key (reference, or amount order) before comparison; all pairs are never compared.
- **Normalisation.** Reference and memo are NFKC-normalised and whitespace-collapsed before comparison.
- **D8. Test data split.** For the Splink test data, destination rows follow their origin partner and the split is by
  the median origin date (2022-04-02). The rules are written from the first half only and the second half is run once
  (`docs/validation/targets.md`, `splink_rules.md`, `results.md`). After the rules of 2026-10-26 the second half was run
  once more with the same two rules (`results.md`).

## Alternatives considered

- **Score-based matching (the Fellegi-Sunter model).** Splink's documentation describes it as the statistical model
  behind Splink. Not built: the tool runs with no statistical model (ADR 0001, Context) and the rules above say exactly
  what becomes automatic. Splink is used only for its test data (D8).
- **Similarity of names (Reconify's `name_mode: tokens`).** Not built; rules compare for equality only (D3). Reconify
  eval 008 uses it and is recorded as not expressible.
- **Searching for combinations of rows whose amounts add up (Reconify's `subset_sum`).** Not built (Bundles). The subset
  sum problem is known to be NP-complete (Wikipedia, "Subset sum problem"); rows are bundled by a key and the totals
  compared instead.
- **Other rules on the Splink first half** (`docs/validation/splink_rules.md`). Amount equal with a date window of 1, 2
  or 3 days gave a precision of 0.9149, 0.9523 and 0.9385, all under the target of 0.99. With the memo equal, a tolerance
  of 2.00, 10.00, 30.00 and 100.00 gave 0.9997, 0.9982, 0.9956 and 0.9895. 30.00 is the largest tolerance whose precision
  stays at or above 0.995, so it is the one fixed; the smaller ones find fewer pairs.
- **Duplicates.** Reconify's documents differ: the matching page lets only the first occurrence take part, the README
  treats duplicates as an annotation, and the duplicates guide has a setting `duplicate_policy` with four values (`flag`,
  `keep`, `merge`, `latest`; default `flag`). This tool follows `flag` (D5). In the first version duplicate rows skipped
  the 1対1 rules and went to a person; that was replaced on 2026-10-26 (revision table).
- **A later automatic rule dissolving the candidate of an earlier rule.** The first version, the author's design:
  BlackLine's blog says only that a suggested match will be affected by any subsequent pass rules. Replaced on
  2026-10-26 by the author's decision that a candidate keeps its rows; no other reason is recorded, and the effect is
  that the order of the rules is the order of priority (Consequences).
- **A tolerance and a date window in bundle rules.** The first version used the rule's tolerance and always compared
  dates. Oracle's pages allow no amount tolerance outside one to one matching, so it was replaced (D7).
- **Leaving every pair with a difference to a person.** The first version: such a pair waited in 差額の処理待ち until a
  person made the journal. With 「差額の相手勘定」 the tool now drafts the journal (D6); a rule without it keeps the first
  behaviour.
- **Approval of manual matches as an option.** BlackLine's overview says an approval process can be applied to manual
  matches. Here it is always on and no setting switches it off (`docs/deliverables/qa-log.md`, Q-06).
- **Three or more data sources in one rule set.** Not built; a bank reconciliation compares two records (see "Other
  decisions"). BlackLine's overview states no upper limit on the number of data sources.

### Where the three hard decisions are in the code

Line numbers are those of `close_kit/matching.py` in this commit; the function names stay if the lines move.

1. **What may become automatic.** Options above: a score (Fellegi-Sunter), similarity of names, or equality with a
   tolerance and a date window. Chosen: equality, with the tolerance and window of the rule, because the result must be
   explained by the rule alone and no statistical model runs (ADR 0001). The width was fixed on the first half of the
   Splink data only (2.00, 10.00, 30.00, 100.00 compared; 30.00 kept, `docs/validation/splink_rules.md`) and then measured
   once on the second half (`docs/validation/results.md`). Code: `_rule_one_to_one` (lines 144-189). A rule with an
   identifier field compares only rows that share it (an index, lines 166-179); a rule on amount and date compares only
   rows whose amounts lie inside the tolerance (a sorted list and a binary search, lines 180-188). The second half,
   44,868 rows, was matched in 0.6 s (`docs/validation/results.md`).
2. **A row with more than one possible partner.** Options: let the tool pick one, or leave it to a person. Chosen: a
   person (Oracle "Ambiguous", NetSuite, above). Code: `_apply_components` (lines 191-226) joins every row to the rows it
   could pair with; only a component of exactly one row on each side with a full match becomes automatic, every other
   component becomes one candidate with the reason `ambiguous`; a candidate larger than `CANDIDATE_MAX_ROWS` (20, line 34)
   is not made.
3. **Several rows against one.** Options: search for combinations whose amounts add up (Reconify's `subset_sum`; the
   problem is NP-complete) or bundle rows by a key. Chosen: a key, with equal totals and no tolerance (Oracle, D7). Code:
   `_bundles` (lines 229-252) and `_rule_bundle` (lines 254-281). Together with `_free` (lines 115-117) and
   `_make_candidate` (lines 129-141), an earlier rule's candidate keeps its rows (the author's decision, above), so the order of the rules
   is the order of priority.

## Consequences

- A person sees every pair that is not clean: a pair with a difference is finished only through a journal that
  another person approves; a pair with more than one possible partner is never decided by the tool.
- Recall suffers when only equality is available (see `docs/validation/results.md`); precision is the target.
- The table of rows is the part of the code with the most tests, and the mutation tests break it in several places.
- A candidate of an early rule holds its rows against every later rule, so the order of the rules is the order of
  priority (Odoo has a sequence of models, of which the first that matches is applied; holding rows is the author's decision); in the sample workbook the three instalments of INV-2610-005 are a candidate of rule 10
  and the bundle rule 40 does not take them.

## Revision of 2026-10-26

| Before | Now | Source |
|---|---|---|
| A pair inside the tolerance with a difference waits in 差額の処理待ち until a person makes the journal | With 「差額の相手勘定」 the pair is 完了 and the tool drafts the journal; without it, as before | Oracle (tolerance rules: an external transaction is created), Odoo (reconciliation models: the underpaid-invoice model); the journal draft is the author's decision |
| Duplicate rows skip the 1対1 rules and go to a person | Duplicate rows take part and carry a mark; several possible partners make a candidate | Reconify `flag`; Oracle "Ambiguous"; NetSuite |
| A later automatic rule dissolves the candidate of an earlier rule | The candidate keeps its rows; later rules do not use them | Author's decision; Odoo only says "the first one in the sequence" is applied |
| Bundles use the rule's tolerance and always compare dates | Equal totals only; dates compared only if 勘定日 is a bundle key | Oracle (tolerance rules; matching rules) |
| The range of days from the period end is fixed at -20 to 40 | A setting of the workbook (説明 sheet), -20 to 40 when empty (ADR 0007) | Oracle Close Manager |
