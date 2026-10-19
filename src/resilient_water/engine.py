"""Two-stage capacity/storage MILP solved by SciPy's bundled HiGHS.

One shared binary investment vector; scenario/day-specific continuous recourse.
Lexicographic objectives are solved separately, never replaced by fake scores.
"""
import hashlib
import json
import math
from pathlib import Path
from copy import deepcopy
import numpy as np
from scipy.optimize import milp, Bounds, LinearConstraint
from scipy.sparse import coo_matrix
from .quality import validate_network
from . import __version__


def digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class Model:
    def __init__(self):
        self.keys=[]; self.idx={}; self.lo=[]; self.hi=[]; self.integer=[]; self.rows=[]; self.lb=[]; self.ub=[]
    def var(self,key,upper=np.inf,integer=0,lower=0):
        i=len(self.keys); self.keys.append(key); self.idx[key]=i
        self.lo.append(lower); self.hi.append(upper); self.integer.append(integer)
        return i
    def row(self,terms,lo=-np.inf,hi=np.inf):
        self.rows.append(terms); self.lb.append(lo); self.ub.append(hi)
    def solve(self,objective):
        rr=[]; cc=[]; vv=[]
        for r,terms in enumerate(self.rows):
            for c,v in terms.items(): rr.append(r); cc.append(c); vv.append(v)
        matrix=coo_matrix((vv,(rr,cc)),shape=(len(self.rows),len(self.keys))).tocsc()
        cost=np.zeros(len(self.keys))
        for i,v in objective.items(): cost[i]=v
        res=milp(cost,integrality=np.array(self.integer),bounds=Bounds(self.lo,self.hi),constraints=LinearConstraint(matrix,self.lb,self.ub),options={"mip_rel_gap":0.0,"time_limit":120})
        if res.status!=0: raise RuntimeError(f"Optimization failed: {res.status} {res.message}")
        return res


