"""Allocation policy comparison on one fixed network (handoff §6.3).

A  PROPORTIONAL     myopic day-by-day proportional sharing (max-min fair water filling: every
                    reachable demand gets the same fraction of its demand), storage held at its
                    normal level when possible, no pre-positioning, no essential priority.
B  SINGLE_FORECAST  stage-1 (prep-day) operations optimised for one forecast scenario with an
                    efficiency objective (OPEX, no value on stored water); after onset the
                    recourse is re-optimised exactly like C.
C  ROBUST           stage-1 operations optimised jointly over every development scenario
                    (non-anticipative, lexicographic worst case); same recourse as B.

B and C differ only in what they do before the event is recognised, so the comparison
isolates the value of robust preparation.  All policies are scored on the same development
and held-out scenarios with the same metrics; no probabilities are used.
"""
import math

from .engine import (Model, edge_capacity, horizon_days, initial_storage, optimize_preparedness_design,
                     prepared_decisions, scenario_conditions, summarize_operation, default_prep_days)

POLICIES = {
    "A_PROPORTIONAL": "A. 비례 배분 (근시안·사전 비축 없음)",
    "B_SINGLE_FORECAST": "B. 단일 예측 최적화 (효율 중심)",
    "C_ROBUST": "C. 다중 시나리오 강건 최적화",
}
EPS = 1e-7


def _day_model(network, design, cond, storage_state, demand_floor, theta=None):
    """One-day network LP. ``demand_floor`` maps demand node -> lower bound on supply."""
    nodes = {v["id"]: v for v in network["nodes"]}
    edges = network["edges"] + network["candidates"]
    m = Model()
    flow = {e["id"]: m.var(("flow", e["id"]), edge_capacity(e, nodes, cond, e["existing"] or e["id"] in design))
            for e in edges}
    supply, storage, dev = {}, {}, {}
    th = m.var(("theta",), 1.0) if theta is None else None
    for vid, v in nodes.items():
        terms, rhs = {}, 0.0
        for e in edges:
            if e["to_node"] == vid:
                terms[flow[e["id"]]] = terms.get(flow[e["id"]], 0) + 1
            if e["from_node"] == vid:
                terms[flow[e["id"]]] = terms.get(flow[e["id"]], 0) - 1
        if v["type"] == "source":
            cap = 0.0 if vid in cond["failed_sources"] else v["source_yield_m3_day"] * cond["source_factor"]
            terms[m.var(("source", vid), cap)] = 1
        if v["type"] == "demand":
            d = v["demand_m3_day"] * cond["demand_factor"]
            x = supply[vid] = m.var(("supply", vid), d, lower=min(demand_floor.get(vid, 0.0), d))
            terms[x] = -1
            if th is not None and vid not in demand_floor and d > 0:
                m.row({x: 1, th: -d}, lo=0)  # x_i >= theta * d_i for still-active nodes
        if v["type"] == "reservoir":
            s = storage[vid] = m.var(("storage", vid), v["storage_max_m3"], lower=v["storage_min_m3"])
            terms[s] = -1
            rhs = -storage_state[vid]
            up, down = m.var(("dev+", vid)), m.var(("dev-", vid))
            m.row({s: 1, up: -1, down: 1}, lo=v["storage_m3"], hi=v["storage_m3"])  # deviation from normal level
            dev[up] = dev[down] = 1
        m.row(terms, lo=rhs, hi=rhs)
    return m, dict(flow=flow, supply=supply, storage=storage, dev=dev, theta=th)


def proportional_day(network, design, cond, storage_state):
    """Max-min fair proportional allocation for one day (water filling)."""
    demand = {v["id"]: v["demand_m3_day"] * cond["demand_factor"] for v in network["nodes"] if v["type"] == "demand"}
    frozen = {vid: 0.0 for vid, d in demand.items() if d <= 0}
    while len(frozen) < len(demand):
        m, ix = _day_model(network, design, cond, storage_state, frozen)
        theta = m.solve({ix["theta"]: -1}).x[ix["theta"]]
        active = [vid for vid in demand if vid not in frozen]
        if theta >= 1 - EPS:
            frozen.update({vid: demand[vid] for vid in active})
            break
        stuck = []
        for vid in active:
            floor = {**frozen, **{k: theta * demand[k] - EPS for k in active}}
            mi, ixi = _day_model(network, design, cond, storage_state, floor, theta=theta)
            best = mi.solve({ixi["supply"][vid]: -1}).x[ixi["supply"][vid]]
            if best <= theta * demand[vid] + 1e-6:
                stuck.append(vid)
        for vid in stuck or active:
            frozen[vid] = max(0.0, theta * demand[vid] - EPS)
    m, ix = _day_model(network, design, cond, storage_state, frozen, theta=0.0)
    res = m.solve({**ix["dev"], **{j: EPS for j in ix["flow"].values()}})  # keep normal storage, then least flow
    return {k: {a: float(max(0.0, res.x[j])) for a, j in ix[k].items()} for k in ["flow", "supply", "storage"]}, res


