"""
Lightweight in-memory state for message deduplication and human handoff flags.

For a single-server deployment this is fine. If you ever run multiple server
processes/instances behind a load balancer, move both of these to Redis
(the shape stays the same — SETNX for dedup, SET with TTL for handoff) so all
instances share the same state.
"""
import json
import os
import re
import time
from config import HANDOFF_TTL_SECONDS

STATE_FILE = os.getenv("TAILORSIN_STATE_FILE", os.path.join(os.getcwd(), ".tailorsin_state.json"))


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            return payload
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return {}


def _persist_state() -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    payload = {
        "seen_message_ids": _seen_message_ids,
        "handoff_threads": _handoff_threads,
        "greeted_threads": list(_greeted_threads),
        "last_menu": _last_menu,
        "telegram_mobile_map": _telegram_mobile_map,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


_state = _load_state()

# --- Message deduplication ---
# WhatsApp/Telegram webhooks can redeliver the same event on timeout/retry.
# Track recently seen message IDs and skip reprocessing.
_seen_message_ids: dict[str, float] = _state.get("seen_message_ids", {})
_DEDUP_WINDOW_SECONDS = 60 * 10  # 10 minutes is plenty for webhook retries
_handoff_threads: dict[str, float] = _state.get("handoff_threads", {})
_greeted_threads: set[str] = set(_state.get("greeted_threads", []))
_last_menu: dict[str, list] = _state.get("last_menu", {})
_telegram_mobile_map: dict[str, str] = _state.get("telegram_mobile_map", {})


def is_duplicate_message(message_id: str) -> bool:
    now = time.time()
    # opportunistic cleanup of old entries
    stale = [mid for mid, ts in _seen_message_ids.items() if now - ts > _DEDUP_WINDOW_SECONDS]
    for mid in stale:
        _seen_message_ids.pop(mid, None)

    if message_id in _seen_message_ids:
        return True
    _seen_message_ids[message_id] = now
    _persist_state()
    return False


# --- Human handoff flags ---
# Once human_handover fires for a thread, the bot should stop auto-responding
# until a human clears it (or the TTL expires) — otherwise bot and human
# agent replies can collide.

def _normalize_mobile(mobile: str) -> str:
    if not mobile:
        return ""
    digits = re.sub(r"\D", "", str(mobile))
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    if len(digits) > 10:
        return digits[-10:]
    return digits


def mark_handed_off(thread_id: str) -> None:
    _handoff_threads[thread_id] = time.time()
    _persist_state()


def is_handed_off(thread_id: str) -> bool:
    ts = _handoff_threads.get(thread_id)
    if ts is None:
        return False
    if time.time() - ts > HANDOFF_TTL_SECONDS:
        _handoff_threads.pop(thread_id, None)
        _persist_state()
        return False
    return True


def clear_handoff(thread_id: str) -> None:
    """Call this from an internal/admin endpoint once a human agent resolves the thread."""
    _handoff_threads.pop(thread_id, None)
    _persist_state()


# --- Greeting/menu tracking ---
# Show the segmented greeting menu once per session, and again whenever the
# customer explicitly types "menu"/"hi"/"start". For production, back this
# with the DB (or just check whether the checkpointer thread has any prior
# messages) so it survives restarts — this in-memory set resets on deploy.
MENU_TRIGGER_WORDS = {"hi", "hello", "hey", "menu", "start", "/start", "hii", "hlo"}


def has_been_greeted(thread_id: str) -> bool:
    return thread_id in _greeted_threads


def mark_greeted(thread_id: str) -> None:
    _greeted_threads.add(thread_id)
    _persist_state()


def is_menu_trigger(text: str) -> bool:
    return text.strip().lower() in MENU_TRIGGER_WORDS


# --- Last shown menu (for resolving typed-number replies like "1", "2") ---
_last_menu: dict[str, list] = {}


def set_last_menu(thread_id: str, options) -> None:
    _last_menu[thread_id] = options
    _persist_state()


def get_last_menu(thread_id: str):
    return _last_menu.get(thread_id)


# --- Telegram mobile mapping ---
# Telegram only gives us a numeric chat_id, not a phone number, unless the
# customer explicitly shares their contact. We map chat_id -> mobile once
# they do, so classify_customer() has something to query the CRM with.
#
# NOTE: in-memory here for simplicity — move this to the Postgres store
# (or a small dedicated table) for production so it survives restarts;
# otherwise every customer has to re-share their contact after a deploy.
def set_telegram_mobile(chat_id: str, mobile: str) -> None:
    normalized_mobile = _normalize_mobile(mobile)
    _telegram_mobile_map[str(chat_id)] = normalized_mobile
    _persist_state()


def get_telegram_mobile(chat_id: str) -> str | None:
    return _telegram_mobile_map.get(str(chat_id)) or None