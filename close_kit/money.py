"""How an amount is written for a person. Amounts are kept as integers of the smallest unit; the data source says how
many of them make one unit (`multiplier`: 1 for yen, 100 for a currency with cents). Every screen and every sentence
that a person reads goes through `fmt_amount`; the CSV files that machines read keep the plain integer."""
from __future__ import annotations

from typing import Any


def fmt_amount(minor: int | None, mult: int = 1) -> str:
    if minor is None:
        return ""
    digits = len(str(mult)) - 1
    sign = "-" if minor < 0 else ""
    q, r = divmod(abs(minor), mult)
    frac = f".{r:0{digits}d}" if digits else ""
    return f"{sign}{q:,}{frac}"


def display_multiplier(cfg: dict[str, Any]) -> int:
    """The multiplier of the screens of the period-end features: that of the left source of the first match set used
    for the balance reconciliation (the two sources of such a set have the same multiplier; the workbook is refused
    otherwise)."""
    for m in cfg.get("match_sets", []):
        if m.get("reconcile"):
            src = next((s for s in cfg["sources"] if s["name"] == m["left"]), None)
            if src:
                return src["multiplier"]
    return 1


def set_multipliers(cfg: dict[str, Any], match_set: str) -> tuple[int, int]:
    """(left, right) multiplier of the two sources of a match set; 1 for a source or a set the workbook does not have."""
    m = next((x for x in cfg.get("match_sets", []) if x["name"] == match_set), None)
    by_name = {s["name"]: s["multiplier"] for s in cfg.get("sources", [])}
    if m is None:
        return 1, 1
    return by_name.get(m["left"], 1), by_name.get(m["right"], 1)
