"""Two-stage robust preparedness MILP solved by SciPy's bundled HiGHS.

Stage 1 (here-and-now, identical in every scenario)
  * binary investment ``y[a]`` for candidate links;
  * operations during ``prep_days`` of normal conditions before the event is
    recognised.  Non-anticipativity rows force these to be equal across
    scenarios, so the model decides how much water to pre-position in service
    reservoirs without knowing which disaster will follow.
Stage 2 (recourse, per scenario)
  * intake, link flows, supply, shortfall and storage on the event days.  After
    onset the event is assumed known for the rest of the horizon (perfect
    information after recognition; rolling-horizon MPC is future work).

Objectives are solved lexicographically.  Each earlier optimum is fixed with a
*relative* slack so large horizon volumes never fall below the solver's
feasibility tolerance (the absolute 1e-6 slack of v0.1 made 14-day runs fail).
"""
import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from . import __version__
from .quality import validate_network, validate_scenarios

REL_TOL = 1e-7
ABS_TOL = 1e-6
TIME_LIMIT_S = 120

PERFORMANCE_STAGES = ["worst_essential_m3", "worst_shortfall_m3", "worst_regional_shortfall_ratio",
                      "sum_essential_m3", "sum_shortfall_m3"]
PROFILES = {
    # Robust preparedness: protect the worst case, then equity, then totals, then
    # spend as little as possible, then keep as much water as possible in storage.
    "robust": PERFORMANCE_STAGES + ["capex_1e8_KRW", "terminal_storage_m3", "opex_KRW"],
    # Efficiency-oriented single-forecast operation: no value on stored water.
    "efficiency": PERFORMANCE_STAGES + ["capex_1e8_KRW", "opex_KRW"],
}
LIMITATIONS = [
    "SYNTHETIC_OR_SCALE_MODEL: 실제 국가·시설·GIS 노선 아님",
    "선형 용량·저장량 근사; 수압·수질·펌프곡선 검증 미수행",
    "개념 설계; 인허가·환경·수리권·측량 및 상세 설계 필요",
    "일 단위 고정 유입·무손실 처리; 재난 인지 후 잔여 기간은 완전정보로 재최적화(MPC 미구현)",
    "시나리오 가중치/확률 없음; 개발 시나리오 최악값 기준",
]


class SolverFailure(RuntimeError):
    """The solver did not prove optimality (time limit, numerical trouble, ...)."""


class InfeasibleModel(SolverFailure):
    """The solver proved the model infeasible."""


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def snap(value):
    """Clip solver noise (incl. the lexicographic stage slack) to exact zero."""
    return 0.0 if value < 2 * ABS_TOL else float(value)


def stage_slack(value):
    return max(ABS_TOL, REL_TOL * abs(value))


class Model:
    """Minimal sparse MILP builder on top of ``scipy.optimize.milp``."""

    def __init__(self):
        self.keys, self.idx = [], {}
        self.lo, self.hi, self.integer = [], [], []
        self.rows, self.lb, self.ub = [], [], []

    def var(self, key, upper=np.inf, integer=0, lower=0.0):
        i = len(self.keys)
        self.keys.append(key)
        self.idx[key] = i
        self.lo.append(lower)
        self.hi.append(upper)
        self.integer.append(integer)
        return i

    def row(self, terms, lo=-np.inf, hi=np.inf):
        self.rows.append(dict(terms))
        self.lb.append(lo)
        self.ub.append(hi)

    def fix(self, i, value):
        value = min(max(value, self.lo[i]), self.hi[i])
        self.lo[i] = self.hi[i] = value

    def solve(self, objective):
        rr, cc, vv = [], [], []
        for r, terms in enumerate(self.rows):
            for c, v in terms.items():
                rr.append(r)
                cc.append(c)
                vv.append(v)
        matrix = coo_matrix((vv, (rr, cc)), shape=(len(self.rows), len(self.keys))).tocsc()
        cost = np.zeros(len(self.keys))
        for i, v in objective.items():
            cost[i] += v
        res = milp(cost, integrality=np.array(self.integer), bounds=Bounds(self.lo, self.hi),
                   constraints=LinearConstraint(matrix, self.lb, self.ub),
                   options={"mip_rel_gap": 0.0, "time_limit": TIME_LIMIT_S})
        if res.status == 2:
            raise InfeasibleModel(f"Infeasible model: {res.message}")
        if res.status != 0:
            raise SolverFailure(f"Optimization failed: {res.status} {res.message}")
        return res


