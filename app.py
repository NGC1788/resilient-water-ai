from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))
from copy import deepcopy
import math
import pandas as pd
import streamlit as st
from resilient_water.demo import load_network, generate_scenarios, held_out_scenario
from resilient_water.engine import digest, optimize_preparedness_design, optimize_emergency_operations, SolverFailure
from resilient_water.reporting import (build_preparedness_plan, network_figure, comparison_row, export_preparedness_plan,
                                       policy_figure, policy_rows, units)
from resilient_water.quality import validate_network
from resilient_water.policies import compare_policies
from resilient_water.hardware import (load_twin, operation_to_schedule, schedule_to_jsonl, read_measurements,
                                      compare_measurements)

ERRORS = (ValueError, KeyError, TypeError, RuntimeError)
PAGES = ["권역 개요", "데이터 카탈로그·품질", "현재 공급망", "위험 지역 분석", "재난 시나리오 제작실", "사전 대비망 설계 스튜디오",
         "선정 이유·대안 비교", "배분 정책 비교 (A/B/C)", "재난 대응 운영 시뮬레이터", "하드웨어 모형 연동", "대비 계획서·사업 우선순위"]
DATASETS = {"synthetic": "가상 3권역 (합성 데이터)", "twin": "하드웨어 축소모형 (중력식 탁상 실험)"}

st.set_page_config(page_title="RESILIENT WATER · 사전 대비망", page_icon="💧", layout="wide")
st.title("💧 RESILIENT WATER")
st.caption("재난 전 공통 투자·비축 결정 → 시나리오별 비상 배분 → 배분 정책 비교 → 계산에 근거한 선택 이유")

with st.sidebar:
    dataset = st.radio("데이터", list(DATASETS), format_func=DATASETS.get)
    page = st.radio("화면", PAGES)
    st.header("사전 대비 설계 조건")
    if dataset == "synthetic":
        network = load_network()
        days = st.slider("재난 분석 기간 (일)", 1, 14, 3)
        severity = st.slider("가뭄 공급원 수율 배수", 0., 1., .6, .05)
        base_scenarios = generate_scenarios(days, severity)
        held_out = [held_out_scenario(days)]
        prep_days = st.slider("재난 인지 전 공통 운영 기간 (일, 비예견성)", 0, 3, 1)
        budget = st.slider("투자 예산 (억원, 가상 추정)", 0, 20, 8)
    else:
        network, base_scenarios, held_out = load_twin()
        days = base_scenarios[0]["days"]
        prep_days = 0
        budget = 0
        st.caption("축소모형은 탁상 실험 상태(수원 수위)가 시나리오마다 달라 사전 공통 기간 0, 투자 후보 없음.")
    allow_rationing = st.checkbox("사전 기간 제한급수(헤징) 허용", value=False, disabled=prep_days == 0)
    regions = sorted({v["region_id"] for v in network["nodes"] if v["type"] == "demand"})
    protected = st.multiselect("보호 권역", regions, default=regions)
    chosen = st.multiselect("설계에 사용할 재난", [s["id"] for s in base_scenarios], default=[s["id"] for s in base_scenarios])
    essential_scale = st.slider("필수 보호 수요 배수", 0.5, 1.2, 1., .1)
    st.caption("모든 시나리오에서 공통인 사전 결정 · 발생 확률 미사용")

vol, per = units(network)
# Unprotected region demand remains ordinary demand but receives no essential-priority quota.
for v in network["nodes"]:
    v["essential_demand_m3_day"] = min(v["demand_m3_day"], v["essential_demand_m3_day"] * essential_scale) if v["region_id"] in protected else 0
edits_key = f"candidate_edits_{dataset}"
if edits_key in st.session_state:
    network["candidates"] = deepcopy(st.session_state[edits_key])
scenarios = [s for s in base_scenarios if s["id"] in chosen]
scenario_edits = st.session_state.get(f"scenario_edits_{dataset}", {})
for s in scenarios:
    if s["id"] in scenario_edits:
        s.update(scenario_edits[s["id"]])
settings = dict(network=network, scenarios=scenarios, budget=budget, prep_days=prep_days, rationing=allow_rationing, dataset=dataset)
signature = digest(settings)
bundle = st.session_state.get("bundle")
if bundle and st.session_state.get("signature") != signature:
    st.info("입력이 변경되었습니다. 새 조건으로 사전 대비망 설계를 실행하세요.")
    bundle = None
if dataset == "synthetic":
    st.warning("SYNTHETIC DEMO · 가상 3개 권역의 개념 설계입니다. 실제 지리·노선·시공비·수압 검증 자료가 아닙니다.")
