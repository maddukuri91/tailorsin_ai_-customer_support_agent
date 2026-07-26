"""
Classifies an incoming customer into a segment so we can show a relevant
greeting menu instead of a blank chat.

Segments:
    new_user       - not found in CRM at all
    client         - registered, but no active order right now
    active_client  - registered, with at least one active/in-progress order

NOTE: the exact JSON field names below (client_id, cname, orders, status)
are best-effort guesses based on the API shapes used elsewhere in this
project. Once you have a real sample response from getclient.php and
orderstatus.php, adjust the field lookups accordingly.
"""
import requests
from dataclasses import dataclass

BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api"

# Order statuses that count as "active" for menu purposes — adjust to match
# whatever your CRM actually returns (e.g. "in_production", "picked_up", etc.)
ACTIVE_ORDER_STATUSES = {"pending", "in_progress", "in_production", "picked_up", "processing"}


@dataclass
class CustomerProfile:
    segment: str          # "new_user" | "client" | "active_client"
    name: str | None
    client_id: str | None


def classify_customer(mobile: str) -> CustomerProfile:
    try:
        client_resp = requests.get(f"{BASE_URL}/getclient.php", params={"mobile": mobile}, timeout=10)
        client_resp.raise_for_status()
        client_data = client_resp.json()
    except (requests.exceptions.RequestException, ValueError):
        client_data = {}

    client_id = client_data.get("client_id")
    name = client_data.get("cname") or client_data.get("name")

    if not client_id:
        return CustomerProfile(segment="new_user", name=None, client_id=None)

    # Registered — check for an active order to decide client vs active_client
    try:
        order_resp = requests.get(f"{BASE_URL}/orderstatus.php", params={"mobile": mobile}, timeout=10)
        order_resp.raise_for_status()
        order_data = order_resp.json()
    except (requests.exceptions.RequestException, ValueError):
        order_data = None

    has_active_order = False
    if isinstance(order_data, list):
        has_active_order = any(
            str(o.get("status", "")).lower() in ACTIVE_ORDER_STATUSES for o in order_data
        )
    elif isinstance(order_data, dict) and order_data.get("status"):
        has_active_order = str(order_data["status"]).lower() in ACTIVE_ORDER_STATUSES

    segment = "active_client" if has_active_order else "client"
    return CustomerProfile(segment=segment, name=name, client_id=client_id)