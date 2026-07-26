"""
FastAPI webhook server for the Tailorsin support bot.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000

Architecture:
    1. Webhook receives the incoming message, responds 200 immediately
       (both WhatsApp and Telegram will retry aggressively if you're slow
       or don't return 200 quickly).
    2. The actual agent processing + reply sending happens in a
       BackgroundTask, decoupled from the webhook response.
    3. thread_id = f"{platform}:{mobile_or_chat_id}" so each customer's
       conversation persists across messages via the Postgres checkpointer.
    4. If a thread has been handed off to a human, the bot skips processing
       entirely until cleared.
"""
import logging
from fastapi import FastAPI, Request, BackgroundTasks, Query, HTTPException
from fastapi.responses import PlainTextResponse

from agent_setup import agent, get_thread_id
from messaging import (
    send_whatsapp_message, send_telegram_message,
    send_whatsapp_menu, send_telegram_menu, answer_telegram_callback,
    send_telegram_request_contact, send_telegram_remove_keyboard,
)
from state import (
    is_duplicate_message, is_handed_off, mark_handed_off, clear_handoff,
    has_been_greeted, mark_greeted, is_menu_trigger,
    set_last_menu, get_last_menu,
    set_telegram_mobile, get_telegram_mobile,
)
from customer_segment import classify_customer
from menu import build_menu, resolve_typed_number
from config import WHATSAPP_VERIFY_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tailorsin_bot")

app = FastAPI(title="Tailorsin Support Bot")


# ---------------------------------------------------------------------------
# Shared agent invocation logic
# ---------------------------------------------------------------------------
async def send_greeting_menu(platform: str, user_identifier: str, mobile_for_lookup: str) -> None:
    """
    Classifies the customer and sends the segmented menu. `mobile_for_lookup`
    is the actual phone number used to query the CRM — for WhatsApp this is
    the same as user_identifier; for Telegram (numeric chat_id, no phone by
    default) you'll need to have captured their mobile during registration
    and mapped it, otherwise this falls back to treating them as new_user.
    """
    thread_id = get_thread_id(platform, user_identifier)
    profile = classify_customer(mobile_for_lookup)
    greeting, options = build_menu(profile)

    set_last_menu(thread_id, options)
    mark_greeted(thread_id)

    if platform == "whatsapp":
        await send_whatsapp_menu(user_identifier, greeting, options)
    elif platform == "telegram":
        await send_telegram_menu(user_identifier, greeting, options)


async def process_and_reply(platform: str, user_identifier: str, user_text: str) -> None:
    thread_id = get_thread_id(platform, user_identifier)

    if is_handed_off(thread_id):
        logger.info("Thread %s is handed off to a human — skipping bot reply.", thread_id)
        return

    # Telegram: we need the customer's real mobile number to classify them
    # and query the CRM. If we don't have it yet, ask them to share contact
    # instead of greeting them as a blind new_user.
    if platform == "telegram" and not get_telegram_mobile(user_identifier):
        await send_telegram_request_contact(
            user_identifier,
            "Hi! 👋 To get started, please share your mobile number using the button below."
        )
        return

    # First contact, or customer explicitly asked for the menu
    if not has_been_greeted(thread_id) or is_menu_trigger(user_text):
        mobile_for_lookup = get_telegram_mobile(user_identifier) if platform == "telegram" else user_identifier
        await send_greeting_menu(platform, user_identifier, mobile_for_lookup=mobile_for_lookup)
        return

    # Typed a plain number matching the last shown menu (e.g. "1") — resolve
    # to that option's intent text instead of sending the raw digit to the LLM
    last_options = get_last_menu(thread_id)
    if last_options:
        resolved_intent = resolve_typed_number(user_text, last_options)
        if resolved_intent:
            user_text = resolved_intent

    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_text}]},
            config,
        )
        reply_text = result["messages"][-1].content

        # simple heuristic: if the agent itself triggered human_handover,
        # flag the thread so the bot stops responding until a human clears it
        if "human_handover" in str(result["messages"][-2:]).lower():
            mark_handed_off(thread_id)

    except Exception:
        logger.exception("Agent invocation failed for thread %s", thread_id)
        reply_text = ("Sorry, I ran into an issue processing that. "
                      "I'm connecting you with a human agent.")
        mark_handed_off(thread_id)

    if platform == "whatsapp":
        await send_whatsapp_message(user_identifier, reply_text)
    elif platform == "telegram":
        await send_telegram_message(user_identifier, reply_text)