else:
    st.warning("탁상 축소모형 · 관로 용량은 보정 전 가정값입니다. 실제 수도시설을 제어하지 않습니다.")


def run_design():
    return build_preparedness_plan(network, scenarios, budget * 1e8, held_out=held_out, prep_days=prep_days,
                                   allow_prep_rationing=allow_rationing)


if page == "권역 개요":
    st.subheader(network.get("title", ""))
    st.write(f"노드 {len(network['nodes'])}개, 기존 링크 {len(network['edges'])}개, 후보 링크 {len(network['candidates'])}개. "
             f"단위: {vol}/{per}. 권역 선택은 필수 보호 우선순위에 반영됩니다.")
    st.plotly_chart(network_figure(network, bundle["best"]["selected_assets"] if bundle else []), width="stretch")
elif page == "데이터 카탈로그·품질":
    st.json(network["catalog"])
    st.dataframe(network["nodes"])
    st.json({"input_hash": digest(network), "schema": "docs/data_dictionary.md", "crs": network["coordinate_status"]})
elif page == "현재 공급망":
    all_layers = sorted({e["layer"] for e in network["edges"] + network["candidates"]})
    layers = st.multiselect("레이어", all_layers, default=all_layers)
    st.plotly_chart(network_figure(network, layers=layers), width="stretch")
    st.dataframe(network["edges"])
elif page == "위험 지역 분석":
    st.write("규칙 기반 구성 지표입니다. 발생 확률 또는 인과 효과 추정이 아닙니다.")
    nodes = {v["id"]: v for v in network["nodes"]}
    links = network["edges"] + network["candidates"]
    rows = []
    for region in regions:
        vs = [v for v in network["nodes"] if v["region_id"] == region]
        demand = sum(v["demand_m3_day"] for v in vs)
        source = sum(v["source_yield_m3_day"] for v in vs)
        storage = sum(v["storage_m3"] - v["storage_min_m3"] for v in vs)
        inbound = [e for e in links if nodes[e["to_node"]]["region_id"] == region and nodes[e["from_node"]]["region_id"] != region]
        rows.append(dict(region=region, demand_exposure=demand, essential=sum(v["essential_demand_m3_day"] for v in vs),
                         local_source=source, usable_storage=storage,
                         storage_days=storage / demand if demand else None,
                         existing_interregion_links=sum(e["existing"] for e in inbound),
                         candidate_interregion_links=sum(not e["existing"] for e in inbound),
                         risk_probability="NOT_ESTIMATED"))
    st.dataframe(rows)
elif page == "재난 시나리오 제작실":
    st.write("고장 링크와 수요 배수를 편집할 수 있습니다.")
    edits = dict(scenario_edits)
    for s in scenarios:
        with st.expander(s["id"]):
            failed = st.multiselect("고장 시설", [e["id"] for e in network["edges"] + network["candidates"]],
                                    default=s["failed_edges"], key="failed" + dataset + s["id"])
            demand = st.number_input("수요 배수", min_value=.1, max_value=3., value=float(s["demand_factor"]), key="demand" + dataset + s["id"])
            edits[s["id"]] = dict(failed_edges=failed, demand_factor=demand)
    if st.button("시나리오 변경 저장"):
        st.session_state[f"scenario_edits_{dataset}"] = edits
        st.rerun()
    st.json(scenarios)
