from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent/"src"))
import json
from copy import deepcopy
import pandas as pd
import streamlit as st
from resilient_water.demo import load_network,generate_scenarios
from resilient_water.engine import digest,optimize_preparedness_design,optimize_emergency_operations
from resilient_water.reporting import build_preparedness_plan,network_figure,comparison_row,export_preparedness_plan
from resilient_water.quality import validate_network

st.set_page_config(page_title="RESILIENT WATER · 사전 대비망",page_icon="💧",layout="wide")
st.title("💧 RESILIENT WATER")
st.caption("재난 전 공통 투자망 설계 → 시나리오별 비상 배분 → 계산에 근거한 선택 이유")
st.warning("SYNTHETIC DEMO · 가상 3개 권역의 개념 설계입니다. 실제 지리·노선·시공비·수압 검증 자료가 아닙니다.")
network=load_network()
with st.sidebar:
    st.header("사전 대비 설계 조건")
    page=st.radio("화면",["권역 개요","데이터 카탈로그·품질","현재 공급망","위험 지역 분석","재난 시나리오 제작실","사전 대비망 설계 스튜디오","선정 이유·대안 비교","재난 대응 운영 시뮬레이터","대비 계획서·사업 우선순위"])
    regions=st.multiselect("보호 권역",["R1","R2","R3"],default=["R1","R2","R3"])
    budget=st.slider("투자 예산 (억원, 가상 추정)",0,20,8)
    days=st.slider("재난 분석 기간 (일)",1,14,3)
    severity=st.slider("가뭄 공급원 수율 배수",0.,1.,.6,.05)
    chosen=st.multiselect("설계에 사용할 재난",["NORMAL","LONG_DROUGHT","PIPE_BREAK","COMPOUND"],default=["NORMAL","LONG_DROUGHT","PIPE_BREAK","COMPOUND"])
    essential_scale=st.slider("필수 보호 수요 배수",0.5,1.2,1.,.1)
    st.caption("모든 시나리오에서 공통인 투자 결정 · 발생 확률 미사용")

# Preserve physical cross-region infrastructure; unprotected region demand remains
# ordinary demand but receives no essential-priority quota.
for v in network["nodes"]:
    v["essential_demand_m3_day"]=min(v["demand_m3_day"],v["essential_demand_m3_day"]*essential_scale) if v["region_id"] in regions else 0
if "candidate_edits" in st.session_state:
    network["candidates"]=deepcopy(st.session_state.candidate_edits)
scenarios=[s for s in generate_scenarios(days,severity) if s["id"] in chosen]
for s in scenarios:
    if s["id"] in st.session_state.get("scenario_edits",{}): s.update(st.session_state.scenario_edits[s["id"]])
signature=digest(dict(network=network,scenarios=scenarios,budget=budget))
bundle=st.session_state.get("bundle")
if bundle and st.session_state.get("signature")!=signature:
    st.info("입력이 변경되었습니다. 새 조건으로 사전 대비망 설계를 실행하세요."); bundle=None

if page=="권역 개요":
    st.subheader("가상국 → 3개 유역 → 3개 급수권역 → 시설")
    st.write("공급원 3개, 정수장 3개, 배수지 3개, 수요지역 6개, 기존 링크 15개와 후보 링크 6개입니다. 권역 선택은 필수 보호 우선순위에 반영됩니다.")
    st.plotly_chart(network_figure(network,bundle["best"]["selected_assets"] if bundle else []),width="stretch")
elif page=="데이터 카탈로그·품질":
    st.json(network["catalog"]); st.write("원본 입력은 결정적 생성기로 보존됩니다. P1의 실제 자료 수집·전처리 이력 DB는 미구현입니다.")
    st.dataframe(network["nodes"]); st.json({"input_hash":digest(network),"schema":"docs/data_dictionary.md","crs":network["coordinate_status"]})
elif page=="현재 공급망":
    layers=st.multiselect("레이어",["raw_transfer","treatment","potable_transfer","distribution"],default=["raw_transfer","treatment","potable_transfer","distribution"])
    st.plotly_chart(network_figure(network,layers=layers),width="stretch"); st.dataframe(network["edges"])
