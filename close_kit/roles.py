"""Operation x role table (section 4 of the design). Checked on the server for every call."""
from __future__ import annotations

from dataclasses import dataclass

from . import audit

# operation -> roles allowed. BlackLine's buyer's guide has the checklist line "Segregation of duties (Preparer,
# Approver, Reviewer)" - three roles - and "Proper administration roles with segregation of functionality". The four
# roles here (作成者, 承認者, 管理者, 監査人) and which role may do which operation are the author's design decision;
# the guide does not name these four roles or the operations.
PERMISSIONS: dict[str, frozenset[str]] = {
    "workbook_load": frozenset({"管理者"}),
    "ingest": frozenset({"作成者", "管理者"}),
    "match_run": frozenset({"作成者", "管理者"}),
    "manual_match": frozenset({"作成者"}),
    "manual_decide": frozenset({"承認者"}),  # approve or reject
    "view": frozenset({"作成者", "承認者", "管理者", "監査人"}),
    "audit_csv": frozenset({"管理者", "監査人"}),
}

# Operations of the journal / reconciliation / task features. Kept apart from PERMISSIONS above so that the table of
# the first release is still exactly that table (its test compares it cell by cell).
PERMISSIONS_CLOSE: dict[str, frozenset[str]] = {
    "reason_apply": frozenset({"作成者"}),      # attach a reason code (journal draft or adjusting item)
    "journal_create": frozenset({"作成者"}),    # enter / import / submit / withdraw a journal
    "journal_decide": frozenset({"承認者"}),    # approve or reject a journal (also: named in the route, not the author)
    "adjust_decide": frozenset({"承認者"}),     # approve or reject an adjusting item (not the author)
    "posting_export": frozenset({"管理者"}),    # write the posting file, release a journal for a second output
    "task_generate": frozenset({"管理者"}),     # create the tasks of a period
    "task_prepare": frozenset({"作成者"}),      # 作成完了 (and only the preparer of that task)
    "task_review": frozenset({"承認者"}),       # 完了 / 差し戻し (and only the reviewer of that task)
}
# Operations of the change requests (settings version -> production). Kept apart again so that the two tables above stay
# exactly as they were. "Being a person other than the requester" is a separate check in `changes.py`.
PERMISSIONS_CHANGE: dict[str, frozenset[str]] = {
    "change_create": frozenset({"管理者"}),         # make a request or a rollback request, put it forward
    "change_withdraw": frozenset({"管理者"}),       # (and only the requester, before it is applied)
    "change_pre_decide": frozenset({"管理者"}),     # approve / reject before the test (not the requester)
    "change_retest": frozenset({"管理者"}),         # run the test again (this voids the earlier approvals)
    "change_result_decide": frozenset({"承認者", "管理者"}),   # approve / reject the result (not the requester)
    "change_apply": frozenset({"管理者"}),          # put the version into production (not the requester)
    "change_confirm": frozenset({"管理者"}),        # confirm / cannot confirm (and only the requester)
}
ALL_PERMISSIONS: dict[str, frozenset[str]] = {**PERMISSIONS, **PERMISSIONS_CLOSE, **PERMISSIONS_CHANGE}


class PermissionDenied(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: int
    name: str
    roles: frozenset[str]


def allowed(user: User, op: str) -> bool:
    return bool(ALL_PERMISSIONS[op] & user.roles)


def require(conn, user: User, op: str, *, env: str | None = None,
            version_id: int | None = None) -> None:
    """Raise PermissionDenied (and write the refusal to the log) unless some role of the user allows op."""
    if not allowed(user, op):
        audit.log(conn, user_id=user.id, user_name=user.name, action=op,
                  outcome=audit.OUTCOME_DENIED, env=env, version_id=version_id,
                  detail=f"役割 {','.join(sorted(user.roles)) or '(なし)'} には許可されていない操作")
        raise PermissionDenied(op)
