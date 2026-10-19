from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_nine_screens_and_real_design():
    at=AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=60).run()
    assert not at.exception
    pages=list(at.sidebar.radio[0].options)
    assert len(pages)==9
    at.sidebar.radio[0].set_value("사전 대비망 설계 스튜디오").run()
    assert not at.exception
    button=next(b for b in at.button if b.label=="사전 대비망 설계")
    button.click().run(timeout=60)
    assert not at.exception
    assert at.session_state["bundle"]["best"]["solver_status"]=="OPTIMAL"
    for page in pages:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