# ---------------------------------------------------------------- conditions

def scenario_conditions(scenario, t, prep_days):
    """Parameters in force on horizon day ``t``: normal before the event starts."""
    if t < prep_days:
        return dict(phase="prep", source_factor=1.0, demand_factor=1.0, failed_edges=set(),
                    failed_sources=set(), capacity_factors={})
    return dict(phase="event", source_factor=scenario["source_factor"], demand_factor=scenario["demand_factor"],
                failed_edges=set(scenario.get("failed_edges", [])),
                failed_sources=set(scenario.get("failed_sources", [])),
                capacity_factors=scenario.get("capacity_factors", {}))


def edge_capacity(edge, nodes, cond, built=True):
    if not built or edge["id"] in cond["failed_edges"]:
        return 0.0
    cap = edge["capacity"] * cond["capacity_factors"].get(edge["id"], 1.0)
    if edge["layer"] == "treatment":
        cap = min(cap, nodes[edge["from_node"]]["treatment_capacity_m3_day"])
    return cap


def source_capacity(node, cond):
    if node["id"] in cond["failed_sources"]:
        return 0.0
    return node["source_yield_m3_day"] * cond["source_factor"]


def initial_storage(node, scenario):
    return scenario.get("initial_storage", {}).get(node["id"], node["storage_m3"])


def default_prep_days(network):
    return int(network.get("default_prep_days", 1))


def horizon_days(scenario, prep_days):
    return prep_days + scenario["days"]


# ---------------------------------------------------------------- optimization

def optimize_preparedness_design(network, candidates, scenarios, budget, *, forbidden=(), fixed=None,
                                 exclude_designs=(), prep_days=None, fixed_prep=None, profile="robust",
                                 allow_prep_rationing=False):
    n = deepcopy(network)
    n["candidates"] = deepcopy(candidates)
    validate_network(n)
    prep_days = default_prep_days(n) if prep_days is None else int(prep_days)
    validate_scenarios(n, scenarios, prep_days)
    if math.isnan(budget) or budget < 0:
        raise ValueError("Budget must be nonnegative")
    if profile not in PROFILES:
        raise ValueError("Unknown objective profile")
    nodes = {v["id"]: v for v in n["nodes"]}
    edges = n["edges"] + n["candidates"]

    # Unless hedging is allowed, serve normal demand as fully as physically possible before the
    # event is recognised; only then protect the worst case (no precautionary rationing).
    order = PROFILES[profile] if allow_prep_rationing else ["prep_shortfall_m3"] + PROFILES[profile]
    args = (n, nodes, edges, candidates, scenarios, budget, forbidden, prep_days, fixed_prep)
    m, y, objectives = _build_model(*args, fixed, exclude_designs)
    stages, res = _solve_lexicographic(m, objectives, order)
    selected = [aid for aid, j in y.items() if res.x[j] > 0.5]
    if candidates and fixed is None:
        # Polish: fix the binary design exactly and re-solve the stages as an LP, so the MIP
        # integrality tolerance (y≈1e-6 × link capacity) cannot leak flow through unselected links.
        m, y, objectives = _build_model(*args, selected, ())
        stages, res = _solve_lexicographic(m, objectives, order)
    selected = [aid for aid, j in y.items() if res.x[j] > 0.5]
    operations = []
    for s in scenarios:
        records = []
        for key, j in m.idx.items():
            if len(key) == 4 and key[0] == s["id"]:
                records.append(dict(day=key[1], phase=scenario_conditions(s, key[1], prep_days)["phase"],
                                    kind=key[2], asset_id=key[3], value=snap(res.x[j])))
        operations.append(summarize_operation(n, selected, s, records, prep_days))
    source_hash = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()
    payload = dict(network=n, scenarios=scenarios, budget=budget if math.isfinite(budget) else "INF",
                   forbidden=sorted(forbidden), fixed=sorted(fixed) if fixed is not None else None,
                   exclude_designs=[sorted(d) for d in exclude_designs], prep_days=prep_days,
                   fixed_prep=sorted([list(k) + [v] for k, v in (fixed_prep or {}).items()]), profile=profile,
                   allow_prep_rationing=allow_prep_rationing,
                   algorithm_version=__version__, code_sha256=source_hash)
    run_id = digest(payload)
    valid = all(o["validation"]["passed"] for o in operations)
    gap = nonanticipativity_gap(operations, prep_days)
    stage_value = {st["objective"]: st["value"] for st in stages}
    return dict(
        run_id=run_id, plan_id="PLAN-" + run_id[:12],
        status="GRAPH_FEASIBLE" if valid and gap < 1e-5 else "NOT_VALIDATED",
        physical_level="CONCEPTUAL_ONLY", hydraulic_status="NOT_VALIDATED", solver_status="OPTIMAL",
        solver="scipy.optimize.milp / HiGHS", algorithm_version=__version__, profile=profile,
        input_hash=digest(n), scenario_hash=digest(scenarios), input=payload, selected_assets=selected,
        candidates=candidates, capex_estimate=sum(a["capex_estimate"] for a in candidates if a["id"] in selected),
        budget=budget, prep_days=prep_days, nonanticipativity_max_gap=gap, operations=operations,
        worst_essential_m3_day=max(o["essential_shortfall_m3_day"] for o in operations),
        worst_shortfall_m3_day=max(o["shortfall_m3_day"] for o in operations),
        worst_regional_shortfall_ratio=stage_value.get("worst_regional_shortfall_ratio"),
        terminal_storage_m3=stage_value.get("terminal_storage_m3"),
        objectives=stages, limitations=LIMITATIONS)