def optimize_preparedness_design(network,candidates,scenarios,budget,objectives=None,*,forbidden=(),fixed=None,exclude_designs=()):
    n=deepcopy(network); n["candidates"]=deepcopy(candidates); validate_network(n)
    if not math.isfinite(budget) or budget<0: raise ValueError("Budget must be nonnegative")
    if not scenarios or len({s["id"] for s in scenarios})!=len(scenarios): raise ValueError("Unique nonempty scenarios required")
    nodes=n["nodes"]; edges=n["edges"]+n["candidates"]
    node_ids={v["id"] for v in nodes}; edge_ids={e["id"] for e in edges}
    for s in scenarios:
        if not isinstance(s["days"],int) or not 1<=s["days"]<=30: raise ValueError("Days: 1..30")
        if not 0<=s["source_factor"]<=2 or not 0<s["demand_factor"]<=3: raise ValueError("Invalid scenario factors")
        if set(s["failed_edges"])-edge_ids or set(s.get("failed_sources",[]))-node_ids: raise ValueError("Unknown failed facility")
    m=Model(); y={}
    for a in candidates:
        allowed=a.get("enabled",True) and a["id"] not in forbidden and a.get("permit_status")!="DENIED" and a.get("environmental_status")!="DENIED"
        upper=int(allowed); lower=0
        if fixed is not None:
            lower=int(a["id"] in fixed); upper=min(upper,lower)
            if lower>upper: raise ValueError("Fixed design contains forbidden asset")
        y[a["id"]]=m.var(("y",a["id"]),upper,1,lower)
    m.row({y[a["id"]]:a["capex_estimate"]/1e8 for a in candidates},hi=budget/1e8)
    groups={a.get("exclusive_group") for a in candidates}-{None,""}
    for group in groups: m.row({y[a["id"]]:1 for a in candidates if a.get("exclusive_group")==group},hi=1)
    for previous in exclude_designs:
        m.row({j:(-1 if aid in previous else 1) for aid,j in y.items()},lo=1-len(previous))
    ze=m.var(("worst_essential",)); zt=m.var(("worst_total",))
    sum_e={}; sum_t={}; operating={}; scenario_terms={}
    for s in scenarios:
        sid=s["id"]; es={}; ts={}
        for t in range(s["days"]):
            for e in edges:
                cap=e["capacity"]*(0 if e["id"] in s["failed_edges"] else 1)
                if e["layer"]=="treatment":
                    cap=min(cap,next(v["treatment_capacity_m3_day"] for v in nodes if v["id"]==e["from_node"]))
                j=m.var((sid,t,"flow",e["id"]),cap)
                if not e["existing"]: m.row({j:1,y[e["id"]]:-cap},hi=0)
                operating[j]=e["cost_opex_per_m3"]
            for v in nodes:
                vid=v["id"]; terms={}
                for e in edges:
                    j=m.idx[(sid,t,"flow",e["id"])]
                    if e["to_node"]==vid: terms[j]=terms.get(j,0)+1
                    if e["from_node"]==vid: terms[j]=terms.get(j,0)-1
                rhs=0
                if v["type"]=="source":
                    cap=v["source_yield_m3_day"]*s["source_factor"]*(0 if vid in s.get("failed_sources",[]) else 1)
                    j=m.var((sid,t,"source",vid),cap); terms[j]=1
                if v["type"]=="demand":
                    demand=v["demand_m3_day"]*s["demand_factor"]; essential=v["essential_demand_m3_day"]*s["demand_factor"]
                    x=m.var((sid,t,"supply",vid),demand); terms[x]=-1
                    u=m.var((sid,t,"shortfall",vid),demand)
                    h=m.var((sid,t,"essential_shortfall",vid),essential)
                    m.row({x:1,u:1},lo=demand,hi=demand)
                    # Essential service is supplied first; positive part, fixed by lex objectives.
                    m.row({x:1,h:1},lo=essential)
                    es[h]=1; ts[u]=1; sum_e[h]=1; sum_t[u]=1
                if v["type"]=="reservoir":
                    j=m.var((sid,t,"storage",vid),v["storage_max_m3"],lower=v["storage_min_m3"]); terms[j]=-1
                    if t==0: rhs=-v["storage_m3"]
                    else: terms[m.idx[(sid,t-1,"storage",vid)]]=1
                m.row(terms,lo=rhs,hi=rhs)
        # Source period water budget (yield is constant daily in this MVP).
        for v in nodes:
            if v["type"]=="source":
                cap=v["source_yield_m3_day"]*s["source_factor"]*s["days"]*(0 if v["id"] in s.get("failed_sources",[]) else 1)
                m.row({m.idx[(sid,t,"source",v["id"])]:1 for t in range(s["days"])},hi=cap)
        scenario_terms[sid]=(es,ts)
        m.row({**es,ze:-1},hi=0); m.row({**ts,zt:-1},hi=0)
    stages=[]
    # Volume objectives normalized to average m³/day for fair differing durations.
    # Epigraphs above use horizon volume; default scenario sets use equal durations.
    if len({s["days"] for s in scenarios})>1:
        raise ValueError("Use equal scenario durations for robust horizon comparison")
    costs={y[a["id"]]:a["capex_estimate"]/1e8 for a in candidates}
    costs.update({j:v/1e8 for j,v in operating.items()})
    for name,obj in [("worst_essential_m3",{ze:1}),("worst_shortfall_m3",{zt:1}),("sum_essential_m3",sum_e),("sum_shortfall_m3",sum_t),("cost_1e8_KRW",costs)]:
        res=m.solve(obj); value=sum(res.x[j]*v for j,v in obj.items()); stages.append(dict(objective=name,value=value,status="OPTIMAL",gap=float(res.mip_gap or 0)))
        m.row(obj,hi=value+1e-6)
    selected=[aid for aid,j in y.items() if res.x[j]>0.5]
    operations=[]
    for s in scenarios:
        sid=s["id"]; records=[]
        for key,j in m.idx.items():
            if len(key)==4 and key[0]==sid: records.append(dict(day=key[1],kind=key[2],asset_id=key[3],value=float(max(0,res.x[j]))))
        es=sum(r["value"] for r in records if r["kind"]=="essential_shortfall")
        total=sum(r["value"] for r in records if r["kind"]=="shortfall")
        operations.append(dict(scenario_id=sid,days=s["days"],essential_shortfall_m3=es,shortfall_m3=total,essential_shortfall_m3_day=es/s["days"],shortfall_m3_day=total/s["days"],records=records,trigger=s["trigger"],human_approval_required=True,recovery_actions="고장 구간 격리·복구 확인 후 실측 자료로 재최적화",validation=validate_operation(n,selected,s,records)))
    source_hash=hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()
    payload=dict(network=n,scenarios=scenarios,budget=budget,forbidden=sorted(forbidden),fixed=sorted(fixed) if fixed is not None else None,exclude_designs=exclude_designs,algorithm_version=__version__,code_sha256=source_hash)
    run_id=digest(payload)
    valid=all(o["validation"]["passed"] for o in operations)
    return dict(run_id=run_id,plan_id="PLAN-"+run_id[:12],status="GRAPH_FEASIBLE" if valid else "NOT_VALIDATED",physical_level="CONCEPTUAL_ONLY",hydraulic_status="NOT_VALIDATED",solver_status="OPTIMAL",solver="scipy.optimize.milp / HiGHS",algorithm_version=__version__,input_hash=digest(n),scenario_hash=digest(scenarios),input=payload,selected_assets=selected,candidates=candidates,capex_estimate=sum(a["capex_estimate"] for a in candidates if a["id"] in selected),budget=budget,operations=operations,worst_essential_m3_day=max(o["essential_shortfall_m3_day"] for o in operations),worst_shortfall_m3_day=max(o["shortfall_m3_day"] for o in operations),objectives=stages,limitations=["SYNTHETIC_DEMO: 실제 국가·시설·GIS 노선 아님", "선형 용량·저장량 근사; 수압·수질·펌프곡선 검증 미수행", "개념 설계; 인허가·환경·수리권·측량 및 상세 설계 필요", "고정 일별 유입, 무손실 처리, 일 단위; 종말 저장량은 최소 비상 저장량", "시나리오 가중치/확률 없음; 개발 시나리오 최악값 기준"])


