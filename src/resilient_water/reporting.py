import csv
import io
import json
from html import escape
import plotly.graph_objects as go
from .engine import optimize_preparedness_design,design_alternatives,counterfactual_without_asset,optimize_emergency_operations
from .demo import held_out_scenario


def network_figure(network,selected=(),layers=None,operation=None,day=0):
    fig=go.Figure(); nodes={v["id"]:v for v in network["nodes"]}
    flows={r["asset_id"]:r["value"] for r in (operation or {}).get("records",[]) if r["kind"]=="flow" and r["day"]==day}
    for e in network["edges"]+network["candidates"]:
        if layers is not None and e["layer"] not in layers: continue
        a,b=nodes[e["from_node"]],nodes[e["to_node"]]; chosen=e["id"] in selected
        color="#22c55e" if chosen else ("#ef8354" if e["water_type"]=="raw" else "#60a5fa") if e["existing"] else "#64748b"
        label=f"{e['id']}: {a['id']} → {b['id']} | {e['layer']} | 용량 {e['capacity']} m³/일"
        if operation: label+=f" | 계산 유량 {flows.get(e['id'],0):.3f} m³/일"
        fig.add_trace(go.Scatter(x=[a["x"],b["x"]],y=[a["y"],b["y"]],mode="lines",line=dict(color=color,width=4 if chosen else 2,dash="solid" if e["existing"] else "dash"),name=e["id"]+(" 선정" if chosen else " 기존" if e["existing"] else " 미선정"),text=[label,label],hovertemplate="%{text}<extra></extra>"))
        fig.add_annotation(x=b["x"]*0.55+a["x"]*.45,y=b["y"]*.55+a["y"]*.45,ax=a["x"]*.55+b["x"]*.45,ay=a["y"]*.55+b["y"]*.45,xref="x",yref="y",axref="x",ayref="y",showarrow=True,arrowhead=2,arrowcolor=color,text="")
    for kind in sorted({v["type"] for v in nodes.values()}):
        items=[v for v in nodes.values() if v["type"]==kind]
        fig.add_trace(go.Scatter(x=[v["x"] for v in items],y=[v["y"] for v in items],text=[v["id"] for v in items],mode="markers+text",textposition="top center",marker=dict(size=15),name=kind))
    fig.update_layout(title="가상 3권역 논리망 · 실제 지리·시공 노선 아님",xaxis_title="로컬 개념 좌표 X",yaxis_title="로컬 개념 좌표 Y",height=540,template="plotly_dark",legend=dict(orientation="h"),margin=dict(l=20,r=20,t=60,b=20))
    return fig


def comparison_row(p):
    return dict(plan_id=p["plan_id"],selected=", ".join(p["selected_assets"]) or "투자 없음",capex_KRW=p["capex_estimate"],worst_essential_m3_day=p["worst_essential_m3_day"],worst_shortfall_m3_day=p["worst_shortfall_m3_day"],essential_reduction_vs_baseline=p.get("essential_reduction_vs_baseline"),essential_reduction_per_1e8_KRW=p.get("essential_reduction_per_1e8_KRW"),scenario_coverage=sum(o["essential_shortfall_m3"]<1e-4 for o in p["operations"]),scenario_count=len(p["operations"]),status=p["status"])


def build_preparedness_plan(network,scenarios,budget):
    alternatives=design_alternatives(network,scenarios,budget)
    if not alternatives: raise RuntimeError("No feasible design")
    best=alternatives[0]; baseline=optimize_preparedness_design(network,network["candidates"],scenarios,0)
    for p in alternatives:
        reduction=baseline["worst_essential_m3_day"]-p["worst_essential_m3_day"]
        p["essential_reduction_vs_baseline"]=reduction
        p["essential_reduction_per_1e8_KRW"]=reduction/(p["capex_estimate"]/1e8) if p["capex_estimate"] else None
    explanations=[]
    for aid in best["selected_assets"]:
        cf=counterfactual_without_asset(best,aid)
        text=(f"기존 망의 최악 필수 부족량 {baseline['worst_essential_m3_day']:.6f} m³/일, "
              f"{aid}를 포함한 공통 설계의 최악 필수 부족량 {best['worst_essential_m3_day']:.6f} m³/일. "
              f"{aid}를 금지하고 동일 예산으로 다른 후보를 다시 선택하면 {cf['worst_essential_m3_day']:.6f} m³/일 "
              f"(변화 {cf['delta_worst_essential_m3_day']:.6f}). 전체 부족량 변화 {cf['delta_worst_shortfall_m3_day']:.6f} m³/일. "
              "변화가 0이면 대체 시설로 보완 가능하거나 이 지표에서 한계 기여가 없음을 뜻합니다.")
        by_region=[]
        for op,base in zip(best["operations"],baseline["operations"]):
            for region in sorted({v["region_id"] for v in network["nodes"]}):
                ids={v["id"] for v in network["nodes"] if v["region_id"]==region}
                value=lambda x:sum(r["value"] for r in x["records"] if r["kind"]=="essential_shortfall" and r["asset_id"] in ids)/x["days"]
                by_region.append(dict(region_id=region,scenario_id=op["scenario_id"],baseline=value(base),proposal=value(op),metric="essential_shortfall_m3_day",evidence_ref=f"{best['run_id']}/operations/{op['scenario_id']}/records/essential_shortfall"))
        explanations.append(dict(plan_id=best["plan_id"],run_id=best["run_id"],selected_asset_id=aid,decision="가상 사전 투자 후보 선정",text=text,reasons=by_region,counterfactual=cf,evidence_refs=dict(baseline=baseline["run_id"]+"/worst_essential_m3_day",proposal=best["run_id"]+"/worst_essential_m3_day",counterfactual=cf["run_id"]+"/worst_essential_m3_day"),compared_alternatives=[p["plan_id"] for p in alternatives[1:]],solver_status=best["solver_status"]))
    bottlenecks=[]
    for op in best["operations"]:
        scenario=next(s for s in scenarios if s["id"]==op["scenario_id"])
        for r in op["records"]:
            if r["kind"]=="flow":
                e=next(e for e in network["edges"]+network["candidates"] if e["id"]==r["asset_id"])
                cap=e["capacity"]*(e["id"] not in scenario["failed_edges"])*(e["existing"] or e["id"] in best["selected_assets"])
                if cap>0 and abs(r["value"]-cap)<1e-4: bottlenecks.append(dict(scenario_id=op["scenario_id"],day=r["day"],edge_id=e["id"],flow=r["value"],capacity=cap,evidence_ref=best["run_id"]+"/operations/records/flow",interpretation="포화 제약; 인과적 병목 기여는 별도 제약 완화 실험 필요"))
    days=scenarios[0]["days"]
    return dict(best=best,baseline=baseline,alternatives=alternatives,alternative_note="서로 다른 투자 조합에 no-good cut을 추가한 사전식 상위 대안" if len(alternatives)==3 else "예산·금지·배타 조건 내 서로 다른 실행 가능 투자 조합이 3개 미만",explanations=explanations,bottlenecks=bottlenecks,held_out=optimize_emergency_operations(best,held_out_scenario(days)),roadmap=[dict(stage="1 비상 운영 매뉴얼",prerequisite="실측 유입·고장·수요 확인 및 인적 승인",priority=1),dict(stage="2 기존 시설 운영 변경",prerequisite="운영 계획 재계산·시설 운전 범위 확인",priority=2),dict(stage="3 소규모 증설",assets=[a["id"] for a in network["candidates"] if a["id"] in best["selected_assets"] and a["asset_type"]=="upgrade_pipe"],prerequisite="현장 조사·수압·비용 검증",priority=3),dict(stage="4 광역 신규 연결",assets=[a["id"] for a in network["candidates"] if a["id"] in best["selected_assets"] and a["asset_type"]!="upgrade_pipe"],prerequisite="수리권·환경·인허가·설계 검토",priority=4)])


