"""PO exception assistant.

    python run.py --offline                 # template drafts, no API key needed
    python run.py                           # Claude drafts (needs ANTHROPIC_API_KEY)
    python run.py --as-of 2026-10-20        # rerun the rules for another date
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from src.drafting import REPLY_WINDOW_BUSINESS_DAYS, draft_offline, draft_with_claude, reply_by_date
from src.exceptions import load_data, run_all
from src.validate import validate_draft

ROOT = Path(__file__).parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 13))
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs")
    ap.add_argument("--offline", action="store_true", help="skip Claude and use templates")
    args = ap.parse_args()

    pos, inv = load_data(args.data)
    flags = run_all(pos, inv, args.as_of)
    args.out.mkdir(exist_ok=True)
    pd.DataFrame([f.to_dict() for f in flags]).to_csv(args.out / "exceptions.csv", index=False)
    print(f"{len(flags)} exceptions found as of {args.as_of}")

    draft = draft_offline(flags, args.as_of) if args.offline else draft_with_claude(flags, args.as_of)
    issues = validate_draft(
        draft, flags, args.as_of.isoformat(),
        extra_dates={reply_by_date(args.as_of).isoformat()},
        extra_nums={str(REPLY_WINDOW_BUSINESS_DAYS)},
    )

    (args.out / "summary.md").write_text(draft["summary_markdown"] + "\n")
    emails_md = "\n\n---\n\n".join(
        f"**To:** {e['supplier']}  \n**Subject:** {e['subject']}\n\n{e['body']}" for e in draft["emails"])
    (args.out / "supplier_emails.md").write_text(emails_md + "\n")
    (args.out / "validation.json").write_text(json.dumps({"passed": not issues, "issues": issues}, indent=2))

    if issues:
        print("VALIDATION FAILED: review before sending")
        for i in issues:
            print("  " + i)
        return 1
    print("Validation passed: every PO, date, and figure traces to source data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())