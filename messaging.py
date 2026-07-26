"""
Functions to send outbound replies back to WhatsApp / Telegram.
Kept separate from the webhook handlers so the same functions can be reused
by a proactive notifier (e.g., "your alteration is ready") outside the
request/response cycle.
"""
import re
import httpx
from config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, TELEGRAM_BOT_TOKEN


def _make_async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10, trust_env=False)


# ---------------------------------------------------------------------------
# Markdown stripper — LLMs love formatting but WhatsApp/Telegram plain text
# doesn't support it (Telegram has MarkdownV2 but it's strict and error-prone
# with special characters). Strip it to plain text instead.
# ---------------------------------------------------------------------------
def _strip_markdown(text: str) -> str:
    """
    Remove common markdown formatting so raw agent replies render nicely
    on WhatsApp and Telegram (plain text mode).
    """
    # Remove code blocks (```...```)
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Remove inline code (`...`)
    text = re.sub(r'`[^`]+`', '', text)
    # Remove image/link markup: ![alt](url) and [text](url)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove bold/italic markers: **text**, __text__, *text*, _text_
    text = re.sub(r'(\*\*|__)(.*?)\1', r'\2', text)
    text = re.sub(r'(\*|_)(.*?)\1', r'\2', text)
    # Remove heading markers: ###, ##, #
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove horizontal rules: ---, ***, ___
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Remove blockquote markers: >
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # Remove strikethrough: ~~text~~
    text = re.sub(r'~~(.*?)~~', r'\1', text)
    # Trim leading/trailing whitespace and collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text


async def send_whatsapp_message(to_mobile: str, text: str) -> None:
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_mobile,
        "type": "text",
        "text": {"body": _strip_markdown(text)},
    }
    async with _make_async_client() as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()


async def send_telegram_message(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": _strip_markdown(text)}
    async with _make_async_client() as client:
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
    async with _make_async_client() as client:
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
    async with _make_async_client() as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def answer_telegram_callback(callback_query_id: str) -> None:
    """Must be called after handling a button tap, or Telegram shows a loading spinner on the button."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    async with _make_async_client() as client:
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
    async with _make_async_client() as client:
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