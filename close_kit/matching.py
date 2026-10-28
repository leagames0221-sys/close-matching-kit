"""Matching engine. Pure functions over in-memory rows; no database access.

Terms follow docs/adr/0003. Pass rules are applied in the order of their "順番" column. What a rule does
with a pair of rows (1対1):

  full match      key fields equal, |date difference| <= date_window, |amount difference| <= tolerance
                  -> 自動. State 完了 when the amount difference is 0; when it is not, 完了 if the rule names an account
                  for the difference ("差額の相手勘定": the caller then drafts the journal), else 差額の処理待ち.
  partial match   key fields equal (a key field other than 金額 / 勘定日 must be in the rule), but the amount
                  is outside the tolerance (Reconify: amount_diff) or the date is outside the window
                  (Reconify: timing_diff) -> 候補 (確認待ち).
  several partners the rows of a connected set of full matches contain more than one row on a side
                  (Oracle: Ambiguous; NetSuite: "can't choose one") -> one 候補 for the whole set, never 自動.

Bundles (1対多, 多対1, 多対多): the amounts of the two bundles must be equal (Oracle: no amount tolerance outside
one to one); the dates are compared (through each bundle's latest date) only when 勘定日 is one of the bundle keys
(Oracle: date is a matching criterion only after it is selected as a grouping attribute).

A candidate keeps its rows: a row of an earlier rule's candidate is not used by a later rule. This is the author's
decision; Odoo's page says only "the first one in the sequence of models is applied" (which model applies, not which
rows a suggestion holds). Rows whose non-empty reference is shared with another row of the
same data source are duplicates (Reconify `flag`): they take part in every rule like any other row, the caller shows
a duplicate mark, and if nothing takes them they stay in the class 重複.
"""
from __future__ import annotations

import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

CANDIDATE_MAX_ROWS = 20  # (author's decision) a set of possible partners larger than this is not turned into one candidate

KLASS_AUTO, KLASS_CANDIDATE, KLASS_UNMATCHED, KLASS_DUP = "自動", "候補", "未照合", "重複"
STATE_DONE, STATE_DIFF, STATE_REVIEW = "完了", "差額の処理待ち", "確認待ち"
IDENT_FIELDS = ("参照", "摘要")  # fields other than amount / date: partial matches need one of them


def norm_text(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).split())


@dataclass(frozen=True)
class Row:
    id: int
    side: str       # 'L' or 'R'
    date: str       # ISO yyyy-mm-dd
    day: int        # proleptic ordinal of the date
    amount: int     # minor units
    ref: str
    memo: str

    @staticmethod
    def make(id: int, side: str, iso_date: str, amount: int, ref: str, memo: str) -> "Row":
        return Row(id, side, iso_date, date.fromisoformat(iso_date).toordinal(), amount,
                   norm_text(ref), norm_text(memo))


GETTERS: dict[str, Callable[[Row], Any]] = {
    "参照": lambda r: r.ref, "摘要": lambda r: r.memo, "金額": lambda r: r.amount, "勘定日": lambda r: r.date,
}


@dataclass
class Group:
    kind: str                 # '自動' or '候補'
    state: str
    reason: str               # '' | amount_diff | timing_diff | ambiguous | 候補ルール
    rule_order: int
    rule_name: str
    left: list[int]
    right: list[int]
    left_total: int
    right_total: int
    diff_account: str = ""    # the rule's 差額の相手勘定 (only on a 自動 group with a difference)

    @property
    def diff(self) -> int:
        return self.left_total - self.right_total


@dataclass
class EngineResult:
    groups: list[Group]
    klass: dict[int, str]
    dup_ids: set[int]
    skipped_bundles: list[str] = field(default_factory=list)


def duplicate_ids(rows: Iterable[Row], fixed_refs: Counter | None = None) -> set[int]:
    cnt: Counter[str] = Counter(fixed_refs or {})
    rows = list(rows)
    for r in rows:
        if r.ref:
            cnt[r.ref] += 1
    return {r.id for r in rows if r.ref and cnt[r.ref] >= 2}