elif page=="위험 지역 분석":
    st.write("규칙 기반 구성 지표입니다. 발생 확률 또는 인과 효과 추정이 아닙니다.")
    rows=[]
    for region in ["R1","R2","R3"]:
        vs=[v for v in network["nodes"] if v["region_id"]==region]; demand=sum(v["demand_m3_day"] for v in vs); source=sum(v["source_yield_m3_day"] for v in vs)
        rows.append(dict(region=region,demand_exposure_m3_day=demand,essential_m3_day=sum(v["essential_demand_m3_day"] for v in vs),drought_supply_gap_m3_day=max(0,demand-source*severity),existing_interregion_links=0,recovery_capacity="UNKNOWN",risk_probability="NOT_ESTIMATED"))
    st.dataframe(rows)
elif page=="재난 시나리오 제작실":
    st.write("가뭄 배수·기간·시나리오 선택은 왼쪽에서 설정합니다. 고장 링크와 수요 배수를 편집할 수 있습니다.")
    edits=st.session_state.get("scenario_edits",{}).copy()
    for s in scenarios:
        with st.expander(s["id"]):
            failed=st.multiselect("고장 시설",[e["id"] for e in network["edges"]],default=s["failed_edges"],key="failed"+s["id"])
            demand=st.number_input("수요 배수",min_value=.1,max_value=3.,value=float(s["demand_factor"]),key="demand"+s["id"])
            edits[s["id"]]=dict(failed_edges=failed,demand_factor=demand)
    if st.button("시나리오 변경 저장"): st.session_state.scenario_edits=edits; st.rerun()
    st.json(scenarios)
elif page=="사전 대비망 설계 스튜디오":
    st.subheader("후보 연결·증설 옵션")
    st.write("후보는 명시적으로 입력한 추상 연결입니다. 지리적 인접성으로 공급 가능성을 추정하지 않습니다. 미확정 허가는 개념 검토만 허용하며 DENIED는 선택 금지입니다.")
    editable=pd.DataFrame([{k:a[k] for k in ["id","enabled","capacity","capex_estimate","permit_status"]} for a in network["candidates"]])
    edited=st.data_editor(editable,disabled=["id"],hide_index=True)
    if st.button("후보 변경 저장"):
        for row in edited.to_dict("records"):
            next(a for a in network["candidates"] if a["id"]==row["id"]).update(row)
        try: validate_network(network)
        except (ValueError,KeyError) as exc: st.error(str(exc))
        else: st.session_state.candidate_edits=network["candidates"]; st.rerun()
    with st.expander("사용자 후보 링크 추가"):
        with st.form("add_candidate"):
            aid=st.text_input("후보 ID",value="USER-01")
            a=st.selectbox("시작 노드",[v["id"] for v in network["nodes"]]); b=st.selectbox("끝 노드",[v["id"] for v in network["nodes"]],index=1)
            water=st.selectbox("물 유형",["potable","raw"]); cap=st.number_input("설계 용량 m³/일",min_value=0.,value=50.)
            cost=st.number_input("가상 CAPEX 원",min_value=0.,value=200000000.)
            if st.form_submit_button("추상 후보 추가"):
                asset=deepcopy(network["candidates"][0]); asset.update(id=aid,from_node=a,to_node=b,water_type=water,layer="raw_transfer" if water=="raw" else "potable_transfer",capacity=cap,capex_estimate=cost)
                proposal=deepcopy(network); proposal["candidates"].append(asset)
                try: validate_network(proposal)
                except (ValueError,KeyError) as exc: st.error(str(exc))
                else: st.session_state.candidate_edits=proposal["candidates"]; st.rerun()
    if st.button("사전 대비망 설계",type="primary"):
        if not scenarios: st.error("재난 시나리오를 하나 이상 선택하세요.")
        else:
            try:
                with st.spinner("MILP 공통 설계·대안·반사실적 재계산 중…"):
                    bundle=build_preparedness_plan(network,scenarios,budget*1e8)
                st.session_state.bundle=bundle; st.session_state.signature=signature
            except (ValueError,RuntimeError) as exc: st.error(str(exc))
    if bundle:
        st.dataframe([comparison_row(bundle["baseline"])]+[comparison_row(p) for p in bundle["alternatives"]]); st.caption(bundle["alternative_note"])
        st.plotly_chart(network_figure(network,bundle["best"]["selected_assets"]),width="stretch")
