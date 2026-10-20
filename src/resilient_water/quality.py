from copy import deepcopy
import math

NODE_TYPES = {"source", "treatment", "treated_outlet", "reservoir", "demand"}
NODE_QUANTITIES = ["source_yield_m3_day", "treatment_capacity_m3_day", "demand_m3_day", "essential_demand_m3_day",
                   "storage_m3", "storage_min_m3", "storage_max_m3"]


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_data(rows):
    result = deepcopy(rows)
    for row in result:
        value = row.get("value")
        bad = not is_number(value)
        if not bad:
            bad = value < 0 or (row.get("kind") == "diameter_mm" and value <= 0)
        row["quality"] = "QUARANTINED" if bad else "PASS"
    return result


def validate_network(n):
    """Schema / unit sanity checks. Raises ValueError (never KeyError/TypeError) on bad input."""
    for key in ["nodes", "edges", "candidates"]:
        if not isinstance(n.get(key), list):
            raise ValueError(f"Network needs a '{key}' list")
    for v in n["nodes"]:
        missing = [k for k in ["id", "type", "water_type", "region_id"] + NODE_QUANTITIES if k not in v]
        if missing:
            raise ValueError(f"Node {v.get('id')} missing {missing}")
        if v["type"] not in NODE_TYPES:
            raise ValueError(f"Unknown node type: {v['type']}")
    nodes = {x["id"]: x for x in n["nodes"]}
    if len(nodes) != len(n["nodes"]):
        raise ValueError("Duplicate node ID")
    links = n["edges"] + n["candidates"]
    if len({e.get("id") for e in links}) != len(links):
        raise ValueError("Duplicate edge/candidate ID")
    for e in links:
        missing = [k for k in ["id", "from_node", "to_node", "layer", "water_type", "capacity", "existing",
                               "cost_opex_per_m3"] if k not in e]
        if missing:
            raise ValueError(f"Link {e.get('id')} missing {missing}")
        if e["from_node"] not in nodes or e["to_node"] not in nodes:
            raise ValueError(f"Unknown node on link {e['id']}")
        if e["from_node"] == e["to_node"]:
            raise ValueError(f"Self-loop link {e['id']}")
        a, b = nodes[e["from_node"]], nodes[e["to_node"]]
        if e["layer"] == "treatment":
            valid = a["type"] == "treatment" and b["type"] == "treated_outlet"
        else:
            valid = a["water_type"] == b["water_type"] == e["water_type"]
        if not valid:
            raise ValueError("Untreated raw/potable mixing: " + e["id"])
        if not is_number(e["capacity"]) or e["capacity"] < 0:
            raise ValueError(f"Invalid capacity: {e['id']}")
        if not is_number(e["cost_opex_per_m3"]) or e["cost_opex_per_m3"] < 0:
            raise ValueError(f"Invalid operating cost: {e['id']}")
        if not e["existing"] and (not is_number(e.get("capex_estimate")) or e["capex_estimate"] < 0):
            raise ValueError(f"Invalid cost: {e['id']}")
    for v in nodes.values():
        for k in NODE_QUANTITIES:
            if not is_number(v[k]) or v[k] < 0:
                raise ValueError(f"Invalid node quantity {k}: {v['id']}")
        if v["essential_demand_m3_day"] > v["demand_m3_day"]:
            raise ValueError("Essential exceeds demand")
        if not v["storage_min_m3"] <= v["storage_m3"] <= v["storage_max_m3"]:
            raise ValueError("Invalid storage")
    return True


def validate_scenarios(n, scenarios, prep_days=0):
    if not scenarios or len({s.get("id") for s in scenarios}) != len(scenarios):
        raise ValueError("Unique nonempty scenarios required")
    if not isinstance(prep_days, int) or not 0 <= prep_days <= 7:
        raise ValueError("Prep days: 0..7")
    nodes = {v["id"]: v for v in n["nodes"]}
    edge_ids = {e["id"] for e in n["edges"] + n["candidates"]}
    for s in scenarios:
        if not isinstance(s.get("days"), int) or not 1 <= s["days"] <= 30:
            raise ValueError("Days: 1..30")
        if not is_number(s.get("source_factor")) or not 0 <= s["source_factor"] <= 2:
            raise ValueError("Invalid source factor")
        if not is_number(s.get("demand_factor")) or not 0 < s["demand_factor"] <= 3:
            raise ValueError("Invalid demand factor")
        if set(s.get("failed_edges", [])) - edge_ids:
            raise ValueError("Unknown failed facility")
        for vid in s.get("failed_sources", []):
            if vid not in nodes or nodes[vid]["type"] != "source":
                raise ValueError(f"Failed source must be a source node: {vid}")
        for eid, f in s.get("capacity_factors", {}).items():
            if eid not in edge_ids or not is_number(f) or not 0 <= f <= 2:
                raise ValueError(f"Invalid capacity factor: {eid}")
        overrides = s.get("initial_storage", {})
        for vid, value in overrides.items():
            v = nodes.get(vid)
            if v is None or v["type"] != "reservoir" or not is_number(value) \
                    or not v["storage_min_m3"] <= value <= v["storage_max_m3"]:
                raise ValueError(f"Invalid initial storage: {vid}")
        if overrides and prep_days > 0:
            raise ValueError("Scenario-specific initial storage needs prep_days=0 (prep days must share one state)")
    if len({s["days"] for s in scenarios}) > 1:
        raise ValueError("Use equal scenario durations for robust horizon comparison")
    return True
