"""Check drafted text against the source data before anyone acts on it.

Catches the failures that matter in supplier communication:
  - a PO number that does not exist, or belongs to a different supplier
  - a date that does not appear anywhere in the source facts
  - a quantity or figure that does not appear in the source facts
"""
from __future__ import annotations

import re

from src.exceptions import INTERNAL_ONLY, Flag

PO_RE = re.compile(r"\bPO-\d{5}\b")
SKU_RE = re.compile(r"\bSKU-\d+\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUM_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
LIST_MARKER_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")


def _norm(n: str) -> str:
    n = n.replace(",", "")
    return str(float(n)).rstrip("0").rstrip(".")


def allowed_facts(flags: list[Flag], as_of_iso: str) -> dict:
    pos, dates, nums = set(), {as_of_iso}, set()
    for f in flags:
        if f.po_number:
            pos.add(f.po_number)
        text = f"{f.detail} {f.recommended_action}"
        dates.update(DATE_RE.findall(text))
        cleaned = DATE_RE.sub(" ", PO_RE.sub(" ", SKU_RE.sub(" ", text)))
        nums.update(_norm(n) for n in NUM_RE.findall(cleaned))
        nums.update(_norm(str(v)) for v in f.metrics.values())
    return {"pos": pos, "dates": dates, "nums": nums}


def check(text: str, facts: dict, label: str) -> list[str]:
    issues = []
    for po in sorted(set(PO_RE.findall(text)) - facts["pos"]):
        issues.append(f"[{label}] PO not in this scope's source data: {po}")
    for d in sorted(set(DATE_RE.findall(text)) - facts["dates"]):
        issues.append(f"[{label}] date not found in source facts: {d}")
    cleaned = DATE_RE.sub(" ", PO_RE.sub(" ", SKU_RE.sub(" ", LIST_MARKER_RE.sub("", text))))
    seen = set()
    for m in NUM_RE.finditer(cleaned):
        n = _norm(m.group())
        if n in facts["nums"] or n in seen:
            continue
        seen.add(n)
        context = " ".join(cleaned[max(0, m.start() - 50): m.end() + 50].split())
        issues.append(f'[{label}] figure not found in source facts: {n}  (context: "...{context}...")')
    return issues


def validate_draft(draft: dict, flags: list[Flag], as_of_iso: str,
                   extra_dates: set[str] = frozenset(), extra_nums: set[str] = frozenset()) -> list[str]:
    """extra_dates / extra_nums: facts computed in code but not tied to one flag (e.g. reply-by date)."""
    def facts_for(scope):
        f = allowed_facts(scope, as_of_iso)
        f["dates"] |= set(extra_dates)
        f["nums"] |= {_norm(n) for n in extra_nums}
        return f

    summary_facts = facts_for(flags)
    # Counts the summary may legitimately state: total and per-severity tallies.
    summary_facts["nums"].add(_norm(str(len(flags))))
    for sev in ("high", "medium", "low"):
        summary_facts["nums"].add(_norm(str(sum(f.severity == sev for f in flags))))
    issues = check(draft["summary_markdown"], summary_facts, "summary")
    for email in draft["emails"]:
        # Suppliers only ever see their own PO-level facts, never internal inventory.
        scoped = [f for f in flags if f.supplier == email["supplier"] and f.rule not in INTERNAL_ONLY]
        if not scoped:
            issues.append(f"[email] unknown supplier: {email['supplier']}")
            continue
        facts = facts_for(scoped)
        label = f"email to {email['supplier']}"
        issues += check(email["subject"] + "\n" + email["body"], facts, label)
    return issues