def validate_operation(n,selected,s,records):
    values={(r["day"],r["kind"],r["asset_id"]):r["value"] for r in records}
    get=lambda t,k,i: values.get((t,k,i),0.)
    errors=[]; max_residual=0.; edges=n["edges"]+n["candidates"]
    for t in range(s["days"]):
        for e in edges:
            f=get(t,"flow",e["id"]); cap=e["capacity"]*(e["existing"] or e["id"] in selected)*(e["id"] not in s["failed_edges"])
            if e["layer"]=="treatment":
                cap=min(cap,next(v["treatment_capacity_m3_day"] for v in n["nodes"] if v["id"]==e["from_node"]))
            if f< -1e-5 or f>cap+1e-5: errors.append("capacity:"+e["id"])
        for v in n["nodes"]:
            vid=v["id"]
            residual=sum(get(t,"flow",e["id"]) for e in edges if e["to_node"]==vid)-sum(get(t,"flow",e["id"]) for e in edges if e["from_node"]==vid)
            if v["type"]=="source":
                q=get(t,"source",vid); residual+=q
                cap=v["source_yield_m3_day"]*s["source_factor"]*(vid not in s.get("failed_sources",[]))
                if q>cap+1e-5: errors.append("source:"+vid)
            if v["type"]=="demand":
                x=get(t,"supply",vid); residual-=x; demand=v["demand_m3_day"]*s["demand_factor"]
                if x>demand+1e-5 or abs(x+get(t,"shortfall",vid)-demand)>1e-5: errors.append("demand:"+vid)
                h=max(0,v["essential_demand_m3_day"]*s["demand_factor"]-x)
                if abs(get(t,"essential_shortfall",vid)-h)>1e-4: errors.append("essential:"+vid)
            if v["type"]=="reservoir":
                storage=get(t,"storage",vid); previous=v["storage_m3"] if t==0 else get(t-1,"storage",vid)
                residual+=previous-storage
                if not v["storage_min_m3"]-1e-5<=storage<=v["storage_max_m3"]+1e-5: errors.append("storage:"+vid)
            max_residual=max(max_residual,abs(residual))
            if abs(residual)>1e-5: errors.append("balance:"+vid)
    return dict(passed=not errors,max_mass_balance_residual_m3=max_residual,errors=errors,hydraulic_validation=False)


def optimize_emergency_operations(portfolio,scenario):
    p=portfolio["input"]
    return optimize_preparedness_design(p["network"],p["network"]["candidates"],[scenario],p["budget"],fixed=portfolio["selected_assets"])["operations"][0]


def counterfactual_without_asset(portfolio,asset_id):
    p=portfolio["input"]
    result=optimize_preparedness_design(p["network"],p["network"]["candidates"],p["scenarios"],p["budget"],forbidden=[asset_id])
    return dict(method="reoptimize_without_asset",asset_id=asset_id,run_id=result["run_id"],selected_assets=result["selected_assets"],capex_estimate=result["capex_estimate"],worst_essential_m3_day=result["worst_essential_m3_day"],worst_shortfall_m3_day=result["worst_shortfall_m3_day"],delta_worst_essential_m3_day=result["worst_essential_m3_day"]-portfolio["worst_essential_m3_day"],delta_worst_shortfall_m3_day=result["worst_shortfall_m3_day"]-portfolio["worst_shortfall_m3_day"],solver_status=result["solver_status"],objectives=result["objectives"])


def design_alternatives(network,scenarios,budget,count=3):
    alternatives=[]
    for _ in range(count):
        try: p=optimize_preparedness_design(network,network["candidates"],scenarios,budget,exclude_designs=[x["selected_assets"] for x in alternatives])
        except RuntimeError as exc:
            if "infeasible" in str(exc).lower(): break
            raise
        alternatives.append(p)
    return alternatives
