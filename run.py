"""python run.py [--report] [--test]"""
from pathlib import Path
import sys
import subprocess
sys.path.insert(0,str(Path(__file__).parent/"src"))

if __name__=="__main__":
    root=Path(__file__).parent
    if "--report" in sys.argv:
        from resilient_water.demo import load_network,generate_scenarios
        from resilient_water.reporting import build_preparedness_plan,export_preparedness_plan,comparison_row
        import json
        n=load_network(); s=generate_scenarios(); bundle=build_preparedness_plan(n,s,8e8)
        out=root/"reports"; out.mkdir(exist_ok=True)
        for fmt in ["json","csv","html","geojson"]: (out/("preparedness-plan."+fmt)).write_bytes(export_preparedness_plan(bundle,fmt))
        (root/"data/demo/network.json").write_text(json.dumps(n,ensure_ascii=False,indent=2),encoding="utf-8")
        (root/"data/demo/scenarios.json").write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(comparison_row(bundle["best"]),ensure_ascii=False,indent=2))
    elif "--test" in sys.argv:
        raise SystemExit(subprocess.call([sys.executable,"-m","pytest","-q"],cwd=root))
    else:
        from streamlit.web import cli
        sys.argv=["streamlit","run",str(root/"app.py"),"--server.address=127.0.0.1","--server.headless=true","--browser.gatherUsageStats=false","--global.developmentMode=false"]
        raise SystemExit(cli.main())
