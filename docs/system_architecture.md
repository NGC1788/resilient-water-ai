# 시스템 구조

한국어 Streamlit UI → 합성/축소모형 입력·스키마 검증 → 공동 투자·사전 운영 MILP(비예견성) → 시나리오 운영 records → 독립 물량 검사 → 대안 재계산/삭제 실험 → 근거 연결 한국어 설명 → A/B/C 배분 정책 비교 → JSON/CSV/HTML/GeoJSON → (축소모형) 라인별 목표량 JSONL·측정 비교.

주요 공개 함수는 load_network, generate_scenarios, validate_data, optimize_preparedness_design, optimize_emergency_operations, validate_operation, counterfactual_without_asset, build_preparedness_plan, export_preparedness_plan이다. 후보는 명시적 가상 옵션 생성/사용자 추가로 제공한다. 함수와 결과는 분리되어 있어 실제 자료·시설 계층을 추가할 수 있으나 국가 규모 성능을 검증하지 않았다.

DB 없이 버전 입력 JSON과 실행 JSON을 보존하는 오프라인 P0 구조다. UI 입력 해시가 변경되면 이전 결과를 숨기고 재계산을 요구한다. 결과는 사용자 요청 전에 물리 시설로 보내지 않으며 제어 시스템이 없다.

향후 TelemetryDTO: asset_id, observed_at, quantity, unit, quality, source_id. ProposedControlDTO: proposal_id, plan_id, asset_id, intended_action, interlocks, human_approval. v0.2는 목표량 내보내기(`hardware.py`)와 측정 비교만 제공하며 송수신·서보 제어 구현은 없다(하드웨어 PC 제어기 몫).
