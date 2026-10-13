"""Turn computed exceptions into a summary and supplier emails.

Two paths: Claude (default) or an offline template renderer, so the demo runs
without an API key. Both return the same shape:
    {"summary_markdown": str, "emails": [{"supplier", "subject", "body"}]}
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta

from src.exceptions import INTERNAL_ONLY, Flag

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
REPLY_WINDOW_BUSINESS_DAYS = 2


def reply_by_date(as_of: date, days: int = REPLY_WINDOW_BUSINESS_DAYS) -> date:
    """The reply deadline is computed here, not invented by the model."""
    d = as_of
    while days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d

SYSTEM_PROMPT = """You are a supply chain operations assistant drafting for a supply chain specialist.

Rules:
- Use ONLY the facts in the provided JSON. Do not invent quantities, dates, PO numbers, or commitments.
- Write every date as YYYY-MM-DD and every PO by its exact number (for example PO-24101).
- Lead with the highest-severity items and give each one a recommended action.
- Write one email per external supplier that has exceptions. Never send a supplier information about
  another supplier's POs. Skip the supplier named "Internal".
- STOCKOUT_RISK items are internal only: use them in the summary, but never put inventory levels,
  days of cover, or demand figures in a supplier email.
- Emails are polite and specific, and ask for a reply by the reply_by date in the JSON.
  Do not introduce any other deadline or date.
- Do not use numbered lists; use bullets.

Return JSON only, no code fences, in this shape:
{"summary_markdown": "...", "emails": [{"supplier": "...", "subject": "...", "body": "..."}]}"""


def _payload(flags: list[Flag], as_of: date) -> str:
    return json.dumps({
        "as_of": as_of.isoformat(),
        "reply_by": reply_by_date(as_of).isoformat(),
        "reply_window_business_days": REPLY_WINDOW_BUSINESS_DAYS,
        "exceptions": [f.to_dict() for f in flags],
    }, indent=2)


def draft_with_claude(flags: list[Flag], as_of: date, model: str = DEFAULT_MODEL) -> dict:
    import anthropic  # imported here so offline mode needs no SDK

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _payload(flags, as_of)}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


def draft_offline(flags: list[Flag], as_of: date) -> dict:
    lines = [f"# PO Exception Summary, {as_of.isoformat()}", ""]
    counts = defaultdict(int)
    for f in flags:
        counts[f.severity] += 1
    lines.append(f"{len(flags)} exceptions: {counts['high']} high, {counts['medium']} medium, "
                 f"{counts['low']} low.")
    for sev in ("high", "medium", "low"):
        group = [f for f in flags if f.severity == sev]
        if not group:
            continue
        lines += ["", f"## {sev.title()}"]
        for f in group:
            lines.append(f"- **{f.rule}**: {f.detail} Action: {f.recommended_action}")

    by_supplier = defaultdict(list)
    for f in flags:
        if f.supplier != "Internal" and f.po_number and f.rule not in INTERNAL_ONLY:
            by_supplier[f.supplier].append(f)
    emails = []
    for supplier, group in by_supplier.items():
        pos = sorted({f.po_number for f in group})
        bullets = "\n".join(f"- {f.detail}" for f in group)
        emails.append({
            "supplier": supplier,
            "subject": f"Action needed on {', '.join(pos)}",
            "body": (f"Hello {supplier} team,\n\nWe are reviewing open orders as of "
                     f"{as_of.isoformat()} and need your help on the following:\n\n{bullets}\n\n"
                     "Please reply with a confirmed date or plan for each item within two "
                     "business days.\n\nThank you,\nSupply Chain"),
        })
    return {"summary_markdown": "\n".join(lines), "emails": emails}