def export_preparedness_plan(bundle,format):
    p=bundle["best"]
    if format=="json": return json.dumps(bundle,ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8")
    if format=="csv":
        stream=io.StringIO(newline=""); writer=csv.DictWriter(stream,fieldnames=["run_id","scenario_id","day","kind","asset_id","value"]); writer.writeheader()
        for op in p["operations"]:
            for r in op["records"]: writer.writerow(dict(run_id=p["run_id"],scenario_id=op["scenario_id"],**r))
        return stream.getvalue().encode("utf-8-sig")
    if format=="geojson":
        # Deliberately no fake geographic positions in an RFC 7946 document.
        return json.dumps(dict(type="FeatureCollection",features=[dict(type="Feature",geometry=None,properties={**a,"selected":a["id"] in p["selected_assets"],"coordinate_status":"NO_REAL_GIS"}) for a in p["candidates"]]),ensure_ascii=False,indent=2).encode()
    if format=="html":
        import pandas as pd
        chart=network_figure(p["input"]["network"],p["selected_assets"]).to_html(full_html=False,include_plotlyjs=True)
        table=lambda rows:pd.DataFrame(rows).to_html(index=False,escape=True)
        body=f"<h1>사전 수자원 대비 계획서</h1><p>가상 데이터 · 개념 설계 · {escape(p['status'])} · 수압 NOT_VALIDATED</p><p>run_id: {p['run_id']}</p>"+chart
        body+="<h2>설계 대안</h2>"+table([comparison_row(bundle["baseline"])]+[comparison_row(a) for a in bundle["alternatives"]])
        body+="<h2>건설·확충 후보</h2>"+table([{**a,"selected":a["id"] in p["selected_assets"]} for a in p["candidates"]])
        body+="<h2>선정 근거·반사실적 재최적화</h2>"+"".join("<p>"+escape(e["text"])+"</p><pre>"+escape(json.dumps(e,ensure_ascii=False,indent=2))+"</pre>" for e in bundle["explanations"])
        body+="<h2>시나리오별 배분·저장·취수·송수</h2>"
        for op in p["operations"]:
            body+=f"<h3>{escape(op['scenario_id'])}</h3><p>{escape(op['trigger'])} / {escape(op['recovery_actions'])}</p>"+table(op["records"])+"<pre>"+escape(json.dumps(op["validation"],ensure_ascii=False))+"</pre>"
        body+="<h2>포화 제약</h2>"+table(bundle["bottlenecks"])+"<h2>단계별 로드맵</h2>"+table(bundle["roadmap"])
        body+="<h2>검증·가정·재현 정보</h2><pre>"+escape(json.dumps({k:p[k] for k in ["input_hash","scenario_hash","algorithm_version","solver","objectives","limitations"]},ensure_ascii=False,indent=2))+"</pre>"
        body+="<h2>별도 극단 시나리오 검증</h2><pre>"+escape(json.dumps(bundle["held_out"],ensure_ascii=False,indent=2))+"</pre>"
        return ("<!doctype html><html lang='ko'><meta charset='utf-8'><title>RESILIENT WATER 계획서</title><style>body{font-family:system-ui;margin:36px;background:#f4f7fb;color:#17243b}table{border-collapse:collapse;font-size:12px;display:block;overflow:auto}td,th{border:1px solid #ccd4df;padding:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"+body+"</html>").encode("utf-8")
    raise ValueError("Unsupported export format")
