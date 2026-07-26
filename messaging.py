"""
Functions to send outbound replies back to WhatsApp / Telegram.
Kept separate from the webhook handlers so the same functions can be reused
by a proactive notifier (e.g., "your alteration is ready") outside the
request/response cycle.
"""
import httpx
from config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, TELEGRAM_BOT_TOKEN


async def send_whatsapp_message(to_mobile: str, text: str) -> None:
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_mobile,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()


async def send_telegram_message(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def send_whatsapp_menu(to_mobile: str, greeting: str, options) -> None:
    """
    Sends a native WhatsApp interactive list message. WhatsApp list messages
    support up to 10 rows — if you ever exceed that, split into sections or
    fall back to render_text_menu() instead.
    """
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

    rows = [
        {"id": opt_id, "title": label[:24]}  # WhatsApp row titles are capped at 24 chars
        for opt_id, label, _intent in options[:10]
    ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to_mobile,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": greeting},
            "action": {
                "button": "Choose an option",
                "sections": [{"title": "How can we help?", "rows": rows}],
            },
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()


async def send_telegram_menu(chat_id: str, greeting: str, options) -> None:
    """Sends a Telegram message with an inline keyboard (one button per row)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    keyboard = [[{"text": label, "callback_data": opt_id}] for opt_id, label, _intent in options]
    payload = {
        "chat_id": chat_id,
        "text": greeting,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def answer_telegram_callback(callback_query_id: str) -> None:
    """Must be called after handling a button tap, or Telegram shows a loading spinner on the button."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={"callback_query_id": callback_query_id})
        resp.raise_for_status()


async def send_telegram_request_contact(chat_id: str, text: str) -> None:
    """
    Prompts the customer to share their phone number via Telegram's native
    'request contact' button — this fills in their real phone number
    automatically (no typing/typos), which we need to look them up in the CRM.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "keyboard": [[{"text": "📱 Share my mobile number", "request_contact": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def send_telegram_remove_keyboard(chat_id: str, text: str) -> None:
    """Sends a message and clears any previously shown reply keyboard (e.g. after contact is shared)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"remove_keyboard": True},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()