def _build_model(n, nodes, edges, candidates, scenarios, budget, forbidden, prep_days, fixed_prep, fixed,
                 exclude_designs):
    m = Model()
    y = {}
    for a in candidates:
        allowed = (a.get("enabled", True) and a["id"] not in forbidden
                   and a.get("permit_status") != "DENIED" and a.get("environmental_status") != "DENIED")
        upper, lower = int(allowed), 0
        if fixed is not None:
            lower = int(a["id"] in fixed)
            upper = min(upper, lower)
            if lower > upper:
                raise ValueError("Fixed design contains forbidden asset")
        y[a["id"]] = m.var(("y", a["id"]), upper, 1, lower)
    if math.isfinite(budget):
        m.row({y[a["id"]]: a["capex_estimate"] / 1e8 for a in candidates}, hi=budget / 1e8)
    for group in {a.get("exclusive_group") for a in candidates} - {None, ""}:
        m.row({y[a["id"]]: 1 for a in candidates if a.get("exclusive_group") == group}, hi=1)
    for previous in exclude_designs:
        m.row({j: (-1 if aid in previous else 1) for aid, j in y.items()}, lo=1 - len(previous))

    ze, zt, zr = m.var(("worst_essential",)), m.var(("worst_shortfall",)), m.var(("worst_regional_ratio",))
    sum_e, sum_t, opex, terminal, prep_short = {}, {}, {}, {}, {}
    regions = sorted({v["region_id"] for v in nodes.values() if v["type"] == "demand"})
    for s in scenarios:
        sid = s["id"]
        es, ts = {}, {}
        region_terms = {r: {} for r in regions}
        region_demand = {r: 0.0 for r in regions}
        horizon = horizon_days(s, prep_days)
        for t in range(horizon):
            cond = scenario_conditions(s, t, prep_days)
            for e in edges:
                cap = edge_capacity(e, nodes, cond)
                j = m.var((sid, t, "flow", e["id"]), cap)
                if not e["existing"] and cap > 0:
                    m.row({j: 1, y[e["id"]]: -cap}, hi=0)
                opex[j] = e["cost_opex_per_m3"]
            for vid, v in nodes.items():
                terms, rhs = {}, 0.0
                for e in edges:
                    j = m.idx[(sid, t, "flow", e["id"])]
                    if e["to_node"] == vid:
                        terms[j] = terms.get(j, 0) + 1
                    if e["from_node"] == vid:
                        terms[j] = terms.get(j, 0) - 1
                if v["type"] == "source":
                    terms[m.var((sid, t, "source", vid), source_capacity(v, cond))] = 1
                if v["type"] == "demand":
                    demand = v["demand_m3_day"] * cond["demand_factor"]
                    essential = v["essential_demand_m3_day"] * cond["demand_factor"]
                    x = m.var((sid, t, "supply", vid), demand)
                    u = m.var((sid, t, "shortfall", vid), demand)
                    if cond["phase"] == "prep":
                        prep_short[u] = 1
                    h = m.var((sid, t, "essential_shortfall", vid), essential)
                    terms[x] = -1
                    m.row({x: 1, u: 1}, lo=demand, hi=demand)
                    m.row({x: 1, h: 1}, lo=essential)  # h >= essential - x, made tight by the objectives
                    es[h] = ts[u] = sum_e[h] = sum_t[u] = 1
                    region_terms[v["region_id"]][u] = 1
                    region_demand[v["region_id"]] += demand
                if v["type"] == "reservoir":
                    j = m.var((sid, t, "storage", vid), v["storage_max_m3"], lower=v["storage_min_m3"])
                    terms[j] = -1
                    if t == 0:
                        rhs = -initial_storage(v, s)
                    else:
                        terms[m.idx[(sid, t - 1, "storage", vid)]] = 1
                    if t == horizon - 1:
                        terminal[j] = -1
                m.row(terms, lo=rhs, hi=rhs)
        m.row({**es, ze: -1}, hi=0)
        m.row({**ts, zt: -1}, hi=0)
        for r in regions:
            if region_demand[r] > 0:
                m.row({**{j: 1 / region_demand[r] for j in region_terms[r]}, zr: -1}, hi=0)

    # Non-anticipativity: before the event is recognised every scenario shares one plan.
    first = scenarios[0]["id"]
    for s in scenarios[1:]:
        for t in range(prep_days):
            for key, j in list(m.idx.items()):
                if len(key) == 4 and key[0] == s["id"] and key[1] == t:
                    m.row({j: 1, m.idx[(first,) + key[1:]]: -1}, lo=0, hi=0)
    if fixed_prep:
        for s in scenarios:
            for (t, kind, asset), value in fixed_prep.items():
                key = (s["id"], t, kind, asset)
                if key not in m.idx:
                    raise ValueError(f"Unknown prepared decision {key}")
                m.fix(m.idx[key], value)

    objectives = {
        "prep_shortfall_m3": prep_short,
        "worst_essential_m3": {ze: 1}, "worst_shortfall_m3": {zt: 1}, "worst_regional_shortfall_ratio": {zr: 1},
        "sum_essential_m3": sum_e, "sum_shortfall_m3": sum_t,
        "capex_1e8_KRW": {y[a["id"]]: a["capex_estimate"] / 1e8 for a in candidates},
        "terminal_storage_m3": terminal, "opex_KRW": opex,
    }
    return m, y, objectives


