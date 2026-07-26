"""
Lightweight in-memory state for message deduplication and human handoff flags.

For a single-server deployment this is fine. If you ever run multiple server
processes/instances behind a load balancer, move both of these to Redis
(the shape stays the same — SETNX for dedup, SET with TTL for handoff) so all
instances share the same state.
"""
import time
from config import HANDOFF_TTL_SECONDS

# --- Message deduplication ---
# WhatsApp/Telegram webhooks can redeliver the same event on timeout/retry.
# Track recently seen message IDs and skip reprocessing.
_seen_message_ids: dict[str, float] = {}
_DEDUP_WINDOW_SECONDS = 60 * 10  # 10 minutes is plenty for webhook retries


def is_duplicate_message(message_id: str) -> bool:
    now = time.time()
    # opportunistic cleanup of old entries
    stale = [mid for mid, ts in _seen_message_ids.items() if now - ts > _DEDUP_WINDOW_SECONDS]
    for mid in stale:
        _seen_message_ids.pop(mid, None)

    if message_id in _seen_message_ids:
        return True
    _seen_message_ids[message_id] = now
    return False


# --- Human handoff flags ---
# Once human_handover fires for a thread, the bot should stop auto-responding
# until a human clears it (or the TTL expires) — otherwise bot and human
# agent replies can collide.
_handoff_threads: dict[str, float] = {}


def mark_handed_off(thread_id: str) -> None:
    _handoff_threads[thread_id] = time.time()


def is_handed_off(thread_id: str) -> bool:
    ts = _handoff_threads.get(thread_id)
    if ts is None:
        return False
    if time.time() - ts > HANDOFF_TTL_SECONDS:
        _handoff_threads.pop(thread_id, None)
        return False
    return True


def clear_handoff(thread_id: str) -> None:
    """Call this from an internal/admin endpoint once a human agent resolves the thread."""
    _handoff_threads.pop(thread_id, None)


# --- Greeting/menu tracking ---
# Show the segmented greeting menu once per session, and again whenever the
# customer explicitly types "menu"/"hi"/"start". For production, back this
# with the DB (or just check whether the checkpointer thread has any prior
# messages) so it survives restarts — this in-memory set resets on deploy.
_greeted_threads: set[str] = set()

MENU_TRIGGER_WORDS = {"hi", "hello", "hey", "menu", "start", "/start", "hii", "hlo"}


def has_been_greeted(thread_id: str) -> bool:
    return thread_id in _greeted_threads


def mark_greeted(thread_id: str) -> None:
    _greeted_threads.add(thread_id)


def is_menu_trigger(text: str) -> bool:
    return text.strip().lower() in MENU_TRIGGER_WORDS


# --- Last shown menu (for resolving typed-number replies like "1", "2") ---
_last_menu: dict[str, list] = {}


def set_last_menu(thread_id: str, options) -> None:
    _last_menu[thread_id] = options


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
_telegram_mobile_map: dict[str, str] = {}


def set_telegram_mobile(chat_id: str, mobile: str) -> None:
    _telegram_mobile_map[chat_id] = mobile


def get_telegram_mobile(chat_id: str) -> str | None:
    return _telegram_mobile_map.get(chat_id)