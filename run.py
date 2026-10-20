"""python run.py [--report] [--test]"""
from pathlib import Path
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))


def write_reports(root):
    from resilient_water.demo import load_network, generate_scenarios
    from resilient_water.hardware import load_twin, operation_to_schedule, schedule_to_jsonl
    from resilient_water.reporting import build_preparedness_plan, export_preparedness_plan, comparison_row, policy_rows

    n = load_network()
    s = generate_scenarios()
    bundle = build_preparedness_plan(n, s, 8e8)
    out = root / "reports"
    out.mkdir(exist_ok=True)
    for fmt in ["json", "csv", "html", "geojson"]:
        (out / ("preparedness-plan." + fmt)).write_bytes(export_preparedness_plan(bundle, fmt))
    (root / "data/demo/network.json").write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "data/demo/scenarios.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

    tn, ts, th = load_twin()
    twin = build_preparedness_plan(tn, ts, 0, held_out=th)
    hw = out / "hardware"
    hw.mkdir(exist_ok=True)
    for fmt in ["json", "csv", "html"]:
        (hw / ("twin-plan." + fmt)).write_bytes(export_preparedness_plan(twin, fmt))
    for r in twin["policies"]["rows"]:
        op = r["operation"]
        name = f"targets-{r['policy']}-{op['scenario_id']}.jsonl"
        (hw / name).write_text(schedule_to_jsonl(operation_to_schedule(tn, op), f"{twin['best']['plan_id']}-{r['policy']}"),
                               encoding="utf-8")
    print(json.dumps(dict(synthetic=comparison_row(bundle["best"]), synthetic_policies=policy_rows(bundle["policies"]),
                          twin_policies=policy_rows(twin["policies"])), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    root = Path(__file__).parent
    if "--report" in sys.argv:
        write_reports(root)
    elif "--test" in sys.argv:
        raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=root))
    else:
        from streamlit.web import cli
        sys.argv = ["streamlit", "run", str(root / "app.py"), "--server.address=127.0.0.1", "--server.headless=true",
                    "--browser.gatherUsageStats=false", "--global.developmentMode=false"]
        raise SystemExit(cli.main())