elif page=="선정 이유·대안 비교":
    if bundle:
        st.dataframe([comparison_row(p) for p in bundle["alternatives"]])
        for e in bundle["explanations"]:
            with st.expander(e["selected_asset_id"],expanded=True): st.write(e["text"]); st.json(e)
        st.subheader("포화 제약 (인과적 병목으로 단정하지 않음)"); st.dataframe(bundle["bottlenecks"])
        st.caption("미선정 후보는 동일 목적의 상위 대안 조합·예산·허가 조건으로 비교합니다. 개별 인과 이유가 없으면 EXPLANATION_UNAVAILABLE입니다.")
        if st.button("예산 민감도 재계산"):
            rows=[]
            for b in sorted({max(0,budget-2),budget,budget+2}):
                p=optimize_preparedness_design(network,network["candidates"],scenarios,b*1e8); rows.append(dict(budget_1e8=b,**comparison_row(p)))
            st.dataframe(rows)
    else: st.info("설계 스튜디오에서 먼저 계산하세요.")
elif page=="재난 대응 운영 시뮬레이터":
    if bundle:
        idx=st.selectbox("설계 대안",range(len(bundle["alternatives"])),format_func=lambda x:bundle["alternatives"][x]["plan_id"])
        p=bundle["alternatives"][idx]; sid=st.selectbox("운영 시나리오",[o["scenario_id"] for o in p["operations"]])
        op=next(o for o in p["operations"] if o["scenario_id"]==sid)
        st.json({k:v for k,v in op.items() if k!="records"}); st.dataframe(op["records"])
        day=st.slider("계산 일차 (0부터)",0,days-1,0) if days>1 else 0
        st.plotly_chart(network_figure(network,p["selected_assets"],operation=op,day=day),width="stretch")
        st.subheader("개발에 사용하지 않은 극단 시나리오"); st.json({k:v for k,v in bundle["held_out"].items() if k!="records"})
        factor=st.number_input("실측 유입 수율 배수",min_value=0.,max_value=2.,value=.4)
        if st.button("선택 망 고정 후 운영 재최적화"):
            s=deepcopy(next(s for s in scenarios if s["id"]==sid)); s["source_factor"]=factor
            result=optimize_emergency_operations(p,s); st.dataframe(result["records"]); st.json({k:v for k,v in result.items() if k!="records"})
    else: st.info("설계 스튜디오에서 먼저 계산하세요.")
elif page=="대비 계획서·사업 우선순위":
    if bundle:
        st.dataframe(bundle["roadmap"]); st.json(bundle["best"]["limitations"])
        for fmt,mime in [("html","text/html"),("json","application/json"),("csv","text/csv"),("geojson","application/geo+json")]:
            st.download_button(fmt.upper()+" 다운로드",export_preparedness_plan(bundle,fmt),file_name="preparedness-plan."+fmt,mime=mime)
        st.caption("HTML은 그래프 스크립트를 포함한 독립 보고서입니다. 브라우저 인쇄로 PDF 저장할 수 있습니다. GeoJSON은 실제 GIS가 없으므로 geometry=null입니다.")
    else: st.info("설계 스튜디오에서 먼저 계산하세요.")

if bundle:
    p=bundle["best"]; st.divider(); a,b,c=st.columns(3)
    a.metric("선정 사업비 (억원)",f"{p['capex_estimate']/1e8:.1f}"); b.metric("최악 필수 부족 m³/일",f"{p['worst_essential_m3_day']:.3f}"); c.metric("최악 전체 부족 m³/일",f"{p['worst_shortfall_m3_day']:.3f}")
    st.caption(f"{p['status']} · 수리 검증 {p['hydraulic_status']} · 실행 {p['run_id'][:16]}")
