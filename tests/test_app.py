"""Phase 7 smoke test: the Streamlit app imports cleanly and its non-UI helper works.

We don't launch Streamlit here (no UI runtime in tests); we just guard against import-time breakage
and verify the upload-fallback helper, since the data logic itself is covered by the pipeline tests.
"""

import importlib.util
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"


def _load_app():
    spec = importlib.util.spec_from_file_location("app_under_test", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_app_imports_and_exposes_main():
    app = _load_app()
    assert callable(app.main)


def test_save_upload_falls_back_to_sample():
    app = _load_app()
    fallback = APP.parent / "src" / "steelreuse" / "data" / "samples" / "donor.json"
    assert app._save_upload(None, fallback) == str(fallback)
