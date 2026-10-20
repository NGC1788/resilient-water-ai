# 인수 기준·검증 (v0.2)

실행: `python run.py --test`. 계산 재생성: `python run.py --report`.

|명세 요구|구현/검사|
|---|---|
|3개 권역·3개 수원·6개 수요·신규 후보 4개 이상|demo.py / 스냅샷|
|정상·가뭄·파손·복합|generate_scenarios|
|공통 투자 시설 선택 MILP와 무투자 비교|engine.py, baseline|
|운영·최악·필수 부족|operations 및 독립 validator|
|선정/미선정 지도 구분|Plotly 논리 지도, 원본 없는 실 GIS 미표시 검사|
|삭제 후 반사실적 재최적화|반복 solve, 금지 후보 불포함·동일 입력 재현 검사|
|상위 3개 대안|투자 조합 no-good cuts, 서로 다른 조합 검사|
|HTML/JSON/CSV 보고서|실제 계산 내보내기 검사, GeoJSON 추가|
|원본 보존·0mm·음수 격리|품질 검사, 극단 양수 보존|
|계층 혼합 금지·선정 전 후보 흐름 금지|스키마 검사 및 유량 검사|
|예산·용량·저장·흐름보존|출력 records 독립 검증|
|극단 필수 부족 정확성|실제 공급으로 부족 재산출|
|설명 수치 run_id 연결|문구 값·evidence_refs 검사|
|수리 검증 성공 오표시 금지|GRAPH_FEASIBLE / NOT_VALIDATED 검사|
|시나리오 변경 재계산·seed 재현|입력 해시·성능 변화 및 재현 검사|
|미래 데이터 누출|학습 예측 비적용; 가정만 사용하는 결정적 시나리오|
|긴 분석 기간(1·7·14·30일) 계산 성공|상대 허용오차, test_long_horizons_solve|
|정상 시나리오 부족 0, 남는 저장량 보존|합성 데이터 v2·저장량 단계, test_normal_scenario_has_no_shortfall·test_unused_storage_is_kept|
|비예견성(재난 전 운영 시나리오 공통)|비예견성 행·gap 검사, test_prep_days_are_non_anticipative…|
|형평성|최악 지역 부족률 단계, test_equity_stage…|
|열등안 제외|test_dominated_superset_is_not_an_alternative|
|비례·단일예측·강건 정책 비교, held-out 평가|tests/test_policies.py|
|하드웨어 축소모형 망·목표량·측정 비교|tests/test_hardware.py|
|11개 화면, 두 데이터셋에서 설계 실행|tests/test_ui.py|

수리 압력·현장 건설 타당성은 검증하지 않았다. 부족을 허용하는 네트워크 물량 해의 GRAPH_FEASIBLE은 필수 수요의 완전 충족을 의미하지 않는다. 부족량을 별도 수치로 공개한다. 솔버 실패/시간초과는 성공 결과로 변환하지 않는다.
