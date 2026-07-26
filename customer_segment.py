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
import logging
import time
import requests
from dataclasses import dataclass

logger = logging.getLogger("tailorsin_bot")

BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api"

# Order statuses that count as "active" for menu purposes — adjust to match
# whatever your CRM actually returns (e.g. "in_production", "picked_up", etc.)
ACTIVE_ORDER_STATUSES = {"pending", "in_progress", "in_production", "picked_up", "processing"}
_CACHE_TTL_SECONDS = 300
_classification_cache: dict[str, tuple[float, "CustomerProfile"]] = {}

requests_session = requests.Session()
requests_session.trust_env = False


@dataclass
class CustomerProfile:
    segment: str          # "new_user" | "client" | "active_client"
    name: str | None
    client_id: str | None


def _normalize_mobile(mobile: str) -> str:
    """
    Strip country code prefix from a mobile number so the CRM gets a clean
    10-digit number. WhatsApp 'from' field includes country code
    (e.g. '919701667788' for India), but the CRM API expects just the
    10-digit number (e.g. '9701667788').

    Handles:
      - 919701667788  (India 91 + 10 digits)
      - +919701667788 (with + prefix)
      - 9701667788    (already clean)
    """
    cleaned = mobile.lstrip("+")
    # If the number is longer than 10 digits, assume the first digits are
    # the country code and take only the last 10.
    if len(cleaned) > 10:
        cleaned = cleaned[-10:]
    return cleaned


def try_parse_json(resp, label: str):
    """Try to parse a response as JSON, log the raw text on failure."""
    try:
        return resp.json()
    except ValueError:
        logger.error("CRM %s returned non-JSON: status=%s, body=%s", label, resp.status_code, resp.text[:500])
        return None


def _unwrap_data(data: dict, *wrapper_keys: str) -> dict:
    """If the dict contains a nested dict under one of the wrapper_keys, unwrap it."""
    for key in wrapper_keys:
        if isinstance(data.get(key), dict):
            return data[key]
    return data


def classify_customer(mobile: str) -> CustomerProfile:
    normalized = _normalize_mobile(mobile)
    now = time.time()
    cached = _classification_cache.get(normalized)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        logger.info("classify_customer: using cached segment for mobile=%s", normalized)
        return cached[1]

    logger.info("classify_customer: raw mobile=%s, normalized=%s", mobile, normalized)

    # --- Step 1: Look up client in CRM ---
    try:
        client_resp = requests_session.get(f"{BASE_URL}/getclient.php", params={"mobile": normalized}, timeout=10)
        client_resp.raise_for_status()
        logger.info("getclient.php response status=%s for mobile=%s", client_resp.status_code, normalized)
        client_data = try_parse_json(client_resp, "getclient.php")
        if client_data:
            logger.info("getclient.php parsed data: %s", str(client_data)[:300])
    except requests.exceptions.RequestException as e:
        logger.error("getclient.php request failed for mobile=%s: %s", normalized, e)
        client_data = {}

    if client_data is None:
        client_data = {}

    # The CRM tells us the type directly in the top-level response!
    # e.g. {'status': 'success', 'type': 'active_client', 'client': {...}}
    crm_type = client_data.get("type", "")
    logger.info("classify_customer: CRM-reported type='%s'", crm_type)

    if crm_type == "new_user":
        logger.info("classify_customer: CRM says new_user -> returning new_user")
        profile = CustomerProfile(segment="new_user", name=None, client_id=None)
        _classification_cache[normalized] = (now, profile)
        return profile

    # Extract client details from the nested 'client' object
    client_nested = client_data.get("client")
    if isinstance(client_nested, dict):
        client_id = client_nested.get("id") or client_nested.get("client_id")
        name = client_nested.get("cname") or client_nested.get("name")
        logger.info("classify_customer: extracted from nested client: client_id=%s, name=%s",
                    client_id, name)
    else:
        client_id = None
        name = None

    if not client_id:
        logger.info("classify_customer: no client_id in nested data -> returning new_user")
        profile = CustomerProfile(segment="new_user", name=None, client_id=None)
        _classification_cache[normalized] = (now, profile)
        return profile

    # CRM already tells us the type for known customers
    if crm_type in ("active_client", "client"):
        logger.info("classify_customer: using CRM-reported type='%s' directly", crm_type)
        profile = CustomerProfile(segment=crm_type, name=name, client_id=client_id)
        _classification_cache[normalized] = (now, profile)
        return profile

    # --- Step 2: Only needed if CRM doesn't tell us the type ---
    # Check for active orders
    try:
        order_resp = requests_session.get(f"{BASE_URL}/orderstatus.php", params={"mobile": normalized}, timeout=10)
        order_resp.raise_for_status()
        logger.info("orderstatus.php response status=%s for mobile=%s", order_resp.status_code, normalized)
        order_data = try_parse_json(order_resp, "orderstatus.php")
        if order_data:
            logger.info("orderstatus.php parsed data: %s", str(order_data)[:300])
    except requests.exceptions.RequestException as e:
        logger.error("orderstatus.php request failed for mobile=%s: %s", normalized, e)
        order_data = None

    has_active_order = False
    if isinstance(order_data, list):
        has_active_order = any(
            str(o.get("status", "")).lower() in ACTIVE_ORDER_STATUSES for o in order_data
        )
        logger.info("classify_customer: order_data is list len=%d, has_active_order=%s",
                    len(order_data), has_active_order)
    elif isinstance(order_data, dict):
        # CRM wraps orders in {'orders': [...]} or {'data': [...]} 
        orders = order_data.get("orders") or order_data.get("data")
        if isinstance(orders, list):
            has_active_order = any(
                str(o.get("stage_label", "")).lower() in ACTIVE_ORDER_STATUSES
                or str(o.get("status", "")).lower() in ACTIVE_ORDER_STATUSES
                for o in orders
            )
            logger.info("classify_customer: extracted orders list len=%d, has_active_order=%s",
                        len(orders), has_active_order)
        else:
            logger.info("classify_customer: order_data dict but no orders list found, keys=%s",
                        list(order_data.keys()))
    else:
        logger.info("classify_customer: order_data is None or unexpected type=%s", type(order_data))

    segment = "active_client" if has_active_order else "client"
    logger.info("classify_customer: final segment=%s for mobile=%s (fallback method)", segment, normalized)
    profile = CustomerProfile(segment=segment, name=name, client_id=client_id)
    _classification_cache[normalized] = (now, profile)
    return profile
