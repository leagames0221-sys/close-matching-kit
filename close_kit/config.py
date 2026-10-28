"""Fixed settings. Each value names its source (details in docs/sources.md). Values marked (author's decision) have no
source that was found; they are the author's design decisions (docs/sources.md, "Author's design decisions")."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VAR_DIR = REPO_ROOT / "var"
DEFAULT_DB = VAR_DIR / "close_kit.sqlite3"
DEMO_USERS_FILE = VAR_DIR / "demo_users.txt"

HOST = "127.0.0.1"
PORT = 8765  # (author's decision) the port only appears in the address
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
ALLOWED_ORIGINS = ["http://127.0.0.1:8765", "http://localhost:8765"]

ENV_SANDBOX = "sandbox"
ENV_PRODUCTION = "production"
ENVIRONMENTS = (ENV_SANDBOX, ENV_PRODUCTION)

SCHEMA_VERSION = "2"  # workbook schema version (see the 説明 sheet); version 1 workbooks are refused

# File size limits in bytes, checked while reading, before parsing (the OWASP File Upload Cheat Sheet asks for a size
# limit, also after decompression, but gives no number). An uploaded file and the unpacked total of an xlsx: the defaults
# of the Frappe framework (MIT; ERPNext's bank statement import takes its file through it), frappe/core/api/file.py,
# get_max_file_size() and get_max_extract_size(), each "or 25 * 1024 * 1024" (System Settings: "Maximum total size a ZIP
# archive is allowed to expand to when extracted."). Entries in an xlsx: Apache POI (Apache-2.0), ZipSecureFile.java,
# "DEFAULT_MAX_FILE_COUNT = 1000", refused when the count is larger. Details in docs/sources.md.
LIMIT_XLSX = 25 * 1024 * 1024
LIMIT_XLSX_UNZIPPED = 25 * 1024 * 1024
LIMIT_XLSX_ENTRIES = 1000
LIMIT_XML = 25 * 1024 * 1024
LIMIT_FIXED_OR_CSV = 25 * 1024 * 1024

# Login. Microsoft, "Account lockout threshold": "Windows security baselines recommend configuring a threshold of 10
# invalid sign-in attempts"; "Reset account lockout counter after": "Windows security baselines recommend configuring the
# Reset account lockout counter after policy setting to 15" (minutes). Here the failures of the last 15 minutes are
# counted, so a name is refused for at most 15 minutes ("Account lockout duration": "approximately 15 minutes").
# OWASP Session Management Cheat Sheet: for an office worker's full day "an appropriate absolute timeout range could be
# between 4 and 8 hours".
LOGIN_FAIL_LIMIT = 10
LOGIN_FAIL_WINDOW_SECONDS = 15 * 60
SESSION_SECONDS = 8 * 60 * 60

# OWASP Password Storage Cheat Sheet: one of the listed scrypt parameter sets (N, r, p).
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 2**28              # OpenSSL (providers/implementations/kdfs/scrypt.c) needs 128*r*(N+2) + 128*r*p bytes =
                                   # 134,220,800 for these N, r, p, just above 2^27; the next power of two is used (author's decision; no document gives a rounding)
SCRYPT_SALT_BYTES = 16             # Python hashlib.scrypt: "salt should be about 16 or more bytes"; NIST SP 800-132: at least 128 bits

ROLES = ("作成者", "承認者", "管理者", "監査人")

# ---- journals, tasks (a value that follows no source is marked as the author's decision)
JOURNAL_MAX_LINES = 999            # SAP "Upload General Journal Entries": at most 999 line items per entry
POSTING_MAX_JOURNALS = 999         # ... and at most 999 entries per file
# The range of "days from the period end" a task may use is a setting of the workbook (説明 sheet), as in Oracle
# Financial Close Management ("You can specify the number of days a template tracks before and after the Zero day.").
# These are the values used when the workbook leaves the two columns empty (author's decision; no model gives numbers).
TASK_OFFSET_MIN, TASK_OFFSET_MAX = -20, 40
SYSTEM_USER_NAME = "照合の道具"         # the user that stands as the maker of a journal the tool drafts by itself (cannot log in)
HOLIDAY_CSV = REPO_ROOT / "close_kit" / "data" / "syukujitsu.csv"
BKTXT_LENGTH = 25                  # SAP field BKTXT (document header text), 25 characters

# ---- change requests (version of the settings -> production). Sources in ADR 0008; the author's decision marks the rest.
CHANGE_URGENCY = "Scheduled"       # NIST SP 800-128 appendix E item 6; the other two values are not built
CR_DRAFT = "下書き"
CR_WAIT_PRE = "事前承認待ち"
CR_WAIT_TEST = "試験待ち"
CR_TESTED = "試験済み"             # waits for the approval of the result
CR_RESULT_OK = "結果の承認済み"
CR_APPLIED = "本番に適用済み"
CR_CONFIRMED = "確認済み"
CR_CANNOT_CONFIRM = "確認できない"
CR_REJECTED = "却下"
CR_WITHDRAWN = "取り下げ"
CR_STATES = (CR_DRAFT, CR_WAIT_PRE, CR_WAIT_TEST, CR_TESTED, CR_RESULT_OK, CR_APPLIED, CR_CONFIRMED,
             CR_CANNOT_CONFIRM, CR_REJECTED, CR_WITHDRAWN)
CR_CLOSED = (CR_CONFIRMED, CR_REJECTED, CR_WITHDRAWN)      # no further step; 確認できない is deliberately not closed
CR_ROW_LIMIT_SHOWN = 200           # (author's decision) rows listed of a changed classification (the count is always complete)