def _solve_lexicographic(m, objectives, order):
    stages, res = [], None
    for name in order:
        obj = {j: v for j, v in objectives[name].items() if v}
        if not obj:
            stages.append(dict(objective=name, value=0.0, status="EMPTY", gap=0.0))
            continue
        res = m.solve(obj)
        value = float(sum(res.x[j] * v for j, v in obj.items()))
        m.row(obj, hi=value + stage_slack(value))
        stages.append(dict(objective=name, value=-value if name == "terminal_storage_m3" else value,
                           status="OPTIMAL", gap=float(res.mip_gap or 0)))
    return stages, res


def summarize_operation(n, selected, s, records, prep_days):
    nodes = {v["id"]: v for v in n["nodes"]}
    horizon = horizon_days(s, prep_days)

    def total(kind, ids=None):
        return sum(r["value"] for r in records if r["kind"] == kind and (ids is None or r["asset_id"] in ids))

    demand_total = essential_total = 0.0
    regional = {}
    for t in range(horizon):
        cond = scenario_conditions(s, t, prep_days)
        for v in nodes.values():
            if v["type"] == "demand":
                demand_total += v["demand_m3_day"] * cond["demand_factor"]
                essential_total += v["essential_demand_m3_day"] * cond["demand_factor"]
                regional.setdefault(v["region_id"], [0.0, 0.0])[1] += v["demand_m3_day"] * cond["demand_factor"]
    for r in records:
        if r["kind"] == "shortfall":
            regional[nodes[r["asset_id"]]["region_id"]][0] += r["value"]
    ratios = {reg: (u / d if d > 0 else 0.0) for reg, (u, d) in sorted(regional.items())}
    es, sh = total("essential_shortfall"), total("shortfall")
    prep_end = {r["asset_id"]: r["value"] for r in records
                if r["kind"] == "storage" and prep_days > 0 and r["day"] == prep_days - 1}
    final = {r["asset_id"]: r["value"] for r in records if r["kind"] == "storage" and r["day"] == horizon - 1}
    opex_cost = sum(r["value"] * next(e["cost_opex_per_m3"] for e in n["edges"] + n["candidates"] if e["id"] == r["asset_id"])
                    for r in records if r["kind"] == "flow")
    return dict(
        scenario_id=s["id"], days=s["days"], prep_days=prep_days, horizon_days=horizon,
        essential_shortfall_m3=es, shortfall_m3=sh,
        prep_shortfall_m3=sum(r["value"] for r in records if r["kind"] == "shortfall" and r["phase"] == "prep"),
        essential_shortfall_m3_day=es / horizon, shortfall_m3_day=sh / horizon,
        essential_demand_m3=essential_total, demand_m3=demand_total,
        essential_satisfaction=1 - es / essential_total if essential_total > 0 else 1.0,
        supply_satisfaction=1 - sh / demand_total if demand_total > 0 else 1.0,
        regional_shortfall_ratio=ratios,
        regional_shortfall_spread=(max(ratios.values()) - min(ratios.values())) if ratios else 0.0,
        storage_after_prep_m3=prep_end, terminal_storage_m3=final, opex_KRW=opex_cost,
        records=records, trigger=s.get("trigger", ""), human_approval_required=True,
        recovery_actions="고장 구간 격리·복구 확인 후 실측 자료로 재최적화",
        validation=validate_operation(n, selected, s, records, prep_days))


