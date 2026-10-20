# 데이터 사전

데이터 스냅샷: `data/demo/network.json`, `scenarios.json`. 버전 synthetic-v2 / stress-v2. 하드웨어 축소모형: `data/hardware/gravity_twin_network.json`, `gravity_twin_scenarios.json`(hardware-twin-v1, 단위 mL/주기). 길이·관경·거칠기·실제 노선 및 수압 자료는 현재 스키마에 포함하지 않으며 해당 검증을 주장하지 않는다.

|객체|주요 필드|단위/의미|
|---|---|---|
|node|id, type, water_type, region_id, basin_id|고유 시설·물 유형·권역/유역|
|node|x,y,crs,elevation_m|가상 로컬 개념 좌표 / 가상 고도 m|
|node|source_yield_m3_day,treatment_capacity_m3_day|일별 취수·처리 상한 m³/일|
|node|demand_m3_day,essential_demand_m3_day|전체·필수 수요 m³/일|
|node|storage_m3,storage_min_m3,storage_max_m3|초기·최소·최대 저장량 m³|
|edge|id,from_node,to_node,layer,water_type,existing|방향성 연결·계층|
|edge|capacity,cost_opex_per_m3|m³/일, 원/m³ (가상)|
|candidate|capex_estimate,asset_type,enabled|가상 원, 시설 종류, 활성 여부|
|candidate|permit_status,environmental_status,exclusive_group|DENIED 금지, 배타 그룹|
|candidate|route_geometry,route_source,validity_level|실 GIS 없음, ABSTRACT_NODE_LINK, PRELIMINARY_CANDIDATE|
|candidate|capex_uncertainty,lead_time_months,cost_basis|±50% 가정, 개월, 가상 옵션별 비용 근거|
|scenario|days,source_factor,demand_factor,failed_edges,failed_sources|재난 일수·배수·고장 시설(재난 인지 후에만 적용)|
|scenario|capacity_factors, initial_storage|링크별 용량 배수(0~2), 배수지별 시작 저장량(prep_days=0일 때만)|
|network|default_prep_days, units, title|재난 인지 전 공통 기간 기본값, 표시 단위, 그림 제목|
|edge|hardware_line|축소모형 서보 라인 ID(R1·R3A·R3B·R2)|
|operation.records|day,phase,kind,asset_id,value|flow/source/supply/shortfall/essential_shortfall은 일당 m³; storage는 일말 m³; phase=prep(재난 인지 전)/event|
|portfolio|run_id,input_hash,scenario_hash,algorithm_version|입력 기반 SHA-256 및 코드 알고리즘 버전|

외부 명세의 node_id/edge_id/candidate_id는 구현에서 일관된 `id`, capacity_m3_day/proposed_capacity_m3_day는 `capacity`로 단축했다. 후보 노선은 geometry=null이며 실제 좌표로 오인될 GeoJSON을 만들지 않는다. API는 Python 공개 함수이며 서버 REST API는 미구현이다.
