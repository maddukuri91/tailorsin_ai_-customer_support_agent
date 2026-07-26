"""
Builds the greeting + menu shown to a customer based on their segment.
Each menu option has:
    id    - stable identifier used in button taps / typed replies
    label - what the customer sees
    intent - free-text phrase fed to the agent when this option is chosen
             (kept close to natural customer language so the agent's
             existing tool-calling logic handles it the same way it would
             if the customer had typed it themselves)

Common options are appended to every segment automatically, per your
instinct that FAQ/booking/addresses/human-handover are always relevant
regardless of the customer's order state.
"""
from customer_segment import CustomerProfile

SEGMENT_OPTIONS = {
    "active_client": [
        ("track_order", "📦 Track my order", "I want to track my order status"),
        ("modify_order", "✏️ Modify my order", "I want to modify my order"),
        ("cancel_order", "❌ Cancel my order", "I want to cancel my order"),
        ("report_issue", "🧵 Report an issue / Alteration", "I have an issue with my order and need an alteration"),
    ],
    "client": [
        ("place_order", "🧺 Place an order / Pickup", "I want to place a new order and schedule a pickup"),
        ("drop_off", "🏬 Drop off fabric at store", "I want to drop off fabric at the store"),
    ],
    "new_user": [
        ("faq", "ℹ️ Learn about Tailorsin", "Tell me about tailorsin.com and how it works"),
        ("register", "📝 Register to place an order / book a visit", "I want to register as a new customer"),
    ],
}

COMMON_OPTIONS = [
    ("faq", "❓ Frequently asked questions", "I have a question about your services"),
    ("book_visit", "🏠 Book a store visit", "I want to book a store visit appointment"),
    ("manage_address", "📍 Manage my addresses", "I want to add or delete a saved address"),
    ("human", "🙋 Talk to a human", "I want to speak with a human agent"),
]


def _dedupe_by_id(options):
    seen = set()
    result = []
    for opt in options:
        if opt[0] not in seen:
            seen.add(opt[0])
            result.append(opt)
    return result


def build_menu(profile: CustomerProfile):
    """
    Returns (greeting_text, options) where options is a list of
    (id, label, intent) tuples, segment-specific options first, then
    common ones (deduped — e.g. 'faq' won't show twice for new_user).
    """
    name_part = f", {profile.name}" if profile.name else ""

    greetings = {
        "active_client": f"Welcome back{name_part}! 👋 Looks like you have an order with us. What would you like to do?",
        "client": f"Hi{name_part}! 👋 Great to see you again. What can I help you with today?",
        "new_user": "Hi there! 👋 Welcome to Tailorsin — quick e-tailoring, pickup to delivery in 24 hours. How can I help?",
    }
    greeting = greetings.get(profile.segment, "Hi! 👋 How can I help you today?")

    options = _dedupe_by_id(SEGMENT_OPTIONS.get(profile.segment, []) + COMMON_OPTIONS)
    return greeting, options


def render_text_menu(greeting: str, options) -> str:
    """Plain numbered-text fallback, used when interactive UI isn't available/fails."""
    lines = [greeting, ""]
    for i, (_id, label, _intent) in enumerate(options, start=1):
        lines.append(f"{i}. {label}")
    lines.append("\nReply with a number, or just type what you need.")
    return "\n".join(lines)


def resolve_typed_number(text: str, options):
    """
    If the customer replies with a plain number matching a menu position,
    return that option's intent string. Otherwise return None (treat as
    free text and let the agent handle it).
    """
    text = text.strip()
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(options):
            return options[idx][2]  # intent
    return None