def nonanticipativity_gap(operations, prep_days):
    if prep_days == 0 or len(operations) < 2:
        return 0.0
    ref = {(r["day"], r["kind"], r["asset_id"]): r["value"] for r in operations[0]["records"] if r["day"] < prep_days}
    gap = 0.0
    for o in operations[1:]:
        for r in o["records"]:
            if r["day"] < prep_days:
                gap = max(gap, abs(r["value"] - ref[(r["day"], r["kind"], r["asset_id"])]))
    return gap


def validate_operation(n, selected, s, records, prep_days=0):
    """Re-check an operation from its records only (no solver matrix reuse)."""
    values = {(r["day"], r["kind"], r["asset_id"]): r["value"] for r in records}

    def get(t, k, i):
        return values.get((t, k, i), 0.0)

    nodes = {v["id"]: v for v in n["nodes"]}
    edges = n["edges"] + n["candidates"]
    errors, max_residual = [], 0.0
    for t in range(horizon_days(s, prep_days)):
        cond = scenario_conditions(s, t, prep_days)
        for e in edges:
            f = get(t, "flow", e["id"])
            cap = edge_capacity(e, nodes, cond, built=e["existing"] or e["id"] in selected)
            if f < -1e-5 or f > cap + stage_slack(cap) + 1e-5:
                errors.append(f"capacity:{e['id']}@{t}")
        for vid, v in nodes.items():
            residual = (sum(get(t, "flow", e["id"]) for e in edges if e["to_node"] == vid)
                        - sum(get(t, "flow", e["id"]) for e in edges if e["from_node"] == vid))
            if v["type"] == "source":
                q = get(t, "source", vid)
                residual += q
                if q > source_capacity(v, cond) + 1e-5:
                    errors.append(f"source:{vid}@{t}")
            if v["type"] == "demand":
                x = get(t, "supply", vid)
                residual -= x
                demand = v["demand_m3_day"] * cond["demand_factor"]
                if x > demand + 1e-5 or abs(x + get(t, "shortfall", vid) - demand) > 1e-4:
                    errors.append(f"demand:{vid}@{t}")
                h = max(0.0, v["essential_demand_m3_day"] * cond["demand_factor"] - x)
                if abs(get(t, "essential_shortfall", vid) - h) > 1e-4:
                    errors.append(f"essential:{vid}@{t}")
            if v["type"] == "reservoir":
                storage = get(t, "storage", vid)
                previous = initial_storage(v, s) if t == 0 else get(t - 1, "storage", vid)
                residual += previous - storage
                if not v["storage_min_m3"] - 1e-5 <= storage <= v["storage_max_m3"] + 1e-5:
                    errors.append(f"storage:{vid}@{t}")
            max_residual = max(max_residual, abs(residual))
            if abs(residual) > 1e-4:
                errors.append(f"balance:{vid}@{t}")
    return dict(passed=not errors, max_mass_balance_residual_m3=max_residual, errors=errors, hydraulic_validation=False)


