"""Helpers for UI-based selection prompts such as order, date, and time choices."""
import re
from typing import List, Tuple


def parse_selection_payload(message: str) -> dict | None:
    """Parse hidden selection markers from a tool response.

    Expected marker format:
        [SELECT_OPTIONS:pickup_day|today|tomorrow|day after tomorrow]
        [SELECT_OPTIONS:cancel_order_order|123:Order 123 (pending)|456:Order 456 (pending)]
    """
    marker = re.search(r"\[SELECT_OPTIONS:([^|\]]+)\|(.+)\]", message)
    if not marker:
        return None

    selection_type = marker.group(1).strip()
    raw_options = marker.group(2).split("|")
    options: list[tuple[str, str]] = []
    for entry in raw_options:
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            value, label = entry.split(":", 1)
            options.append((value.strip(), label.strip()))
        else:
            options.append((entry, entry))

    return {"type": selection_type, "options": options}


def strip_selection_markers(message: str) -> str:
    return re.sub(r"\s*\[SELECT_OPTIONS:[^\]]+\]", "", message).strip()


def build_selection_options(selection_type: str, options: List[Tuple[str, str]]) -> List[Tuple[str, str, str]]:
    """Build menu-style options that fit the existing button transport."""
    return [
        (f"select:{selection_type}:{value}", label, value)
        for value, label in options
    ]
