"""Bridge between plans and the tabletop gravity model (docs/hardware_interface.md).

This module never talks to a device.  It turns a computed operation into per-cycle line
targets (mL) that the hardware PC controller executes after operator approval, and it
compares measured cup volumes with the plan.  Safety (heartbeat stop, rubber-band
fail-closed, emergency stop) lives in the firmware and the physical rig, not here.
"""
import csv
import io
import json
from pathlib import Path

from .quality import validate_network

ROOT = Path(__file__).resolve().parents[2]
TWIN_NETWORK = ROOT / "data" / "hardware" / "gravity_twin_network.json"
TWIN_SCENARIOS = ROOT / "data" / "hardware" / "gravity_twin_scenarios.json"


def load_network_file(path):
    network = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_network(network)
    return network


def load_scenarios_file(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, []
    return data["development"], data.get("held_out", [])


def load_twin():
    network = load_network_file(TWIN_NETWORK)
    development, held_out = load_scenarios_file(TWIN_SCENARIOS)
    return network, development, held_out


def operation_to_schedule(network, operation, cycle_seconds=60):
    """Per-cycle target volume for every servo line plus the cup totals they should produce."""
    lines = {e["id"]: e for e in network["edges"] + network["candidates"] if e.get("hardware_line")}
    rows = []
    for r in sorted(operation["records"], key=lambda r: (r["day"], r["kind"], r["asset_id"])):
        if r["kind"] == "flow" and r["asset_id"] in lines:
            e = lines[r["asset_id"]]
            rows.append(dict(cycle=r["day"], phase=r["phase"], line=e["hardware_line"], edge_id=e["id"],
                             from_node=e["from_node"], to_node=e["to_node"], target_ml=round(r["value"], 1),
                             cycle_seconds=cycle_seconds))
        elif r["kind"] == "flow" and r["value"] > 1e-6:
            rows.append(dict(cycle=r["day"], phase=r["phase"], line=None, edge_id=r["asset_id"], from_node=None,
                             to_node=None, target_ml=round(r["value"], 1), cycle_seconds=cycle_seconds,
                             manual=True))
    return rows


def schedule_to_jsonl(schedule, plan_id):
    """JSON Lines for the hardware PC controller. Every row needs operator approval before execution."""
    out = []
    for seq, row in enumerate(r for r in schedule if r["target_ml"] > 0):
        out.append(json.dumps(dict(type="manual_link" if row.get("manual") else "line_target", seq=seq, plan_id=plan_id,
                                   cycle=row["cycle"], line=row["line"], edge_id=row["edge_id"],
                                   target_ml=row["target_ml"], requires_approval=True), ensure_ascii=False))
    return "\n".join(out) + "\n"


def planned_cup_volumes(operation):
    return {(r["day"], r["asset_id"]): r["value"] for r in operation["records"] if r["kind"] == "supply"}


def read_measurements(text):
    """CSV with columns cycle,node_id,measured_ml (extra columns such as device_id are ignored)."""
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        rows.append(dict(cycle=int(row["cycle"]), node_id=row["node_id"].strip(), measured_ml=float(row["measured_ml"])))
    return rows


def compare_measurements(network, operation, measurements):
    planned = planned_cup_volumes(operation)
    essential = {v["id"] for v in network["nodes"] if v["type"] == "demand"
                 and v["essential_demand_m3_day"] >= v["demand_m3_day"] > 0}
    rows = []
    for m in measurements:
        key = (m["cycle"], m["node_id"])
        if key not in planned:
            raise ValueError(f"No planned volume for cycle {m['cycle']} node {m['node_id']}")
        p = planned[key]
        rows.append(dict(cycle=m["cycle"], node_id=m["node_id"], planned_ml=p, measured_ml=m["measured_ml"],
                         error_ml=m["measured_ml"] - p, error_pct=(m["measured_ml"] - p) / p * 100 if p > 0 else None,
                         essential=m["node_id"] in essential))
    pct = [abs(r["error_pct"]) for r in rows if r["error_pct"] is not None]
    return dict(rows=rows, mean_abs_error_pct=sum(pct) / len(pct) if pct else None,
                max_abs_error_ml=max((abs(r["error_ml"]) for r in rows), default=None),
                essential_delivered_ml=sum(r["measured_ml"] for r in rows if r["essential"]),
                essential_planned_ml=sum(r["planned_ml"] for r in rows if r["essential"]))
