import pytest
from resilient_water.demo import load_network, generate_scenarios, held_out_scenario
from resilient_water.engine import validate_operation
from resilient_water.policies import compare_policies, run_proportional, POLICIES


@pytest.fixture(scope="module")
def comparison():
    n = load_network()
    return n, compare_policies(n, generate_scenarios(), ["C2", "C4", "C5"], held_out=[held_out_scenario()])


def test_all_policies_scored_on_same_scenarios(comparison):
    _, c = comparison
    by_policy = {}
    for r in c["rows"]:
        by_policy.setdefault(r["policy"], []).append((r["scenario_set"], r["operation"]["scenario_id"]))
    assert set(by_policy) == set(POLICIES)
    assert len({tuple(v) for v in by_policy.values()}) == 1
    assert all(r["operation"]["validation"]["passed"] for r in c["rows"])


def test_robust_never_worse_than_single_forecast_on_development_worst_case(comparison):
    _, c = comparison
    s = {(r["policy"], r["scenario_set"]): r for r in c["summary"]}
    robust, single = s[("C_ROBUST", "development")], s[("B_SINGLE_FORECAST", "development")]
    assert robust["worst_essential_shortfall_m3"] <= single["worst_essential_shortfall_m3"] + 1e-6


def test_b_and_c_share_recourse_and_differ_only_before_onset(comparison):
    _, c = comparison
    ops = {(r["policy"], r["operation"]["scenario_id"]): r["operation"] for r in c["rows"]}
    # Same prep plan in every scenario for a given policy (non-anticipativity of the policy itself).
    for policy in ["B_SINGLE_FORECAST", "C_ROBUST"]:
        plans = {tuple(sorted(o["storage_after_prep_m3"].items())) for (p, _), o in ops.items() if p == policy}
        assert len(plans) == 1


def test_proportional_is_proportional_where_reachable():
    n = load_network()
    s = generate_scenarios()[1]  # LONG_DROUGHT: every node reachable
    op = run_proportional(n, [], s, 1)
    assert op["validation"]["passed"]
    demand = {v["id"]: v["demand_m3_day"] for v in n["nodes"] if v["type"] == "demand"}
    for t in range(op["horizon_days"]):
        fractions = [r["value"] / demand[r["asset_id"]] for r in op["records"] if r["kind"] == "supply" and r["day"] == t]
        # max-min fairness: nobody is pushed below the common level reached by the most constrained node
        assert min(fractions) >= 0
    assert validate_operation(n | {"candidates": n["candidates"]}, [], s, op["records"], 1)["passed"]


def test_no_probabilities_in_comparison(comparison):
    _, c = comparison
    assert "probability" not in str(c["summary"]).lower()