# ---------------------------------------------------------------- derived runs

def prepared_decisions(portfolio):
    """Stage-1 operations (prep days) of a solved plan, keyed by (day, kind, asset)."""
    op = portfolio["operations"][0]
    return {(r["day"], r["kind"], r["asset_id"]): r["value"] for r in op["records"] if r["phase"] == "prep"}


def optimize_emergency_operations(portfolio, scenario):
    """Fix the design and the already executed prep-day plan; re-optimize the event days."""
    p = portfolio["input"]
    return optimize_preparedness_design(
        p["network"], p["network"]["candidates"], [scenario], math.inf, fixed=portfolio["selected_assets"],
        prep_days=portfolio["prep_days"], fixed_prep=prepared_decisions(portfolio),
        allow_prep_rationing=p["allow_prep_rationing"])["operations"][0]


def counterfactual_without_asset(portfolio, asset_id):
    p = portfolio["input"]
    result = optimize_preparedness_design(p["network"], p["network"]["candidates"], p["scenarios"], portfolio["budget"],
                                          forbidden=[asset_id], prep_days=portfolio["prep_days"],
                                          allow_prep_rationing=p["allow_prep_rationing"])
    return dict(method="reoptimize_without_asset", asset_id=asset_id, run_id=result["run_id"],
                selected_assets=result["selected_assets"], capex_estimate=result["capex_estimate"],
                worst_essential_m3_day=result["worst_essential_m3_day"],
                worst_shortfall_m3_day=result["worst_shortfall_m3_day"],
                delta_worst_essential_m3_day=result["worst_essential_m3_day"] - portfolio["worst_essential_m3_day"],
                delta_worst_shortfall_m3_day=result["worst_shortfall_m3_day"] - portfolio["worst_shortfall_m3_day"],
                solver_status=result["solver_status"], objectives=result["objectives"])


def performance(p):
    values = {st["objective"]: st["value"] for st in p["objectives"]}
    return [values[name] for name in PERFORMANCE_STAGES]


def dominates(a, b):
    """True when design ``a`` is no worse than ``b`` everywhere and strictly better somewhere (incl. cost)."""
    pa, pb = performance(a) + [a["capex_estimate"] / 1e8], performance(b) + [b["capex_estimate"] / 1e8]
    no_worse = all(x <= y + stage_slack(y) + 1e-9 for x, y in zip(pa, pb))
    better = any(x < y - stage_slack(y) - 1e-9 for x, y in zip(pa, pb))
    return no_worse and better


def design_alternatives(network, scenarios, budget, count=3, *, max_solves=None, prep_days=None,
                        allow_prep_rationing=False):
    """Lexicographic top designs; designs dominated by an earlier one are reported separately."""
    alternatives, dominated, seen = [], [], []
    for _ in range(max_solves or count * 3):
        if len(alternatives) >= count:
            break
        try:
            p = optimize_preparedness_design(network, network["candidates"], scenarios, budget,
                                             exclude_designs=seen, prep_days=prep_days,
                                             allow_prep_rationing=allow_prep_rationing)
        except InfeasibleModel:
            break
        seen.append(p["selected_assets"])
        by = next((q for q in alternatives if dominates(q, p)), None)
        if by is not None:
            dominated.append(dict(plan_id=p["plan_id"], selected_assets=p["selected_assets"],
                                  capex_estimate=p["capex_estimate"], dominated_by=by["plan_id"]))
            continue
        alternatives.append(p)
    return alternatives, dominated
