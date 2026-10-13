from datetime import date
from pathlib import Path

from src.drafting import draft_offline
from src.exceptions import load_data, run_all
from src.validate import validate_draft

DATA = Path(__file__).resolve().parents[1] / "data"
AS_OF = date(2026, 10, 13)


def _flags():
    pos, inv = load_data(DATA)
    return run_all(pos, inv, AS_OF)


def test_expected_rules_fire():
    rules = {(f.rule, f.po_number or f.sku) for f in _flags()}
    assert ("LATE_VS_PROMISE", "PO-24101") in rules
    assert ("SHORT_RECEIPT", "PO-24103") in rules
    assert ("UNCONFIRMED_ORDER", "PO-24104") in rules
    assert ("DEMURRAGE_RISK", "PO-24107") in rules
    assert ("STOCKOUT_RISK", "SKU-120") in rules


def test_fully_received_po_is_not_flagged():
    assert not any(f.po_number == "PO-24109" for f in _flags())


def test_offline_draft_passes_validation():
    flags = _flags()
    assert validate_draft(draft_offline(flags, AS_OF), flags, AS_OF.isoformat()) == []


def test_supplier_emails_never_include_inventory_position():
    flags = _flags()
    draft = draft_offline(flags, AS_OF)
    for e in draft["emails"]:
        assert "days of cover" not in e["body"]
    # and the validator catches it if a draft tries to leak it
    draft["emails"][0]["body"] += "\nWe only have 9,000 units on hand."
    assert any("9000" in i for i in validate_draft(draft, flags, AS_OF.isoformat()))


def test_validator_catches_hallucinated_facts():
    flags = _flags()
    draft = draft_offline(flags, AS_OF)
    northfield = next(e for e in draft["emails"] if e["supplier"] == "Northfield Co-Manufacturing")
    northfield["body"] += "\nAlso, PO-24106 ships 2026-11-15 with 7,500 units."
    issues = validate_draft(draft, flags, AS_OF.isoformat())
    assert any("PO-24106" in i for i in issues)   # another supplier's PO
    assert any("2026-11-15" in i for i in issues)  # invented date
    assert any("7500" in i for i in issues)        # invented quantity