elif page == "사전 대비망 설계 스튜디오":
    if network["candidates"]:
        st.subheader("후보 연결·증설 옵션")
        st.write("후보는 명시적으로 입력한 추상 연결입니다. 미확정 허가는 개념 검토만 허용하며 DENIED는 선택 금지입니다.")
        editable = pd.DataFrame([{k: a[k] for k in ["id", "enabled", "capacity", "capex_estimate", "permit_status"]} for a in network["candidates"]])
        edited = st.data_editor(editable, disabled=["id"], hide_index=True)
        if st.button("후보 변경 저장"):
            proposal = deepcopy(network)
            for row in edited.to_dict("records"):
                clean = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}
                next(a for a in proposal["candidates"] if a["id"] == row["id"]).update(clean)
            try:
                validate_network(proposal)
            except ERRORS as exc:
                st.error(f"입력 오류: {exc}")
            else:
                st.session_state[edits_key] = proposal["candidates"]
                st.rerun()
    if dataset == "synthetic":
        with st.expander("사용자 후보 링크 추가"):
            with st.form("add_candidate"):
                aid = st.text_input("후보 ID", value="USER-01")
                a = st.selectbox("시작 노드", [v["id"] for v in network["nodes"]])
                b = st.selectbox("끝 노드", [v["id"] for v in network["nodes"]], index=1)
                water = st.selectbox("물 유형", ["potable", "raw"])
                cap = st.number_input(f"설계 용량 {vol}/{per}", min_value=0., value=50.)
                cost = st.number_input("가상 CAPEX 원", min_value=0., value=200000000.)
                if st.form_submit_button("추상 후보 추가"):
                    asset = deepcopy(network["candidates"][0]) if network["candidates"] else {}
                    asset.update(id=aid, from_node=a, to_node=b, water_type=water, layer="raw_transfer" if water == "raw" else "potable_transfer",
                                 capacity=cap, capex_estimate=cost, existing=False, cost_opex_per_m3=40.0, enabled=True)
                    proposal = deepcopy(network)
                    proposal["candidates"].append(asset)
                    try:
                        validate_network(proposal)
                    except ERRORS as exc:
                        st.error(f"입력 오류: {exc}")
                    else:
                        st.session_state[edits_key] = proposal["candidates"]
                        st.rerun()
    if st.button("사전 대비망 설계", type="primary"):
        if not scenarios:
            st.error("재난 시나리오를 하나 이상 선택하세요.")
        else:
            try:
                with st.spinner("MILP 공통 설계·대안·반사실적 재계산·정책 비교 중…"):
                    bundle = run_design()
                st.session_state.bundle = bundle
                st.session_state.signature = signature
            except ERRORS as exc:
                st.error(f"계산 실패: {exc}")
    if bundle:
        st.dataframe([comparison_row(bundle["baseline"])] + [comparison_row(p) for p in bundle["alternatives"]])
        st.caption(bundle["alternative_note"])
        if bundle["dominated"]:
            st.write("제외된 열등안(앞선 대안보다 같거나 나쁘고 더 비쌈)")
            st.dataframe(bundle["dominated"])
        st.plotly_chart(network_figure(network, bundle["best"]["selected_assets"]), width="stretch")
elif page == "선정 이유·대안 비교":
    if bundle:
        st.dataframe([comparison_row(p) for p in bundle["alternatives"]])
        if not bundle["explanations"]:
            st.info("선정된 투자 시설이 없습니다.")
        for e in bundle["explanations"]:
            with st.expander(e["selected_asset_id"], expanded=True):
                st.write(e["text"])
                st.json(e)
        st.subheader("포화 제약 (인과적 병목으로 단정하지 않음)")
        st.dataframe(bundle["bottlenecks"])
        if network["candidates"] and st.button("예산 민감도 재계산"):
            rows = []
            for b in sorted({max(0, budget - 2), budget, budget + 2}):
                try:
                    p = optimize_preparedness_design(network, network["candidates"], scenarios, b * 1e8, prep_days=prep_days,
                                                     allow_prep_rationing=allow_rationing)
                    rows.append(dict(budget_1e8=b, **comparison_row(p)))
                except SolverFailure as exc:
                    rows.append(dict(budget_1e8=b, status=f"실패: {exc}"))
            st.dataframe(rows)
    else:
        st.info("설계 스튜디오에서 먼저 계산하세요.")
elif page == "배분 정책 비교 (A/B/C)":
    st.write("A 비례 배분(근시안·사전 비축 없음), B 단일 예측 최적화(효율 중심), C 다중 시나리오 강건 최적화를 "
             "같은 설계망·같은 시나리오·같은 지표로 비교합니다. held-out 시나리오는 B·C 계산에 쓰지 않았습니다.")
    if bundle and bundle.get("policies"):
        comp = bundle["policies"]
        st.caption(comp["note"])
        st.dataframe(policy_rows(comp))
        st.plotly_chart(policy_figure(comp), width="stretch")
        st.plotly_chart(policy_figure(comp, "worst_regional_spread", "정책별 최악 지역 부족률 편차 (낮을수록 형평)"), width="stretch")
        detail = [dict(policy=r["policy"], set=r["scenario_set"], scenario=r["operation"]["scenario_id"],
                       essential_satisfaction=r["operation"]["essential_satisfaction"],
                       supply_satisfaction=r["operation"]["supply_satisfaction"],
                       regional_shortfall_ratio=str({k: round(v, 3) for k, v in r["operation"]["regional_shortfall_ratio"].items()}),
                       storage_after_prep=sum(r["operation"]["storage_after_prep_m3"].values()),
                       opex_KRW=r["operation"]["opex_KRW"], validated=r["operation"]["validation"]["passed"]) for r in comp["rows"]]
        st.dataframe(detail)
    else:
        st.info("설계 스튜디오에서 먼저 계산하세요.")