class _Engine:
    def __init__(self, lefts: list[Row], rights: list[Row], rules: list[dict],
                 fixed_left_refs: Counter | None, fixed_right_refs: Counter | None):
        self.lefts, self.rights = lefts, rights
        self.rules = sorted((r for r in rules if r.get("enabled", True)), key=lambda r: r["order"])
        self.by_id: dict[int, Row] = {r.id: r for r in lefts + rights}
        self.dups = duplicate_ids(lefts, fixed_left_refs) | duplicate_ids(rights, fixed_right_refs)
        self.taken: set[int] = set()
        self.auto: list[Group] = []
        self.cands: dict[int, Group] = {}      # row id -> its candidate (every row of a candidate points to it)
        self.cand_list: list[Group] = []
        self.skipped_bundles: list[str] = []

    # ---- helpers ------------------------------------------------------------------------
    def _free(self, rows: list[Row]) -> list[Row]:
        """Rows no rule has taken and no earlier candidate holds (the earlier rule's candidate stays)."""
        return [r for r in rows if r.id not in self.taken and r.id not in self.cands]

    def _make_auto(self, rule: dict, ls: list[Row], rs: list[Row]) -> None:
        ids = [r.id for r in ls + rs]
        self.taken.update(ids)
        lt, rt = sum(r.amount for r in ls), sum(r.amount for r in rs)
        acct = rule.get("diff_account") or ""
        state = STATE_DONE if lt == rt or acct else STATE_DIFF
        self.auto.append(Group(KLASS_AUTO, state, "", rule["order"], rule["name"],
                               sorted(r.id for r in ls), sorted(r.id for r in rs), lt, rt,
                               diff_account=acct if lt != rt else ""))

    def _make_candidate(self, rule: dict, ls: list[Row], rs: list[Row], reason: str) -> bool:
        ids = [r.id for r in ls + rs]
        if any(i in self.cands or i in self.taken for i in ids):
            return False  # an earlier candidate wins
        if len(ids) > CANDIDATE_MAX_ROWS:
            return False
        g = Group(KLASS_CANDIDATE, STATE_REVIEW, reason, rule["order"], rule["name"],
                  sorted(r.id for r in ls), sorted(r.id for r in rs),
                  sum(r.amount for r in ls), sum(r.amount for r in rs))
        self.cand_list.append(g)
        for i in ids:
            self.cands[i] = g
        return True

    # ---- 1対1 ---------------------------------------------------------------------------
    def _rule_one_to_one(self, rule: dict) -> None:
        is_cand_rule = rule["kind"] == "候補"
        ls = self._free(self.lefts)
        rs = self._free(self.rights)
        tol, window = rule["amount_tolerance_minor"], rule["date_window"]
        ident = [(a, b) for a, b in rule["keys"]
                 if (a, b) not in (("金額", "金額"), ("勘定日", "勘定日"))]
        edges: list[tuple[Row, Row, str]] = []

        def classify(l: Row, r: Row) -> str | None:
            amount_ok = abs(l.amount - r.amount) <= tol
            date_ok = abs(l.day - r.day) <= window
            if amount_ok and date_ok:
                return "full"
            if is_cand_rule or not ident:
                return None
            if date_ok:
                return "amount_diff"
            if amount_ok:
                return "timing_diff"
            return None

        if ident:
            index: dict[tuple, list[Row]] = defaultdict(list)
            for r in rs:
                key = tuple(GETTERS[b](r) for _, b in ident)
                if all(v not in ("", None) for v in key):
                    index[key].append(r)
            for l in ls:
                key = tuple(GETTERS[a](l) for a, _ in ident)
                if any(v in ("", None) for v in key):
                    continue
                for r in index.get(key, ()):
                    kind = classify(l, r)
                    if kind:
                        edges.append((l, r, kind))
        else:
            srt = sorted(rs, key=lambda r: r.amount)
            amounts = [r.amount for r in srt]
            for l in ls:
                lo, hi = bisect_left(amounts, l.amount - tol), bisect_right(amounts, l.amount + tol)
                for r in srt[lo:hi]:
                    kind = classify(l, r)
                    if kind:
                        edges.append((l, r, kind))
        self._apply_components(rule, edges, is_cand_rule)

    def _apply_components(self, rule: dict, edges: list[tuple[Row, Row, str]], is_cand_rule: bool) -> None:
        parent: dict[int, int] = {}

        def find(x: int) -> int:
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for l, r, _ in edges:
            parent[find(l.id)] = find(r.id)
        comps: dict[int, dict[str, Any]] = {}
        for l, r, kind in edges:
            c = comps.setdefault(find(l.id), {"L": {}, "R": {}, "kinds": []})
            c["L"][l.id] = l
            c["R"][r.id] = r
            c["kinds"].append(kind)
        ordered = sorted(comps.values(), key=lambda c: (min(r.day for r in c["L"].values()), min(c["L"])))
        later: list[dict[str, Any]] = []
        for c in ordered:
            ls, rs = sorted(c["L"].values(), key=lambda r: r.id), sorted(c["R"].values(), key=lambda r: r.id)
            if is_cand_rule:
                later.append(c)
            elif len(ls) == 1 and len(rs) == 1 and c["kinds"] == ["full"]:
                self._make_auto(rule, ls, rs)
            else:
                later.append(c)
        for c in later:
            ls, rs = sorted(c["L"].values(), key=lambda r: r.id), sorted(c["R"].values(), key=lambda r: r.id)
            if is_cand_rule:
                reason = "候補ルール"
            elif len(ls) == 1 and len(rs) == 1:
                reason = [k for k in c["kinds"] if k != "full"][0]
            else:
                reason = "ambiguous"
            self._make_candidate(rule, ls, rs, reason)

    # ---- bundles -----------------------------------------------------------------------------
    def _bundles(self, rows: list[Row], keys: list[str], pk: str, limit: int, label: str
                 ) -> dict[Any, list[list[Row]]]:
        groups: dict[tuple, list[Row]] = defaultdict(list)
        for r in rows:
            if keys:
                key = tuple(GETTERS[k](r) for k in keys)
                if any(v in ("", None) for v in key):
                    continue
            else:
                key = (r.id,)
            groups[key].append(r)
        out: dict[Any, list[list[Row]]] = defaultdict(list)
        for key, rs in groups.items():
            if len(rs) > limit:
                self.skipped_bundles.append(f"{label}の束 {key} は {len(rs)} 行で上限 {limit} を超えるため作らない")
                continue
            vals = {GETTERS[pk](r) for r in rs}
            if len(vals) != 1:
                continue
            v = vals.pop()
            if v in ("", None):
                continue
            out[v].append(rs)
        return out

    def _rule_bundle(self, rule: dict) -> None:
        is_cand_rule = rule["kind"] == "候補"
        ls = self._free(self.lefts)
        rs = self._free(self.rights)
        pk, limit = rule["pair_key"], rule["bundle_limit"]
        lb = self._bundles(ls, rule["left_bundle"], pk, limit, "左")
        rb = self._bundles(rs, rule["right_bundle"], pk, limit, "右")
        window = rule["date_window"]
        # Oracle: no amount tolerance outside one to one (so the totals must be equal), and the date is compared only
        # when it is one of the grouping attributes
        date_grouped = "勘定日" in rule["left_bundle"] or "勘定日" in rule["right_bundle"]
        pairs = []
        for v in lb.keys() & rb.keys():
            if len(lb[v]) != 1 or len(rb[v]) != 1:
                continue  # more than one bundle with the same pairing value: not decided here
            pairs.append((lb[v][0], rb[v][0]))
        pairs.sort(key=lambda p: (min(r.day for r in p[0]), min(r.id for r in p[0])))
        for lrows, rrows in pairs:
            lt, rt = sum(r.amount for r in lrows), sum(r.amount for r in rrows)
            amount_ok = lt == rt
            date_ok = (not date_grouped) or abs(max(r.day for r in lrows) - max(r.day for r in rrows)) <= window
            if is_cand_rule:
                if amount_ok and date_ok:  # a 候補 rule is hit only on equal totals and, if grouped by date, inside the window
                    self._make_candidate(rule, lrows, rrows, "候補ルール")
            elif amount_ok and date_ok:
                self._make_auto(rule, lrows, rrows)
            elif pk in IDENT_FIELDS and (amount_ok or date_ok):
                self._make_candidate(rule, lrows, rrows, "amount_diff" if date_ok else "timing_diff")

    # ---- run --------------------------------------------------------------------------------
    def run(self) -> EngineResult:
        for rule in self.rules:
            if rule["type"] == "1対1":
                self._rule_one_to_one(rule)
            else:
                self._rule_bundle(rule)
        klass: dict[int, str] = {}
        for rid in self.by_id:
            if rid in self.taken:
                klass[rid] = KLASS_AUTO
            elif rid in self.cands:
                klass[rid] = KLASS_CANDIDATE
            elif rid in self.dups:
                klass[rid] = KLASS_DUP
            else:
                klass[rid] = KLASS_UNMATCHED
        return EngineResult(groups=self.auto + self.cand_list, klass=klass, dup_ids=self.dups,
                            skipped_bundles=self.skipped_bundles)


def run(lefts: list[Row], rights: list[Row], rules: list[dict], *,
        fixed_left_refs: Counter | None = None, fixed_right_refs: Counter | None = None) -> EngineResult:
    return _Engine(lefts, rights, rules, fixed_left_refs, fixed_right_refs).run()
