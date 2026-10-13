# PO Exception Assistant

A small, working example of an AI-assisted supply chain workflow: it reads purchase order and
inventory data, flags exceptions before they become stockouts or penalties, and drafts a
decision-ready summary plus one email per supplier. Then it checks every PO number, date, and
quantity in the draft against the source data before anything is sent.

The data is synthetic: a fictional supplement brand with a co-manufacturer, two packaging
suppliers, and one container sitting at port.

## The design principle

**Python decides what is true. Claude decides how to say it. Validation proves they agree.**

| Step | Who does it | Why |
|---|---|---|
| Detect exceptions | Python rules (`src/exceptions.py`) | Dates, slips, and shortfalls must be exact and repeatable |
| Draft summary and emails | Claude (`src/drafting.py`) | Turning ten flags into clear, prioritized writing is where AI saves real time |
| Check the draft | Python (`src/validate.py`) | An email that cites the wrong PO or an invented date is worse than no email |

## What it flags

| Rule | Trigger | Recommended action |
|---|---|---|
| `LATE_VS_PROMISE` | Latest ETA is past the promised date | Get a committed recovery date; consider a partial ship or expedite |
| `UNCONFIRMED_ORDER` | Open PO unacknowledged after 3 days | Request a written acknowledgment with a ship date |
| `SHORT_RECEIPT` | Received quantity more than 0.5% under ordered | Open a shortage claim; confirm whether the balance ships or is credited |
| `DEMURRAGE_RISK` | Container at port with 2 or fewer free days left | Book drayage now; ask for a free-time extension |
| `STOCKOUT_RISK` | Days of cover run out before the next inbound arrives | Pull in the PO, split-ship, or place a replenishment order |

Stockout risk is **internal only**. It appears in the team summary but never in a supplier
email, and the validator fails any draft that leaks on-hand or demand figures to a supplier.

## What the validator catches

- A PO number that does not exist, or that belongs to a different supplier
- A date that appears nowhere in the source facts
- A quantity or figure that appears nowhere in the source facts
- Internal inventory figures in a supplier email

If any check fails, the run exits non-zero and writes the issues to `outputs/validation.json`.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python run.py --offline          # template drafts, no API key needed
export ANTHROPIC_API_KEY=...     # then, for Claude-written drafts:
python run.py
python run.py --as-of 2026-10-20 # rerun the rules for another date
python -m pytest -q              # 5 tests, including hallucination checks
```

Set `CLAUDE_MODEL` to override the default model.

## Outputs

| File | Contents |
|---|---|
| `outputs/exceptions.csv` | Every flag with severity, supplier, PO, detail, action, and metrics |
| `outputs/summary.md` | Team summary, highest severity first |
| `outputs/supplier_emails.md` | One email per supplier, scoped to that supplier's POs only |
| `outputs/validation.json` | Pass or fail, with each issue listed |

## Sample run (2026-10-13)

The run finds 10 exceptions: 6 high and 4 medium. Highlights:

- **PO-24101** is slipping 11 days, which leaves the Daily Gummy Pack about 3 days short of cover.
- **PO-24103** was received 9% short, and the SKU has no open replenishment PO.
- **PO-24104** (pouch film) has gone 11 days without an acknowledgment, and stock runs out before it lands.
- **PO-24107** has 2 of 7 free days left at port.

## Extending it

- Swap the CSVs for a NetSuite saved-search export; the column names map directly.
- Add a weekly meeting-recap prompt that reads `exceptions.csv` week over week.
- Schedule `run.py` daily and send only the validated outputs.
