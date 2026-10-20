import json
import pytest
from resilient_water.hardware import (load_twin, operation_to_schedule, schedule_to_jsonl, read_measurements,
                                      compare_measurements)
from resilient_water.reporting import build_preparedness_plan


@pytest.fixture(scope="module")
def twin():
    network, development, held_out = load_twin()
    return network, development, held_out, build_preparedness_plan(network, development, 0, held_out=held_out)


def test_twin_matches_hardware_handoff(twin):
    network, development, held_out, _ = twin
    lines = {e["hardware_line"] for e in network["edges"] if e.get("hardware_line")}
    assert lines == {"R1", "R2", "R3A", "R3B"}  # four servo lines (HARDWARE_HANDOFF §5)
    hospital = next(v for v in network["nodes"] if v["id"] == "H")
    assert hospital["essential_demand_m3_day"] == hospital["demand_m3_day"]
    assert {s["id"] for s in development} == {"NORMAL", "DROUGHT", "PIPE_BREAK", "COMPOUND"}
    assert network["units"]["volume"] == "mL"


def test_twin_plan_is_mass_balanced(twin):
    *_, bundle = twin
    assert bundle["best"]["status"] == "GRAPH_FEASIBLE"
    assert all(r["operation"]["validation"]["passed"] for r in bundle["policies"]["rows"])


def test_schedule_targets_equal_planned_line_flows(twin):
    network, *_, bundle = twin
    op = next(r["operation"] for r in bundle["policies"]["rows"] if r["policy"] == "C_ROBUST")
    schedule = operation_to_schedule(network, op)
    for row in schedule:
        if row["line"]:
            flow = next(r["value"] for r in op["records"] if r["kind"] == "flow" and r["asset_id"] == row["edge_id"] and r["day"] == row["cycle"])
            assert row["target_ml"] == pytest.approx(flow, abs=0.05)
    lines = [json.loads(x) for x in schedule_to_jsonl(schedule, "P").splitlines()]
    assert lines and all(x["requires_approval"] for x in lines)


def test_measurement_comparison(twin):
    network, *_, bundle = twin
    op = next(r["operation"] for r in bundle["policies"]["rows"] if r["policy"] == "C_ROBUST")
    planned = {(r["day"], r["asset_id"]): r["value"] for r in op["records"] if r["kind"] == "supply"}
    (cycle, node), value = next((k, v) for k, v in planned.items() if v > 0)
    result = compare_measurements(network, op, read_measurements(f"cycle,node_id,measured_ml\n{cycle},{node},{value * 0.9}\n"))
    assert result["rows"][0]["error_pct"] == pytest.approx(-10, abs=1e-6)
    with pytest.raises(ValueError):
        compare_measurements(network, op, read_measurements("cycle,node_id,measured_ml\n99,H,1\n"))
