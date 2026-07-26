import importlib
import sys
from pathlib import Path


def test_telegram_mobile_persists_across_reload(tmp_path, monkeypatch):
    state_file = tmp_path / "tailorsin_state.json"
    monkeypatch.setenv("TAILORSIN_STATE_FILE", str(state_file))

    sys.modules.pop("state", None)
    state_module = importlib.import_module("state")

    state_module.set_telegram_mobile("123456", "+91 9701667788")
    reloaded = importlib.reload(state_module)

    assert reloaded.get_telegram_mobile("123456") == "919701667788"
