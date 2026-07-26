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
import httpx

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
from menu import build_menu, resolve_typed_number, resolve_option_id
from config import WHATSAPP_VERIFY_TOKEN, TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tailorsin_bot")

app = FastAPI(title="Tailorsin Support Bot")


# ---------------------------------------------------------------------------
# Startup: ensure Telegram webhook accepts callback_query updates
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def configure_telegram_webhook():
    """
    Ensure the Telegram webhook is set up to receive callback_query updates
    (needed for inline button taps). Without this, Telegram only sends
    'message' updates and button taps are silently dropped.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping webhook configuration.")
        return

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
    set_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"

    async with httpx.AsyncClient(timeout=10) as client:
        # First check current webhook status
        resp = await client.get(api_url)
        info = resp.json()
        logger.info("Current Telegram webhook info: %s", str(info.get("result", info))[:300])

        result = info.get("result", {})
        current_url = result.get("url", "")
        current_allowed = result.get("allowed_updates", [])

        if "callback_query" in current_allowed:
            logger.info("Telegram webhook already accepts callback_query updates.")
            return

        if not current_url:
            logger.warning("No Telegram webhook URL set. "
                           "Set your webhook URL via Telegram API or configure it manually.")
            return

        # Update the existing webhook to include callback_query
        resp = await client.post(set_url, json={
            "url": current_url,
            "allowed_updates": ["message", "callback_query", "edited_message"],
        })
        result = resp.json()
        logger.info("Telegram webhook updated: url=%s, result=%s", current_url, result)


# ---------------------------------------------------------------------------
# Shared agent invocation logic
# ---------------------------------------------------------------------------
async def send_greeting_menu(platform: str, user_identifier: str, mobile_for_lookup: str,
                              is_reply: bool = False) -> None:
    """
    Classifies the customer and sends the segmented menu. `mobile_for_lookup`
    is the actual phone number used to query the CRM — for WhatsApp this is
    the same as user_identifier; for Telegram (numeric chat_id, no phone by
    default) you'll need to have captured their mobile during registration
    and mapped it, otherwise this falls back to treating them as new_user.

    If is_reply=True, uses a shorter prompt (e.g. "Anything else?") instead
    of the full welcome greeting.
    """
    thread_id = get_thread_id(platform, user_identifier)
    profile = classify_customer(mobile_for_lookup)
    greeting, options = build_menu(profile, is_reply=is_reply)

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

    # First contact — send greeting menu
    if not has_been_greeted(thread_id):
        mobile_for_lookup = get_telegram_mobile(user_identifier) if platform == "telegram" else user_identifier
        await send_greeting_menu(platform, user_identifier, mobile_for_lookup=mobile_for_lookup)
        return

    # Customer explicitly asked for the menu — resend it
    if is_menu_trigger(user_text):
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

    # Re-send the menu so buttons are visible for the next interaction
    mobile_for_lookup = get_telegram_mobile(user_identifier) if platform == "telegram" else user_identifier
    profile = classify_customer(mobile_for_lookup)
    greeting, options = build_menu(profile, is_reply=True)
    set_last_menu(thread_id, options)

    if platform == "whatsapp":
        await send_whatsapp_menu(user_identifier, greeting, options)
    elif platform == "telegram":
        await send_telegram_menu(user_identifier, greeting, options)


def resolve_tapped_option(thread_id: str, option_id: str) -> str | None:
    """Given a tapped button/list-row id, return its intent text, if known."""
    # First try the in-memory last menu (fast path)
    options = get_last_menu(thread_id)
    if options:
        for opt_id, _label, intent in options:
            if opt_id == option_id:
                return intent
    # Fallback: look up the option ID globally (works even after server restart)
    return resolve_option_id(option_id)


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

    logger.info("Telegram webhook received: %s", str(payload)[:500])

    # Inline button tap (from send_telegram_menu)
    callback_query = payload.get("callback_query")
    if callback_query:
        chat_id = str(callback_query["message"]["chat"]["id"])
        tapped_id = callback_query["data"]
        thread_id = get_thread_id("telegram", chat_id)
        intent = resolve_tapped_option(thread_id, tapped_id)
        logger.info("Telegram callback_query: chat_id=%s, tapped_id=%s, resolved_intent=%s",
                    chat_id, tapped_id, intent)

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