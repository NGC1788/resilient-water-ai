from pathlib import Path
from streamlit.testing.v1 import AppTest


def _walk(dataset):
    at = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=120).run()
    assert not at.exception
    at.sidebar.radio[0].set_value(dataset).run()
    assert not at.exception
    pages = list(at.sidebar.radio[1].options)
    assert len(pages) == 11
    at.sidebar.radio[1].set_value("사전 대비망 설계 스튜디오").run()
    button = next(b for b in at.button if b.label == "사전 대비망 설계")
    button.click().run(timeout=180)
    assert not at.exception
    assert at.session_state["bundle"]["best"]["solver_status"] == "OPTIMAL"
    assert not any("계산 실패" in e.value for e in at.error)
    for page in pages:
        at.sidebar.radio[1].set_value(page).run()
        assert not at.exception, page


def test_synthetic_screens_and_real_design():
    _walk("synthetic")


def test_hardware_twin_screens():
    _walk("twin")
