# 0006. Journals, reason codes, adjusting items, the balance statement and the posting file

Status: accepted (2026-10-24)

## Context

A match with a difference, or a row nobody matched, has to end in something a person can approve: a journal entry that
puts the books right, or - for an item that only crosses the period end - an adjusting item that is carried until the
bank shows it. At the period end a statement must show that the adjusted bank balance equals the adjusted book
balance, and what is left over must be called what it is: an unidentified difference. Approved journals must leave the
tool in a file a general ledger can take. Sources are listed in `docs/sources.md`; where no source decides
something, the text says **author's decision**: what was decided, the closest source (or that none was found)
and how the decision follows from it.

## Decision

### Reason codes and what they may be attached to

| Object | Treatment |
|---|---|
| a group in 差額の処理待ち (a manual match whose two sides differ, or an automatic one whose rule names no account for the difference) | journal only |
| an unmatched row of the **bank** side (a fee or interest the books do not have yet) | journal only |
| an unmatched row of the **book** side (a receipt or payment the bank has not shown yet) | journal or adjusting item |

Reason: a timing item is a whole transaction that crosses the period end, so it is carried on the bank side of the
reconciliation as an adjusting item. A difference, and a movement the bank has and the books lack, is missing from the
books and is put right by a journal. The US Department of Justice's reconciliation sheet separates exactly these two
(items to adjust the bank balance, items to adjust the book balance). An error made by the bank (something the bank has
to correct) is not built. Duplicates and candidates take no reason code: they are first matched by hand or sorted out.
BlackLine's public material says reason codes drive journal creation and that adjustments or timing items can be created
in bulk and moved to a reconciliation. Taken from the OJP sheet: rows 2 and 3 (the bank side's items adjust the book
balance, the book side's items adjust the bank balance). Row 1 (a difference inside a pair is put right by a journal):
Odoo documentation 17.0, "Reconciliation models" (CC BY-SA 4.0), said of the model "Invoices/Bills partial match if
underpaid": *"The difference is reconciled with the account indicated in the counterpart entries tab."* (the page says of
that model's payment tolerance: *"It is disregarded when an overpayment is received."*); Oracle Fusion
Cash Management, "Overview of Tolerance Rules": *"any amount difference within tolerance is automatically created as an
external transaction in cash management."* Oracle's sentence creates an external transaction in cash management, not a
journal. Neither page says that a journal is drafted for another person to approve; that is the author's decision: when the rule
names an account (「差額の相手勘定」), the tool drafts that journal itself - its maker is the tool's own user - and another
person approves it (ADR 0003 D6, below); when the rule names none, a person attaches a reason code and makes the journal.

**Author's design decision: a book-side row may also take a journal** (not only an adjusting item). No source was
found: the OJP sheet lists the items that adjust the bank balance and the items that adjust the book balance, but does not
say whether a row the books already hold may be put right by a journal. What the tool does: the reason code's column 処理
(仕訳 / 調整項目) decides, and a book-side row accepts both.

The journal draft has two lines: the deposit account of the match set and the reason code's counter account. With
d = (bank side total) - (book side total) - the one function `bank_minus_book` converts the first release's
`left - right` - d > 0 debits the deposit account and credits the counter account, d < 0 the other way, d = 0 makes no
journal. A group's date is the latest date of its bank rows; a row's journal has the row's date. A date inside a closed
period moves to the first day of the first open period after it and the screen says so.

### Journals and their validation
- A journal is a header (number `JE-` + 6 digits, date, period, memo, who, how - reason code / typed / imported -, what
  it settles, environment) and lines (account code, debit, credit, memo), like the Header Items and Line Items of
  BlackLine's journal entry product. It settles one group or one row; that link keeps the journal tied to the
  reconciling item (*"If necessary, tie each JE to the appropriate reconciling item or matching transaction."*).
- Five rules, run by one function when a draft is saved, when it is submitted and when it is approved (a period can be
  closed, or an account removed, in between): debit total = credit total in integer minor units; 2 to 999 lines (SAP's
  upload limit) and each line has exactly one positive side; the account code is in the chart of the version in use;
  the date is in a period that is not closed; no memo starts with `= + - @`, tab, CR, LF or their full-width forms (the
  posting file cannot alter values, so the formula characters are refused at the door). Every problem comes back with the
  rule, the line and the value; nothing is stored while one exists.
- Typed journals and a CSV import use the same function. The CSV columns are the item names of Yayoi's journal data
  (`伝票No.,取引日付,借方勘定科目,借方金額,貸方勘定科目,貸方金額,摘要`). Difference: Yayoi writes accounts by name,
  this tool by account code because its chart is the workbook's. One error anywhere refuses the whole file.
- A journal is a draft, then waiting, then approved or rejected. The author can withdraw a draft or a waiting journal. For a
  draft the tool made, the author is the tool's user (「照合の道具」, no role, no password that matches), so a person with the
  role 作成者 submits or withdraws it; approval is as for any journal and the approver is never the maker.
  Rejected and withdrawn journals stay in the table. The database refuses UPDATE and DELETE on an approved journal and
  its lines (`WHEN OLD.status = '承認済み'`, a different shape from the whole-table triggers of the log tables); a draft
  can still change status. A correction is a new, reversing journal made by hand.
- Group and row states: a draft made with a reason code puts a group into 仕訳の承認待ち and marks the row; approval makes the group 完了 and the
  row 処理済み; rejection or withdrawal gives the group back to 差額の処理待ち and the row stays 未照合. A draft the tool made
  for a pair inside the tolerance (ADR 0003 D6) leaves its group in 完了 (the pair is matched); the group is listed in the
  statement as an unprocessed difference until the journal is approved, and a rejection or withdrawal puts it back to
  差額の処理待ち. Groups in
  仕訳の承認待ち are fixed on a re-run like the other waiting states; a row with a journal leaves the remainder and the
  manual match. A row with an adjusting item **stays** in the remainder: its partner may arrive in a later period.

### The approval route and who may approve
- A sheet of amount ranges (lower bound inclusive, upper bound exclusive, the last upper bound empty; no gap, no
  overlap) lists the people who may approve a journal in each range. The amount is the **debit total**, as in Oracle Fusion's journal approval
  ("Maximum Amount Journal": *"Since each journal balances, the value in the Accounted Amount column represents the sum
  of all of the debits, as well as the sum of all of the credits for each journal"*). Sources:
  BlackLine's journal entry material - *"Dynamic workflow automatically routes JEs to the appropriate individuals for
  approval"*, *"approval process can be based off of amount thresholds"*, *"routing for approvals based on amount
  thresholds and roles"*. One level only (many levels are not built).
- To approve or reject a journal a person needs all three: the role 承認者, a name in the list of that amount's range,
  and not being the person who made the journal (checked with the user id, whatever roles that person has). Sources for
  the separation: the Japanese Financial Services Agency's internal control standard (duties of approving, recording and
  safeguarding given to different people) and a state education board's close policy (every journal entry reviewed and
  approved by someone independent of the preparer). Whether a name is a real user is checked at approval, because users
  are created apart from the workbook versions.