def run_proportional(network, design, scenario, prep_days):
    nodes = {v["id"]: v for v in network["nodes"]}
    storage_state = {vid: initial_storage(v, scenario) for vid, v in nodes.items() if v["type"] == "reservoir"}
    records = []
    for t in range(horizon_days(scenario, prep_days)):
        cond = scenario_conditions(scenario, t, prep_days)
        day, res = proportional_day(network, design, cond, storage_state)
        phase = cond["phase"]
        for eid, f in day["flow"].items():
            records.append(dict(day=t, phase=phase, kind="flow", asset_id=eid, value=f))
        for vid, v in nodes.items():
            if v["type"] == "source":
                outflow = sum(day["flow"][e["id"]] for e in network["edges"] + network["candidates"] if e["from_node"] == vid)
                inflow = sum(day["flow"][e["id"]] for e in network["edges"] + network["candidates"] if e["to_node"] == vid)
                records.append(dict(day=t, phase=phase, kind="source", asset_id=vid, value=max(0.0, outflow - inflow)))
            if v["type"] == "demand":
                d = v["demand_m3_day"] * cond["demand_factor"]
                x = min(d, day["supply"][vid])
                records += [dict(day=t, phase=phase, kind="supply", asset_id=vid, value=x),
                            dict(day=t, phase=phase, kind="shortfall", asset_id=vid, value=d - x),
                            dict(day=t, phase=phase, kind="essential_shortfall", asset_id=vid,
                                 value=max(0.0, v["essential_demand_m3_day"] * cond["demand_factor"] - x))]
            if v["type"] == "reservoir":
                storage_state[vid] = day["storage"][vid]
                records.append(dict(day=t, phase=phase, kind="storage", asset_id=vid, value=day["storage"][vid]))
    return summarize_operation(network, design, scenario, records, prep_days)


def compare_policies(network, scenarios, design=(), *, held_out=(), forecast_id="NORMAL", prep_days=None):
    """Score A/B/C on the development scenarios and on held-out scenarios not used for B/C."""
    prep_days = default_prep_days(network) if prep_days is None else prep_days
    design = list(design)
    cands = network["candidates"]
    forecast = [s for s in scenarios if s["id"] == forecast_id] or scenarios[:1]
    robust = optimize_preparedness_design(network, cands, scenarios, math.inf, fixed=design, prep_days=prep_days)
    single = optimize_preparedness_design(network, cands, forecast, math.inf, fixed=design, prep_days=prep_days,
                                          profile="efficiency")
    prep = {"B_SINGLE_FORECAST": prepared_decisions(single), "C_ROBUST": prepared_decisions(robust)}
    rows = []
    for group, items in [("development", scenarios), ("held_out", list(held_out))]:
        for s in items:
            for policy in POLICIES:
                if policy == "A_PROPORTIONAL":
                    op = run_proportional(network, design, s, prep_days)
                else:
                    op = optimize_preparedness_design(network, cands, [s], math.inf, fixed=design, prep_days=prep_days,
                                                      fixed_prep=prep[policy] if prep_days else None)["operations"][0]
                rows.append(dict(policy=policy, policy_name=POLICIES[policy], scenario_set=group, operation=op))
    return dict(design=design, prep_days=prep_days, forecast_id=forecast[0]["id"], rows=rows,
                summary=summarize_policies(rows), note=(
                    "A는 매일 그날 정보만으로 비례 배분, B·C는 재난 인지 후 동일한 재최적화를 쓰며 사전(prep) 운영만 다르다. "
                    "prep_days=0이면 B와 C는 같은 결과가 정상이다. 확률·기대값은 계산하지 않는다."))


def metric_row(op):
    return dict(essential_satisfaction=op["essential_satisfaction"], supply_satisfaction=op["supply_satisfaction"],
                essential_shortfall_m3=op["essential_shortfall_m3"], shortfall_m3=op["shortfall_m3"],
                max_regional_shortfall_ratio=max(op["regional_shortfall_ratio"].values(), default=0.0),
                regional_shortfall_spread=op["regional_shortfall_spread"], opex_KRW=op["opex_KRW"],
                storage_after_prep_m3=sum(op["storage_after_prep_m3"].values()),
                validation_passed=op["validation"]["passed"])


def summarize_policies(rows):
    out = []
    for policy in POLICIES:
        for group in ["development", "held_out"]:
            ops = [r["operation"] for r in rows if r["policy"] == policy and r["scenario_set"] == group]
            if not ops:
                continue
            metrics = [metric_row(o) for o in ops]
            normal = next((metric_row(o) for o in ops if o["scenario_id"] == "NORMAL"), None)
            out.append(dict(
                policy=policy, policy_name=POLICIES[policy], scenario_set=group, scenarios=len(ops),
                worst_essential_satisfaction=min(x["essential_satisfaction"] for x in metrics),
                worst_essential_shortfall_m3=max(x["essential_shortfall_m3"] for x in metrics),
                worst_shortfall_m3=max(x["shortfall_m3"] for x in metrics),
                total_shortfall_m3=sum(x["shortfall_m3"] for x in metrics),
                worst_regional_shortfall_ratio=max(x["max_regional_shortfall_ratio"] for x in metrics),
                worst_regional_spread=max(x["regional_shortfall_spread"] for x in metrics),
                normal_opex_KRW=normal["opex_KRW"] if normal else None,
                all_validated=all(x["validation_passed"] for x in metrics)))
    return out
