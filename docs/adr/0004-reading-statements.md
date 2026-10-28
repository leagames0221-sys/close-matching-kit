# 0004. How statements are read, and how the login form is protected

Status: accepted (2026-10-24)

## Context

Three input shapes must become one standard row: data source, account, booking date, amount (integer, smallest unit),
direction, reference, memo, original row number. Bank files are the place where a wrong figure does the most harm, so
every file is checked against its own totals before anything is stored, and a file that does not add up is refused
as a whole with the figure that differs.

## Decision

### Zengin fixed length (全銀協 deposit/withdrawal statement)
- Item names, widths and meanings come from the statement-inquiry manual of a trust bank (no license stated, so only
  positions and meanings are used, no sample values): `close_kit/formats/zengin_layout.py`. A test checks that every
  record layout adds up to 200 bytes.
- The file is cut by byte position first; each text field is then decoded as cp932. Records are 200 bytes, or 200 bytes
  plus CR LF (202). EBCDIC is **not** accepted (the manual's character code is 「JISコードまたはEBCDICコード」; a code-division other than
  JIS is refused).
- The calendar (西暦 / 令和) is not in the file, so the data source sheet says it.
- Per account block (header to trailer): opening balance + credits - debits = closing balance, the trailer's counts
  and totals, and the end record's total record count and account count. The overdraft flag is the sign. A block that
  does not agree is refused with the figure; the refusal is for the whole file (a file normally has one block for the
  data source's account, other accounts' blocks are skipped and reported).
- The inquiry number is blank in the files, so it is not used as a row key. The reference is the EDI field.

### camt.052.001.02 (XML)
- Element names from the bank's field table: Rpt, Acct/Id/Othr/Id, FrToDt, Bal (OPAV / CLAV with CdtDbtInd), Ntry
  (Amt, CdtDbtInd, BookgDt, NtryDtls/TxDtls/Refs/Prtry/Ref). Row key = account + booking date + that reference number.
- Check: OPAV + credits - debits = CLAV; entry count and the credit / debit counts and sums of the summary.
- **A report with entries and a balance of 0 at both ends is refused** ("cannot be verified; possibly downloaded by a
  user whose output is restricted"): the manual says balances are written as 0 for such users. The price is that a
  real account with zero balance at both ends and equal credits and debits is refused as well.
- Reversal entries (`RvslInd`) are refused: the manual does not let us decide the sign.
- DOCTYPE and ENTITY are refused before the parser sees the bytes (entity-expansion attacks); only UTF-8 is accepted.

### CSV
- Columns come from the data source sheet. UTF-8 (with or without BOM) or Shift_JIS. Thousands separators, bracketed
  negatives `(320.00)`, `▲`, a leading `¥` are accepted (Reconify's eval 010 reads bracketed amounts as negative).
- A balance column is checked (previous balance + amount = balance) only when the sheet maps one. With a balance column
  the bundle also has an opening balance (first row's balance minus its amount) and a closing balance (last row's), and
  every row keeps its printed balance.

### Balances and the next bundle (second release)
- Every bundle that has balances stores them in minor units: Zengin header and trailer, camt OPAV and CLAV, CSV as above.
  For a data source that takes part in a period-end statement, a new bundle must start where the previous bundle of the
  same account ended and end where the next one starts; otherwise the file is refused with both figures. The first release
  checked only the inside of one file. A data source that is not used for the statement is not chained.

### Amounts and the direction column (D2)
- Amounts are integers in the smallest unit: `Decimal` x multiplier; a fraction of a unit refuses the row. This is
  Reconify's idea: *"Amounts are stored as integer minor units: with `multiplier: 100`, `1500.00` becomes `150000`."*
- The direction column has exactly two values: 入金を + にする / 帳簿の借方を + にする. A third value (money in is
  minus) was considered and **rejected**: Reconify has no sign-inversion option, signs come from the file, and the
  README only normalises amounts. Zengin and camt.052 take the sign from the file's own credit / debit flag, so the
  workbook accepts only 入金を + にする for them.

### Import is refused as a whole file when
the balance or a total does not agree; the date range (from..to) overlaps a batch already imported for the same data
source and account; the file's sha256 was imported before; or a row key was imported before. File sizes are checked
while reading and an upload's name is never used as a path. The OWASP File Upload Cheat Sheet asks for a limit but gives
no figure; the figures follow two open source projects (details in `docs/sources.md`):
- every uploaded file (xlsx, XML, Zengin, CSV) at most 25 MB, and an xlsx at most 25 MB when unpacked (the sizes the zip
  directory declares): the defaults of the Frappe framework (MIT), on which ERPNext's bank statement import takes its
  file - `frappe/core/api/file.py`, `get_max_file_size()` and `get_max_extract_size()`, each ending in
  `or 25 * 1024 * 1024`; the setting of the second is described as *"Maximum total size a ZIP archive is allowed to
  expand to when extracted."*;
- an xlsx with more than 1,000 entries is refused: Apache POI (Apache-2.0), `ZipSecureFile.java`,
  `DEFAULT_MAX_FILE_COUNT = 1000`, and `ZipPackage.java` refuses `list.size() > ZipSecureFile.getMaxFileCount()` with the
  message *"This file embeds more internal file entries than expected."*

### D6. Login form: pre-login session, CSRF token, and a new session id after login (accepted)
Source (OWASP Cross-Site Request Forgery Prevention Cheat Sheet, "Possible CSRF Vulnerabilities in Login Forms"):
*"Login CSRF can be mitigated by creating pre-sessions (sessions before a user is authenticated) and including tokens
in login form. ... Remember that pre-sessions cannot be transitioned to real sessions once the user is authenticated -
the session should be destroyed and a new one should be made to avoid session fixation attacks."*
- `GET /login` creates a pre-login session (no user) and puts its token in the form.
- `POST /login` checks Origin and the token, authenticates, **deletes the pre-login session and issues a new session
  id**. A test shows the old cookie id is no longer valid after login and the new id differs.
- Every other POST checks the Origin header (only `http://127.0.0.1:8765` or `http://localhost:8765`; a missing Origin
  is refused), the hidden token of the server-side session, and the role on the server. Host is limited to the same
  two names (Starlette's TrustedHostMiddleware). The cookie is HttpOnly and SameSite=Strict.

## Alternatives considered

- **A ready-made statement reader.** `bankstatementparser` was looked at and not used; its README does not list the
  Zengin format (ADR 0001, item 5).
- **Accepting EBCDIC in Zengin files.** The trust bank's manual allows JIS or EBCDIC as the character code. EBCDIC is
  refused. The records give no reason other than this decision; the Consequences say that a legitimate file may be
  refused, and the message says why.
- **Accepting a camt.052 report whose balances are 0 at both ends.** Refused: the bank's manual says the balances are
  written as 0 for a user whose output is restricted, so the check cannot be made. The drawback is stated above.
- **Accepting a reversal entry (`RvslInd`) with an assumed sign.** Refused: the manual does not let us decide the sign.
- **A third value for the direction column ("money in is minus").** Considered and rejected (D2 above).
- **Upload limits of our own.** OWASP's File Upload Cheat Sheet asks for a limit but gives no figure, so the figures of
  the Frappe framework and Apache POI are used.
- **Other protections of the login form against CSRF.** No other option was compared; D6 follows the cheat sheet's
  passage on login forms.

## Consequences

- A bank file that does not add up never reaches the matching, but the person gets the reason in words.
- The zero-balance camt rule and the EBCDIC refusal may refuse legitimate files; both say why in the message.
- The sample files are built by `tools/sample_builders.py` from the item tables, with made-up values.