elif page == "재난 대응 운영 시뮬레이터":
    if bundle:
        idx = st.selectbox("설계 대안", range(len(bundle["alternatives"])), format_func=lambda x: bundle["alternatives"][x]["plan_id"])
        p = bundle["alternatives"][idx]
        sid = st.selectbox("운영 시나리오", [o["scenario_id"] for o in p["operations"]])
        op = next(o for o in p["operations"] if o["scenario_id"] == sid)
        st.json({k: v for k, v in op.items() if k != "records"})
        st.dataframe(op["records"])
        horizon = op["horizon_days"]
        day = st.slider("계산 일차 (0부터)", 0, horizon - 1, 0) if horizon > 1 else 0
        st.plotly_chart(network_figure(network, p["selected_assets"], operation=op, day=day), width="stretch")
        st.subheader("개발에 사용하지 않은 극단 시나리오")
        for h in bundle["held_out_all"]:
            st.json({k: v for k, v in h.items() if k != "records"})
        if dataset == "synthetic":
            factor = st.number_input("실측 유입 수율 배수", min_value=0., max_value=2., value=.4)
            if st.button("선택 망·사전 운영 고정 후 운영 재최적화"):
                s = deepcopy(next(s for s in scenarios if s["id"] == sid))
                s["source_factor"] = factor
                try:
                    result = optimize_emergency_operations(p, s)
                    st.dataframe(result["records"])
                    st.json({k: v for k, v in result.items() if k != "records"})
                except ERRORS as exc:
                    st.error(f"계산 실패: {exc}")
    else:
        st.info("설계 스튜디오에서 먼저 계산하세요.")
elif page == "하드웨어 모형 연동":
    st.write("계산된 배분을 탁상 모형의 서보 라인별 목표량(mL/주기)으로 바꿉니다. 이 프로그램은 장치를 직접 제어하지 않으며, "
             "하드웨어 PC 제어기가 운영자 승인 후 실행합니다. 측정 CSV(cycle,node_id,measured_ml)를 올리면 계획 대비 오차를 계산합니다.")
    if dataset != "twin":
        st.info("왼쪽에서 '하드웨어 축소모형' 데이터를 선택하세요.")
    elif not bundle or not bundle.get("policies"):
        st.info("설계 스튜디오에서 먼저 계산하세요.")
    else:
        comp = bundle["policies"]
        policy = st.selectbox("정책", sorted({r["policy"] for r in comp["rows"]}))
        sid = st.selectbox("시나리오", [r["operation"]["scenario_id"] for r in comp["rows"] if r["policy"] == policy])
        op = next(r["operation"] for r in comp["rows"] if r["policy"] == policy and r["operation"]["scenario_id"] == sid)
        schedule = operation_to_schedule(network, op)
        st.dataframe(schedule)
        st.download_button("명령 목표 JSONL 다운로드", schedule_to_jsonl(schedule, f"{bundle['best']['plan_id']}-{policy}-{sid}"),
                           file_name=f"targets-{policy}-{sid}.jsonl", mime="application/x-ndjson")
        upload = st.file_uploader("측정 CSV", type=["csv"])
        if upload:
            try:
                result = compare_measurements(network, op, read_measurements(upload.getvalue().decode("utf-8-sig")))
                st.json({k: v for k, v in result.items() if k != "rows"})
                st.dataframe(result["rows"])
            except ERRORS as exc:
                st.error(f"측정 파일 오류: {exc}")
elif page == "대비 계획서·사업 우선순위":
    if bundle:
        st.dataframe(bundle["roadmap"])
        st.json(bundle["best"]["limitations"])
        for fmt, mime in [("html", "text/html"), ("json", "application/json"), ("csv", "text/csv"), ("geojson", "application/geo+json")]:
            st.download_button(fmt.upper() + " 다운로드", export_preparedness_plan(bundle, fmt), file_name="preparedness-plan." + fmt, mime=mime)
        st.caption("HTML은 그래프 스크립트를 포함한 독립 보고서입니다. 브라우저 인쇄로 PDF 저장할 수 있습니다. GeoJSON은 실제 GIS가 없으므로 geometry=null입니다.")
    else:
        st.info("설계 스튜디오에서 먼저 계산하세요.")

if bundle:
    p = bundle["best"]
    st.divider()
    a, b, c, d = st.columns(4)
    a.metric("선정 사업비 (억원)", f"{p['capex_estimate'] / 1e8:.1f}")
    b.metric(f"최악 필수 부족 {vol}/{per}", f"{p['worst_essential_m3_day']:.3f}")
    c.metric(f"최악 전체 부족 {vol}/{per}", f"{p['worst_shortfall_m3_day']:.3f}")
    d.metric("최악 지역 부족률", f"{(p['worst_regional_shortfall_ratio'] or 0):.3f}")
    st.caption(f"{p['status']} · 수리 검증 {p['hydraulic_status']} · 사전 공통 기간 {p['prep_days']}일 · 실행 {p['run_id'][:16]}")
