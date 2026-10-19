from copy import deepcopy
import math


def validate_data(rows):
    result=deepcopy(rows)
    for row in result:
        value=row.get("value")
        bad=not isinstance(value,(int,float)) or not math.isfinite(value)
        if not bad:
            bad=value<0 or (row.get("kind")=="diameter_mm" and value<=0)
        row["quality"]="QUARANTINED" if bad else "PASS"
    return result


def validate_network(n):
    nodes={x["id"]:x for x in n["nodes"]}
    if len(nodes)!=len(n["nodes"]): raise ValueError("Duplicate node ID")
    links=n["edges"]+n["candidates"]
    if len({e["id"] for e in links})!=len(links): raise ValueError("Duplicate edge/candidate ID")
    for e in links:
        a,b=nodes[e["from_node"]],nodes[e["to_node"]]
        if e["layer"]=="treatment":
            valid=a["type"]=="treatment" and b["type"]=="treated_outlet"
        else:
            valid=a["water_type"]==b["water_type"]==e["water_type"]
        if not valid: raise ValueError("Untreated raw/potable mixing: "+e["id"])
        if not math.isfinite(e["capacity"]) or e["capacity"]<0: raise ValueError("Invalid capacity")
        if not e["existing"] and (not math.isfinite(e["capex_estimate"]) or e["capex_estimate"]<0): raise ValueError("Invalid cost")
    for v in nodes.values():
        for k in ["source_yield_m3_day","demand_m3_day","essential_demand_m3_day","storage_m3","storage_min_m3","storage_max_m3"]:
            if not math.isfinite(v[k]) or v[k]<0: raise ValueError("Invalid node quantity")
        if v["essential_demand_m3_day"]>v["demand_m3_day"]: raise ValueError("Essential exceeds demand")
        if not v["storage_min_m3"]<=v["storage_m3"]<=v["storage_max_m3"]: raise ValueError("Invalid storage")
    return True
