"""Entirely synthetic, local coordinates; no actual infrastructure or GIS routes."""
from copy import deepcopy


def load_network(dataset_version="synthetic-v1"):
    nodes, edges = [], []
    for i in range(3):
        r = f"R{i+1}"
        for suffix, kind, water, x in [("S", "source", "raw", 0), ("T", "treatment", "raw", 1), ("P", "treated_outlet", "potable", 2), ("B", "reservoir", "potable", 3), ("D1", "demand", "potable", 4), ("D2", "demand", "potable", 4)]:
            nodes.append(dict(id=r+suffix, name=r+" "+suffix, type=kind, water_type=water, region_id=r, basin_id=f"B{i+1}", x=x, y=i*3+(0.6 if suffix=="D2" else 0), crs="LOCAL_SYNTHETIC", elevation_m=20+i*10, source_yield_m3_day=[170,100,140][i] if kind=="source" else 0, treatment_capacity_m3_day=[160,100,140][i] if kind=="treatment" else 0, demand_m3_day=(80 if suffix=="D1" else 60) if kind=="demand" else 0, essential_demand_m3_day=(50 if suffix=="D1" else 30) if kind=="demand" else 0, storage_m3=60 if kind=="reservoir" else 0, storage_min_m3=15 if kind=="reservoir" else 0, storage_max_m3=100 if kind=="reservoir" else 0, data_source_id=dataset_version))
        for suffix, a, b, cap, layer in [("intake","S","T",200,"raw_transfer"),("process","T","P",[160,100,140][i],"treatment"),("trunk","P","B",[145,95,125][i],"potable_transfer"),("d1","B","D1",90,"distribution"),("d2","B","D2",70,"distribution")]:
            edges.append(dict(id=r+"-"+suffix, from_node=r+a, to_node=r+b, capacity=cap, layer=layer, water_type="raw" if layer=="raw_transfer" else "potable", cost_opex_per_m3=0.02, existing=True))
    candidates=[]
    for cid,a,b,cap,cost,kind in [("C1","R1P","R2B",70,4,"new_interconnection"),("C2","R3P","R2B",65,3,"new_interconnection"),("C3","R1P","R1B",80,2,"upgrade_pipe"),("C4","R2P","R3B",60,3,"new_pipe"),("C5","R3P","R3B",70,2,"new_pipe"),("C6","R1S","R2T",60,3,"new_interconnection")]:
        candidates.append(dict(id=cid,from_node=a,to_node=b,capacity=cap,layer="raw_transfer" if cid=="C6" else "potable_transfer",water_type="raw" if cid=="C6" else "potable",cost_opex_per_m3=0.04,existing=False,capex_estimate=cost*100_000_000,asset_type=kind,enabled=True,permit_status="UNCONFIRMED",environmental_status="UNCONFIRMED",validity_level="PRELIMINARY_CANDIDATE",route_geometry=None,route_source="ABSTRACT_NODE_LINK",capex_uncertainty=0.5,lead_time_months=18,feasibility_flags=["SYNTHETIC_COST", "LEGAL_ENVIRONMENT_REVIEW_REQUIRED"],cost_basis="SYNTHETIC_DEMO: capacity-independent option assumption",exclusive_group=None))
    lookup={v["id"]:v for v in nodes}
    for a in candidates:
        a.update(elevation_difference_m=lookup[a["to_node"]]["elevation_m"]-lookup[a["from_node"]]["elevation_m"],design_status="PRELIMINARY_CANDIDATE",construction_constraints="노선·지형·시공성 미조사",legal_constraints="인허가·수리권·환경 검토 전",opex_estimate=a["cost_opex_per_m3"]*a["capacity"])
    return dict(version=dataset_version,nodes=nodes,edges=edges,candidates=candidates,coordinate_status="SYNTHETIC_LOGICAL_ONLY",currency="KRW",catalog=dict(dataset_id=dataset_version,agency="Synthetic demo",license="CC0-1.0 synthetic inputs",quality="SYNTHETIC",lineage="Deterministic generator; not observations"))


def generate_scenarios(days=3, severity=0.6, seed=0):
    # Deterministic generation, seed recorded for provenance; no learned predictor.
    return [dict(id=n,days=days,source_factor=f,demand_factor=d,failed_edges=broken,failed_sources=[],version="stress-v1",seed=seed,trigger=trigger) for n,f,d,broken,trigger in [
        ("NORMAL",1.,1.,[],"정상 준비 운영"),
        ("LONG_DROUGHT",severity,1.,[],"유입량 감소 확인"),
        ("PIPE_BREAK",1.,1.,["R2-trunk"],"R2 송수관 파손 확인"),
        ("COMPOUND",severity,1.15,["R2-trunk","R3-trunk"],"가뭄·폭염·복수 관로 파손 확인")]]


def held_out_scenario(days=3):
    s=deepcopy(generate_scenarios(days)[-1]); s.update(id="HELD_OUT_EXTREME",source_factor=0.25,demand_factor=1.3,failed_sources=["R1S"])
    return s
