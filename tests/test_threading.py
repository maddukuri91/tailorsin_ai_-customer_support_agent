import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_telegram_thread_uses_mobile_when_available():
    agent_setup = importlib.import_module("agent_setup")
    thread_id = agent_setup.get_thread_id("telegram", "123456", customer_mobile="919701667788")
    assert thread_id == "telegram:919701667788"
