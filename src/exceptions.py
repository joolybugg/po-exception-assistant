"""Deterministic exception rules.

Every fact the assistant reports is computed here, in plain Python, from the
source CSVs. Claude never calculates a date, quantity, or slip; it only writes
narrative around facts this module has already established.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

import pandas as pd

OPEN_STATUSES = {"open", "in_production", "in_transit", "at_port"}
SHORT_RECEIPT_TOLERANCE = 0.005  # ignore shortfalls under 0.5 percent
UNCONFIRMED_GRACE_DAYS = 3
DEMURRAGE_WARNING_DAYS = 2
# Rules that describe our own inventory position; never shared with suppliers.
INTERNAL_ONLY = {"STOCKOUT_RISK"}


@dataclass
class Flag:
    rule: str
    severity: str  # high, medium, low
    supplier: str
    po_number: str | None
    sku: str
    detail: str
    recommended_action: str
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    date_cols = ["order_date", "promised_date", "latest_eta", "port_arrival_date"]
    pos = pd.read_csv(data_dir / "purchase_orders.csv", parse_dates=date_cols)
    inv = pd.read_csv(data_dir / "inventory.csv")
    return pos, inv


def _d(ts) -> date:
    return ts.date()


def late_vs_promise(pos: pd.DataFrame, as_of: date) -> list[Flag]:
    flags = []
    open_pos = pos[pos["status"].isin(OPEN_STATUSES)]
    for r in open_pos.itertuples():
        slip = (r.latest_eta - r.promised_date).days
        if slip <= 0:
            continue
        flags.append(Flag(
            rule="LATE_VS_PROMISE",
            severity="high" if slip >= 7 else "medium",
            supplier=r.supplier, po_number=r.po_number, sku=r.sku,
            detail=(f"{r.po_number} ({r.item}) latest ETA {_d(r.latest_eta)} is "
                    f"{slip} days past the promised date {_d(r.promised_date)}."),
            recommended_action=("Get a committed recovery date in writing and check whether "
                                "a partial shipment or expedited freight covers demand."),
            metrics={"slip_days": slip, "qty_ordered": int(r.qty_ordered)},
        ))
    return flags


def unconfirmed_orders(pos: pd.DataFrame, as_of: date) -> list[Flag]:
    flags = []
    for r in pos[(pos["status"] == "open") & (pos["supplier_confirmed"] == "N")].itertuples():
        age = (as_of - _d(r.order_date)).days
        if age <= UNCONFIRMED_GRACE_DAYS:
            continue
        flags.append(Flag(
            rule="UNCONFIRMED_ORDER",
            severity="medium",
            supplier=r.supplier, po_number=r.po_number, sku=r.sku,
            detail=(f"{r.po_number} ({r.item}) was placed {_d(r.order_date)} for delivery by "
                    f"{_d(r.promised_date)} and has gone {age} days without a supplier acknowledgment."),
            recommended_action="Request a written order acknowledgment with a committed ship date.",
            metrics={"days_unconfirmed": age, "qty_ordered": int(r.qty_ordered)},
        ))
    return flags


def short_receipts(pos: pd.DataFrame, as_of: date) -> list[Flag]:
    flags = []
    for r in pos[pos["status"].isin({"received", "partial"})].itertuples():
        short = int(r.qty_ordered - r.qty_received)
        pct = short / r.qty_ordered
        if pct <= SHORT_RECEIPT_TOLERANCE:
            continue
        flags.append(Flag(
            rule="SHORT_RECEIPT",
            severity="high" if pct > 0.05 else "low",
            supplier=r.supplier, po_number=r.po_number, sku=r.sku,
            detail=(f"{r.po_number} ({r.item}) received {int(r.qty_received):,} of "
                    f"{int(r.qty_ordered):,} units, short {short:,} ({pct:.1%})."),
            recommended_action=("Open a shortage claim and confirm whether the balance ships "
                                "or is credited."),
            metrics={"qty_ordered": int(r.qty_ordered), "qty_received": int(r.qty_received),
                     "qty_short": short, "pct_short": round(pct * 100, 1)},
        ))
    return flags


def demurrage_risk(pos: pd.DataFrame, as_of: date) -> list[Flag]:
    flags = []
    at_port = pos[(pos["status"] == "at_port") & pos["port_arrival_date"].notna()]
    for r in at_port.itertuples():
        days_at_port = (as_of - _d(r.port_arrival_date)).days
        remaining = int(r.free_days) - days_at_port
        if remaining > DEMURRAGE_WARNING_DAYS:
            continue
        flags.append(Flag(
            rule="DEMURRAGE_RISK",
            severity="high" if remaining <= 0 else "medium",
            supplier=r.supplier, po_number=r.po_number, sku=r.sku,
            detail=(f"{r.po_number} ({r.item}) has been at port since "
                    f"{_d(r.port_arrival_date)} with {remaining} of {int(r.free_days)} "
                    f"free days left."),
            recommended_action=("Book drayage pickup now, and ask the carrier for a free-time "
                                "extension if pickup cannot land in time."),
            metrics={"days_at_port": days_at_port, "free_days": int(r.free_days),
                     "free_days_remaining": remaining},
        ))
    return flags


def stockout_risk(pos: pd.DataFrame, inv: pd.DataFrame, as_of: date) -> list[Flag]:
    flags = []
    open_pos = pos[pos["status"].isin(OPEN_STATUSES)]
    for r in inv.itertuples():
        cover = r.on_hand / r.daily_demand
        inbound = open_pos[open_pos["sku"] == r.sku].sort_values("latest_eta")
        if inbound.empty:
            days_to_supply = int(r.lead_time_days)
            po_number, supplier, source = None, "Internal", "no open PO; uses lead time"
        else:
            nxt = inbound.iloc[0]
            days_to_supply = (_d(nxt["latest_eta"]) - as_of).days
            po_number, supplier = nxt["po_number"], nxt["supplier"]
            source = f"next inbound {po_number} ETA {_d(nxt['latest_eta'])}"
        gap = round(days_to_supply - cover, 1)
        if gap > 0:
            severity = "high"
            action = ("Pull in the inbound PO or split-ship a partial quantity."
                      if po_number else "Place a replenishment PO now.")
        elif cover < r.safety_stock_days:
            severity = "medium"
            action = "Monitor daily and confirm the inbound date holds."
        else:
            continue
        flags.append(Flag(
            rule="STOCKOUT_RISK",
            severity=severity, supplier=supplier, po_number=po_number, sku=r.sku,
            detail=(f"{r.sku} ({r.item}) has {cover:.1f} days of cover against "
                    f"{days_to_supply} days to next supply ({source}); "
                    f"projected gap {max(gap, 0)} days."),
            recommended_action=action,
            metrics={"on_hand": int(r.on_hand), "daily_demand": int(r.daily_demand),
                     "days_of_cover": round(cover, 1), "days_to_supply": days_to_supply,
                     "gap_days": max(gap, 0)},
        ))
    return flags


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def run_all(pos: pd.DataFrame, inv: pd.DataFrame, as_of: date) -> list[Flag]:
    flags = (late_vs_promise(pos, as_of) + unconfirmed_orders(pos, as_of)
             + short_receipts(pos, as_of) + demurrage_risk(pos, as_of)
             + stockout_risk(pos, inv, as_of))
    return sorted(flags, key=lambda f: (SEVERITY_ORDER[f.severity], f.supplier, f.po_number or ""))