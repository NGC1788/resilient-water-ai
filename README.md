# RESILIENT WATER AI — v0.2 (수정·강화본)

재난 **이전**에 내릴 결정(신규 연결·증설 투자, 재난 인지 전 공통 운영·비축)과 재난 **이후** 시나리오별 배분 계획을 함께 계산하는 한국어 의사결정 지원 소프트웨어입니다. 세 가지 배분 정책(비례 / 단일 예측 / 다중 시나리오 강건)을 같은 조건에서 비교하고, 팀의 **중력식 탁상 축소모형**에 보낼 라인별 목표량을 만듭니다. 모든 결과는 SciPy/HiGHS MILP 계산에서 나옵니다.

> **전부 가상 데이터 또는 보정 전 탁상 모형 가정값이며 개념 설계입니다.** 실제 인프라·GIS 노선·공사비·인허가·수압 검증 결과가 아닙니다. 물량 보존 검증은 `GRAPH_FEASIBLE`, 물리 모델은 `CONCEPTUAL_ONLY`, 수리 검증은 `NOT_VALIDATED`로 구분합니다. 이 소프트웨어는 장치를 직접 제어하지 않습니다.

원본: [snowy-applicant/resilient-water-ai](https://github.com/snowy-applicant/resilient-water-ai) (v0.1, 커밋 이력 보존). 이 저장소는 v0.1을 검증해서 발견한 문제를 고치고 기능을 보강한 버전입니다. 무엇을 왜 바꿨는지는 [`docs/CHANGES_v0.2.md`](docs/CHANGES_v0.2.md)에 있습니다.

## 실행

Python 3.12 이상에서:

```sh
python -m pip install -e ".[test]"
python run.py
```

브라우저에서 표시된 로컬 주소를 열고, 왼쪽에서 **데이터**(가상 3권역 / 하드웨어 축소모형)를 고른 뒤 **사전 대비망 설계 스튜디오 → 사전 대비망 설계**를 누릅니다.

```sh
python run.py --test       # 자동 검증 (44개)
python run.py --report     # 기본 입력으로 실제 계산 후 reports/ 와 reports/hardware/ 생성
```

검증한 환경: Python 3.12, SciPy 1.18.1, Streamlit 1.66.0 (macOS arm64).

## 무엇을 계산하나

| 결정 | 언제 | 모든 시나리오 공통? |
|---|---|---|
| 후보 관로 투자 `y` (0/1) | 재난 전 | 예 |
| 재난 인지 전 `prep_days`일의 취수·송수·배수지 비축 | 재난 전 | 예 (**비예견성**: 어떤 재난이 올지 모르는 상태) |
| 재난 일수 동안의 취수·송수·공급·부족·저장 | 재난 후 | 아니요 (시나리오별 재최적화) |

목표는 순서대로(사전식) 풉니다. ① 재난 전 정상 공급 → ② 최악 시나리오 필수 부족 → ③ 최악 전체 부족 → ④ **최악 지역 부족률(형평성)** → ⑤ 필수 부족 합계 → ⑥ 전체 부족 합계 → ⑦ 투자비 → ⑧ **남는 저장량 최대화** → ⑨ 운영비. 자세한 수식은 [`docs/optimization_model.md`](docs/optimization_model.md)에 있습니다.

## 화면 (11개)

권역 개요 · 데이터 카탈로그·품질 · 현재 공급망 · 위험 지역 분석 · 재난 시나리오 제작실 · 사전 대비망 설계 스튜디오 · 선정 이유·대안 비교 · **배분 정책 비교 (A/B/C)** · 재난 대응 운영 시뮬레이터 · **하드웨어 모형 연동** · 대비 계획서·사업 우선순위

## 배분 정책 비교 (인계 문서 §6.3)

| 정책 | 재난 전 | 재난 후 |
|---|---|---|
| A 비례 배분 | 평상 수위 유지, 비축 없음 | 매일 그날 정보만으로 모든 지역에 같은 비율(도달 가능한 범위의 max-min 공정 배분), 필수 우선 없음 |
| B 단일 예측 최적화 | NORMAL 하나만 예상하고 운영비 최소화 | C와 같은 재최적화 |
| C 다중 시나리오 강건 | 모든 개발 시나리오의 최악값 기준 | 재최적화 |

B와 C는 재난 전 운영만 다르므로 차이는 곧 "강건한 사전 대비의 가치"입니다. 개발에 쓰지 않은 held-out 시나리오로도 따로 평가합니다. 기본 입력의 실제 계산 결과는 `python run.py --report` 출력과 `reports/preparedness-plan.html`에서 확인하세요. 축소모형은 시나리오마다 시작 수위가 달라 `prep_days=0`이므로 B와 C가 같게 나오는 것이 정상입니다.

## 하드웨어 축소모형 연동

`data/hardware/gravity_twin_network.json`은 팀 하드웨어 설계(수원 페트병 A·B, 주거1·병원·주거2 컵, 서보 라인 R1·R3A·R3B·R2, 1주기=60초, 단위 mL)를 그대로 옮긴 망입니다. 관로 용량 300 mL/주기는 **보정 전 가정값**이므로 서보 보정표가 나오면 바꿔야 합니다.

- 계획 → 라인별 목표량: `operation_to_schedule`, JSONL 내보내기(`requires_approval=true`)
- 측정 CSV(`cycle,node_id,measured_ml`) → 계획 대비 오차: `compare_measurements`
- A↔B 연결관은 중력 연통관이라 실제로는 유량을 제어할 수 없습니다. 계획에 이 관 유량이 나오면 `manual_link`로 표시합니다.

자세한 내용은 [`docs/hardware_interface.md`](docs/hardware_interface.md)에 있습니다.

## 구조

`src/resilient_water/engine.py` — 2단계 강건 MILP / 비예견성 / 독립 검증 / 반사실적 재계산 / 열등안 제외
`policies.py` — A·B·C 배분 정책 비교
`hardware.py` — 축소모형 망 불러오기·목표량 내보내기·측정 비교
`demo.py` — 가상 3권역 데이터·시나리오 생성
`quality.py` — 망·시나리오 입력 검사 (잘못된 입력은 항상 ValueError)
`reporting.py` — 대안·설명·시각화·내보내기
`app.py` — 한국어 Streamlit 11개 화면
`tests/` — 자동 검증 44개
`data/demo/` — 가상 입력 스냅샷(출력 기록용) · `data/hardware/` — 축소모형 입력(실제로 읽음)

## 범위와 남은 작업

최상위 요구사항은 [`docs/RESILIENT_WATER_CODEX_SPEC_V2.md`](docs/RESILIENT_WATER_CODEX_SPEC_V2.md)입니다. 아직 없는 것: WNTR/EPANET 수압 검증, 실제 자료 수집·FAIR 카탈로그 DB, 학습 예측, 재난 중 매일 다시 계산하는 MPC, 저장·펌프 증설 후보, 위험 점수(R_i), 장치 실시간 제어(하드웨어 PC 제어기 몫). 자세한 내용은 [`docs/validation.md`](docs/validation.md)와 [`docs/assumptions_and_limitations.md`](docs/assumptions_and_limitations.md)에 있습니다.

허가 `UNCONFIRMED`는 건설 허용을 뜻하지 않습니다. `DENIED`는 투자 선정 금지입니다. 예산과 성능이 같아도 최적 유량 해는 여러 개일 수 있습니다. 실행 ID는 입력·알고리즘의 식별자이며 실측 검증의 증명이 아닙니다.