- A rejection needs a reason. The approval record keeps who, when, and the configuration version and range used.

### Adjusting items
- A maker creates one for an unmatched book row; **a different person with the role 承認者 approves it** before it
  enters the statement (**author's design decision**: BlackLine's material says adjusting items are created and moved to a
  reconciliation, not who approves them. Closest source: *"Role-based workflow approval process with built-in
  segregation of duties"* (BlackLine, Account Reconciliations). Taken from it: the person who prepares does not approve; the
  approval of an item follows the same rule as the approval of a manual match). Status: 承認待ち, 未解消, 解消, 却下.
- 解消 is set when the row has come into a group in 完了 or 差額の処理待ち, with that group and run; if that group is
  undone it goes back to 未解消. While 未解消 it appears in the statement of the period it was made in and of every later
  period (BlackLine: *"Address unidentified differences with reconciling items. BlackLine automatically ages all
  items"*). The period it was made in is the period of the row's date: Oracle Account Reconciliation gives every
  reconciling transaction an open date, *"The Open Date field specifies the date the transaction was, or should have
  been, opened in the source system."* (Hyperion Financial Close Management User's Guide, "Adding Transactions
  Manually"); the row's date is that date. The age is the days from that period's last day to today.

### The period-end statement
Shape: the "Bank Reconciliation Statement" (Exhibit 1) of the US Department of Justice, Office of Justice Programs.
The sheet was written under contract (it says so itself), so it is **not** called public domain; only its shape and its
example figures are used.

```
bank balance at the period end
  + deposits in the books, not yet at the bank        - payments in the books, not yet at the bank
= adjusted bank balance
book balance at the period end
  +/- movements of the bank the books do not have yet
= adjusted book balance
difference = adjusted bank - adjusted book = the unidentified difference (shown in red when it is not 0)
```
- **As of the period end E.** The OJP sheet defines the items: *"Deposits in transit— funds received and recorded in a
  company’s records that have not yet been processed by the bank"*, on *"a specified date"*. Matching runs over all rows without a period limit, so the statement treats a row dated
  after E as not yet existing: a book row dated on or before E whose bank partners are all dated after E is a timing
  item whatever the later run decided (evidence: the later pairing); an unmatched book row with an approved,
  unresolved adjusting item made in that period or earlier is one too; a bank row dated on or before E whose book
  partners are all dated after E is a bank movement the books booked later, counted with the **book side** of the pair
  (what the books will add; a difference of the pair belongs to its journal, which takes the bank row's date). The
  movements the books lack also include the approved journals dated on or before E on the deposit account, **except** a
  journal the books already hold (below). A pair counts as a pair in the states 完了, 差額の処理待ち and 仕訳の承認待ち
  (the last one is a 差額の処理待ち pair whose difference has a journal draft made with a reason code; it is not undone). Events after E can only
  turn an unexplained row into an explained one; they never change a figure.
- Unmatched rows without an approved item are listed as 未処理 and fall into the difference. So is the difference of a
  pair whose bank side is dated on or before E and which has no approved journal dated on or before E, and a row marked
  処理済み whose journal is dated after E (as of E nothing settled it).
- An adjusting item stays 解消 while the pair that settled it waits for the journal of its difference (仕訳の承認待ち);
  otherwise it would be counted again next to that pair.
- The balance at E is the opening balance of the bundle that holds E (else the last bundle before it) plus its rows dated
  on or before E. A CSV that also prints a balance per row is checked both ways; a disagreement makes the balance
  unknown. If no bundle of a data source touches the period, the statement says 残高が分からない and shows no difference.
- **OJP's printed figures do not add up.** Bank side: 100,000 + 20,000 - 21,000 = 99,000, as printed. Book side:
  97,600 + 2,050 - 790 = **98,860**, but the sheet prints 99,000. The tests use the calculated figures: adjusted bank
  99,000, adjusted books 98,860, unidentified difference 140. The sample company of this repository reaches a
  difference of 0 with figures fixed beforehand (`docs/validation/acceptance.md`).
- A match set takes part only if it says so; the deposit accounts of those sets must differ (two sets on one account
  would add a journal twice) and both data sources must have balances and one unit (multiplier).

### Balances in the import
Bundles keep an opening and a closing balance (Zengin header/trailer, camt OPAV/CLAV, and for CSV the first and last
row balance); rows keep a printed CSV balance. For data sources used for the statement the closing balance of one
bundle must equal the opening balance of the next of the same account (checked against both neighbours); otherwise the
file is refused with both figures. A file of the first release did this inside one file only.

### The posting file
- Columns are the technical field names of SAP S/4HANA's "Upload General Journal Entries" (F2548) in its one-row
  layout: `BUKRS,BLART,BLDAT,BUDAT,WAERS,XBLNR,BKTXT,HKONT,WRSOL,WRHAB`; lengths 4, 2, 16, 25, 10; at most 999 journals
  per file and 999 lines per journal. Company code and journal type come from the workbook's 説明 sheet. Only names and
  lengths are used (SAP's text is not copied).
- **Not verified:** the heading text of SAP's own template (it is only visible after downloading it; the help search and
  the public JSON endpoints did not give it), the date format SAP expects (written as `YYYYMMDD`, a guess), and
  nothing was uploaded to SAP.
- WAERS is JPY, so the file is refused unless the data sources have multiplier 1. BKTXT is the memo cut to 25
  characters, and the screen says which were cut. The first line of the file is a note with the environment name, and
  the environment is in the file name; making production the only place a file can be written is left to the feature
  that controls changes between environments.
- A journal is written once. The file bytes, their sha256, who and when go to append-only tables. An administrator's
  「外す」 (release) with a reason is the only way to write it again, and is itself a row.
- A ledger row imported later whose reference equals the number of a written journal (XBLNR) is the books' own copy: it is
  linked to the journal, gets the class 転記済み, leaves the remainder, and the statement stops adding that journal once
  the row is dated on or before E. The link is made when the ledger file is imported (and again by a match run), so the
  journal and its copy are never counted together. A ledger CSV must therefore carry XBLNR in its reference column.

## Alternatives considered

- **Booking a timing item as a journal.** Not done. A timing item is a whole transaction that crosses the period end, so
  it is carried on the bank side as an adjusting item; a difference, and a movement the bank has and the books lack, is
  put right by a journal. The OJP sheet separates these two groups (Reason codes, above).
- **An adjusting item only for a book-side row.** This is how the OJP sheet's items that adjust the bank balance read. No
  model was found for a journal on a book-side row, and the tool allows both (the author's decision above).
- **Adjusting items without approval.** The first design had none. A second person's approval was added so that the
  person who prepares an item cannot bring the difference to zero alone.
- **A role for every amount range.** The first design tied the route to amounts and roles. The sheet now names the
  people of each range and keeps the single role 承認者, so the tool stays at four roles. BlackLine's journal sheet speaks
  of routing to "the appropriate individuals".
- **Several approval levels.** Not built. Oracle's journal approval page, one of the pages looked at, does not mention
  levels (`docs/sources.md`).
- **A plain CSV as the posting file.** This was the fallback if SAP's layout could not be found. SAP's help gives the technical field names of the upload (F2548), so those are used; the heading text of the
  template could not be found and the file was not tried in SAP (The posting file, above).
- **Accounts by name in the journal CSV, as Yayoi writes them.** Not followed: the chart of accounts is the workbook's,
  so accounts are written by code.
- **Writing the posting file from either environment.** The first version did. Since ADR 0008 it is written from
  production only.

## Consequences

- A person sees, row by row, what makes the difference zero; nothing is hidden in a total.
- Every journal that reaches the file was made, submitted and approved by different people where required, validated
  three times, and cannot be altered afterwards.
- The statement is only as good as the matching of its rows: a group half before and half after E is left in the
  difference rather than guessed.
- The treatments are this project's reading of public descriptions, not a copy of how any product does it.
