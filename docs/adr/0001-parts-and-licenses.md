# 0001. Parts, licenses and password hashing

Status: accepted (2026-10-24)

Format: Michael Nygard, "Documenting Architecture Decisions" (Title / Status / Context / Decision / Consequences),
https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions (CC0).

## Context

The tool must run on a laptop with no paid service, no card, no network call at run time and no LLM or statistical
model. Every added package is a supply-chain risk and a license question, so the list is short, pinned with hashes, and
each package was checked against PyPI metadata and the OSV database on 2026-10-24 (0 known vulnerabilities for all of
them; versions are from 2026-10-10 or earlier, except `certifi`, see below). The output of that check was not kept in
the repository. The same check was run again on 2026-10-26 (OSV `querybatch` with the 28 versions of
`requirements.lock.txt`, and the PyPI JSON of each version): no known vulnerability; the first upload of every version is
on 2026-10-10 or earlier except `certifi` 2026.10.19 (uploaded 2026-10-20). It can be repeated with those two public
endpoints; its output is not kept either.

## Decision

1. **Seven direct packages**: fastapi 0.143.0, uvicorn 0.54.0, jinja2 3.1.6, openpyxl 3.1.5, python-multipart 0.0.32,
   pytest 9.1.1, httpx 0.28.1. They pull in the other 21. All 28 are installed from `requirements.lock.txt`
   (`pip install --no-deps --require-hashes`), by the person who owns the machine; the code never installs anything.
2. **Installed set** (`pip list` equals the lock; `pip check` reports no broken requirements):

   | Package | Version | | Package | Version |
   |---|---|---|---|---|
   | annotated-doc | 0.0.5 | | jinja2 | 3.1.6 |
   | annotated-types | 0.8.0 | | markupsafe | 3.0.4 |
   | anyio | 4.15.1 | | openpyxl | 3.1.5 |
   | certifi | 2026.10.19 | | opentelemetry-api | 1.45.1 |
   | click | 8.5.0 | | packaging | 26.3 |
   | colorama | 0.4.6 | | pluggy | 1.6.0 |
   | et-xmlfile | 2.0.0 | | pydantic | 2.14.0 |
   | fastapi | 0.143.0 | | pydantic-core | 2.50.0 |
   | h11 | 0.16.0 | | pygments | 2.21.0 |
   | httpcore | 1.0.9 | | pytest | 9.1.1 |
   | httpx | 0.28.1 | | python-multipart | 0.0.32 |
   | idna | 3.20 | | starlette | 1.7.0 |
   | iniconfig | 2.3.1 | | typing-extensions | 4.16.0 |
   | typing-inspection | 0.4.4 | | uvicorn | 0.54.0 |

3. **License exceptions** (everything else is MIT, Apache-2.0 or BSD): `typing-extensions` is PSF-2.0 (a permissive
   license that only asks for the notice) and `certifi` is MPL-2.0 (file-level copyleft; it is used unmodified, so
   nothing has to be published). Both are required by FastAPI / httpx. `certifi` is 2026.10.19 because earlier versions
   contain a compromised certificate authority (GHSA-6q48-6c7w-9gw2).
4. **Converter environment**: `pyarrow` 26.0.0 (Apache-2.0) in a second virtual environment, used only by
   `tools/convert_splink.py` to turn parquet into CSV. The product code never imports it.
5. **Not used**: `bankstatementparser` (no support for the Zengin fixed-length format, a PDF path that switches to an
   LLM, pulls in pandas / lxml / pydantic, license metadata ("Apache-2.0 OR MIT") that disagrees with its PyPI classifier
   ("Other/Proprietary License"), ten releases uploaded to PyPI on one day, 2026-10-10). Zengin and camt.052 are read with the standard library.
6. **Password hashing**: `hashlib.scrypt(n=2**17, r=8, p=1, maxmem=2**28)`, 16-byte random salt, comparison with
   `hmac.compare_digest`. OWASP's Password Storage Cheat Sheet prefers Argon2id; it is not in the standard library, and
   scrypt with N=2^17, r=8, p=1 is one of the parameter sets the same sheet lists, so scrypt is the next best choice
   without adding a package.
7. **Sessions** are rows in the database; the cookie holds only a random 32-byte id (stored hashed), HttpOnly,
   SameSite=Strict. Login failures are limited to 10 per 15 minutes per name (Microsoft's Windows security baselines: a lockout
   threshold of 10, and the failure counter reset after 15 minutes; the advised lockout duration is also about 15 minutes), and a session lasts at most 8 hours (OWASP Session
   Management Cheat Sheet, absolute timeout for an office worker's day: 4 to 8 hours). Sources in `docs/sources.md`. The login form's CSRF and
   session-fixation handling are in ADR 0004.

8. **Second release: no new part.** The journals, the balance statement, the posting file and the task list use the
   same 28 packages and the standard library. The only new outside file is data, not code: the Cabinet Office's national
   holidays CSV (Public Data License 1.0), kept byte for byte (`close_kit/data/*.csv -text` in `.gitattributes`) with its
   sha256 in `docs/sources.md`.

## Alternatives considered

- **`bankstatementparser`, a package that reads bank statement files.** Looked at for reading camt.052 and Zengin; not
  used (item 5). Checked on PyPI on 2026-10-28: its README lists eight formats (CAMT, PAIN.001, CSV, OFX, QFX, MT940,
  MT942, BAI2) and PDFs, and does not list Zengin; its PDF route goes to a configurable LLM, and this tool uses no LLM at
  run time; its metadata requires pandas, lxml and pydantic. Zengin and
  camt.052 are read with the standard library (ADR 0004).
- **Argon2id for the password hash.** OWASP's Password Storage Cheat Sheet names it first, and names scrypt for the case
  that Argon2id is not available. It is not in the standard library, and a package would add to the supply-chain and
  license list (Context), so scrypt from `hashlib` is used (item 6). The sheet also lists bcrypt and PBKDF2; they were not
  compared.
- **5 failed logins per 15 minutes.** Not chosen: 10 failures with a reset after 15 minutes are the values in
  Microsoft's Windows security baselines (item 7).
- **An older `certifi`.** The advisory GHSA-6q48-6c7w-9gw2 says that versions before 2026.10.18 include a root
  certificate whose intermediate certificates have compromised keys, and asks users to upgrade to 2026.10.19 or later.
  The lock holds 2026.10.19 (item 3).
- **No other option was compared** for the web framework (FastAPI), the database (SQLite), how sessions are stored, or
  the reader for the Splink parquet files (pyarrow). The records name only these.

## Consequences

- Installing is a deliberate step by a person; there is no auto-update. The lock needs a re-check when a package is
  bumped.
- Two licenses outside MIT / Apache / BSD are accepted, and the README / THIRD_PARTY_NOTICES say so.
- No Argon2id; if a package for it is ever allowed, the hash column already stores the algorithm name and parameters,
  so old hashes still verify.