def resolve_tapped_option(thread_id: str, option_id: str) -> str | None:
    """Given a tapped button/list-row id, return its intent text, if known."""
    options = get_last_menu(thread_id)
    if not options:
        return None
    for opt_id, _label, intent in options:
        if opt_id == option_id:
            return intent
    return None


# ---------------------------------------------------------------------------
# WhatsApp Cloud API webhook
# ---------------------------------------------------------------------------
@app.get("/webhook/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """Meta calls this once when you register the webhook URL."""
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")
        if not messages:
            # status update (delivered/read receipt) etc — nothing to do
            return {"status": "ignored"}

        msg = messages[0]
        message_id = msg["id"]
        mobile = msg["from"]  # includes country code, e.g. "919701667788"
        text = msg.get("text", {}).get("body", "")

        # Interactive list-row tap (from send_whatsapp_menu)
        interactive = msg.get("interactive", {})
        list_reply = interactive.get("list_reply")
        if list_reply:
            tapped_id = list_reply.get("id")
        else:
            tapped_id = None

    except (KeyError, IndexError):
        logger.warning("Unrecognized WhatsApp payload shape: %s", payload)
        return {"status": "ignored"}

    if is_duplicate_message(message_id):
        return {"status": "duplicate_ignored"}

    if tapped_id:
        thread_id = get_thread_id("whatsapp", mobile)
        intent = resolve_tapped_option(thread_id, tapped_id)
        background_tasks.add_task(process_and_reply, "whatsapp", mobile, intent or tapped_id)
        return {"status": "accepted"}

    if not text:
        # non-text message (image, audio, etc.) — handle separately if needed
        background_tasks.add_task(
            process_and_reply, "whatsapp", mobile,
            "[Customer sent a non-text message — ask them to describe what they need in words.]"
        )
        return {"status": "accepted"}

    background_tasks.add_task(process_and_reply, "whatsapp", mobile, text)
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Telegram Bot API webhook
# ---------------------------------------------------------------------------
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    # Inline button tap (from send_telegram_menu)
    callback_query = payload.get("callback_query")
    if callback_query:
        chat_id = str(callback_query["message"]["chat"]["id"])
        tapped_id = callback_query["data"]
        thread_id = get_thread_id("telegram", chat_id)
        intent = resolve_tapped_option(thread_id, tapped_id)

        background_tasks.add_task(answer_telegram_callback, callback_query["id"])
        background_tasks.add_task(process_and_reply, "telegram", chat_id, intent or tapped_id)
        return {"status": "accepted"}

    message = payload.get("message")
    if not message:
        return {"status": "ignored"}

    chat_id = str(message["chat"]["id"])

    # Customer tapped "Share my mobile number" — capture it and proceed to the greeting menu
    contact = message.get("contact")
    if contact:
        raw_phone = contact.get("phone_number", "")
        normalized_mobile = raw_phone.lstrip("+")  # match the plain-digit format used elsewhere
        set_telegram_mobile(chat_id, normalized_mobile)

        background_tasks.add_task(
            send_telegram_remove_keyboard, chat_id, "Thanks! Got it. ✅"
        )
        background_tasks.add_task(
            send_greeting_menu, "telegram", chat_id, normalized_mobile
        )
        return {"status": "accepted"}

    message_id = str(message.get("message_id"))
    text = message.get("text", "")

    dedup_key = f"telegram:{chat_id}:{message_id}"
    if is_duplicate_message(dedup_key):
        return {"status": "duplicate_ignored"}

    if not text:
        background_tasks.add_task(
            process_and_reply, "telegram", chat_id,
            "[Customer sent a non-text message — ask them to describe what they need in words.]"
        )
        return {"status": "accepted"}

    background_tasks.add_task(process_and_reply, "telegram", chat_id, text)
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Internal/admin endpoints
# ---------------------------------------------------------------------------
@app.post("/admin/clear-handoff")
async def admin_clear_handoff(platform: str, user_identifier: str):
    """Call this once a human support agent has resolved a handed-off conversation."""
    thread_id = get_thread_id(platform, user_identifier)
    clear_handoff(thread_id)
    return {"status": "cleared", "thread_id": thread_id}


@app.get("/health")
async def health():
    return {"status": "ok"}