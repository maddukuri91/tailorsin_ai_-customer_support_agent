"""
All Tailorsin CRM tools in one importable module.

    from tools import tools

gives you the full list to pass into create_agent.
"""
import requests
from datetime import datetime, timedelta
from langchain_core.tools import tool

BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api"


# ---------------------------------------------------------------------------
# Shared internal helpers (not tools themselves — used inside tools below)
# ---------------------------------------------------------------------------
def _fetch_saved_addresses(mobile: str):
    """Returns parsed JSON address list, or None if none found / not JSON."""
    resp = requests.get(f"{BASE_URL}/customeraddress.php", params={"mobile": mobile}, timeout=10)
    resp.raise_for_status()
    try:
        addresses = resp.json()
    except ValueError:
        return None
    return addresses if addresses else None


def _add_new_address(mobile: str, address: str, locality: str, city: str,
                      pincode: str, address2: str = ""):
    """Adds a new address, returns the new address_id (or None on failure)."""
    resp = requests.post(f"{BASE_URL}/addaddress.php", json={
        "mobile": mobile,
        "address": address,
        "address2": address2,
        "locality": locality,
        "city": city,
        "pincode": pincode
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("address_id")


def _resolve_pickup_date(pickup_day: str):
    """Converts 'today'/'tomorrow'/'day after tomorrow' into YYYY-MM-DD, or None if invalid."""
    today = datetime.now()
    day_map = {
        "today": today,
        "tomorrow": today + timedelta(days=1),
        "day after tomorrow": today + timedelta(days=2),
    }
    chosen = day_map.get(pickup_day.strip().lower())
    return chosen.strftime("%Y-%m-%d") if chosen else None


def _resolve_address_or_prompt(mobile, address_id, new_address, new_locality,
                                new_city, new_pincode, new_address2, next_step_hint):
    """
    Shared address-resolution logic used by place_order and alteration_pickup.
    Returns either:
        (resolved_address_id, None)  -> success, proceed
        (None, "NEEDS_INFO: ...")    -> need more from the customer, return this string as-is
    """
    if address_id:
        return address_id, None

    if new_address and new_locality and new_city and new_pincode:
        resolved = _add_new_address(mobile, new_address, new_locality, new_city,
                                     new_pincode, new_address2)
        if not resolved:
            return None, "Failed to add address — the CRM did not return an address_id."
        return resolved, None

    addresses = _fetch_saved_addresses(mobile)
    if addresses:
        return None, (f"NEEDS_INFO: Customer has saved addresses: {addresses}. Ask the "
                       f"customer to pick one (by address_id), then {next_step_hint}.")
    return None, ("NEEDS_INFO: No saved addresses found. Ask the customer for their address "
                   f"(address line, locality, city, pincode, optional landmark), then {next_step_hint}.")


# ---------------------------------------------------------------------------
# Client & FAQ
# ---------------------------------------------------------------------------
@tool
def get_client_type(mobile: str) -> str:
    """
    Fetch client details/classification from the Tailorsin CRM by mobile number.

    Args:
        mobile: The client's 10-digit mobile number (e.g., "9701667788").

    Returns:
        The client details as returned by the CRM API, or an error message if the lookup fails.
    """
    try:
        response = requests.get(f"{BASE_URL}/getclient.php", params={"mobile": mobile}, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        return f"Error fetching client data: {str(e)}"


COMPANY_FAQ_TEXT = """
Q: What is tailorsin.com?
A: tailorsin.com is a quick e-tailoring company that collects cloth material, stitches, and delivers in 24 hours.

Q: How does tailorsin.com work?
A: 1. Schedule a pickup — we come to you, or you can send the material to our store.
2. Hand over your fabric along with a sample or reference garment.
3. Within 6 business hours, our team contacts you to confirm design details and share a detailed estimate.
4. Once you approve the estimate and complete payment, we confirm the delivery timeline and begin production.
5. If you choose not to proceed, your unstitched fabric will be safely returned.
6. After production, an e-invoice will be generated.
7. Your stitched outfits will be delivered to your doorstep.
8. Free alterations are available within 7 days of delivery.

Q: What is your measurement policy?
A: Currently, we do not offer home measurement services due to quality control and customer privacy reasons.
To ensure a reliable fit, please share a sample or reference outfit during pickup, or book a store visit by appointment.

Q: What is the delivery timeline?
A: Most orders are completed within 24 hours of cloth pickup once the design and estimate are approved.
For complex garments, detailed embroidery, or special finishing, our team confirms the committed delivery date during order approval.

Q: What is your return/alteration policy?
A: Alterations are free within 7 days of the bill being generated if there's a fitting issue.

Q: Where are the serviceable areas?
A: We currently serve Hyderabad, but we accept orders from other locations as well.
Share your area or pincode and we will confirm pickup and delivery availability for your location before scheduling.

Q: What garments does tailorsin.com stitch?
A: We stitch all kinds of men's, women's, and kids' wear.

Q: What are the prices?
A: To view our price list, click this link:
https://drive.google.com/file/d/1s67qOzn2n22lN670ir0Le462FcgGyCGL/view?usp=sharing
"""


@tool
def get_company_faq(question: str) -> str:
    """
    Retrieve company FAQ knowledge base for Tailorsin — covers what the company does,
    how the process works, measurement policy, delivery timeline, alteration policy,
    serviceable areas, garment types, and pricing.

    Args:
        question: The customer's question about company policies or info.
    """
    return COMPANY_FAQ_TEXT


@tool
def register_client(mobile: str, cname: str, email: str) -> str:
    """
    Register a new client in the Tailorsin CRM.

    Args:
        mobile: The client's 10-digit mobile number (e.g., "7287874454").
        cname: The client's full name.
        email: The client's email address.

    Returns:
        Confirmation of client registration, or an error message if it fails.
    """
    try:
        response = requests.post(f"{BASE_URL}/addclient.php", json={
            "mobile": mobile, "cname": cname, "email": email
        }, timeout=10)
        response.raise_for_status()
        return str(response.json())
    except requests.exceptions.RequestException as e:
        return f"Error registering client: {str(e)}"


# ---------------------------------------------------------------------------
# Appointments & handoff
# ---------------------------------------------------------------------------
@tool
def book_appointment(mobile: str, store_id: int, bookdate: str, booktime: str) -> str:
    """
    Book a store visit appointment for a client at Tailorsin.

    Args:
        mobile: The client's mobile number, including country code (e.g., "919908712226").
        store_id: The ID of the store to book the appointment at.
        bookdate: The appointment date in YYYY-MM-DD format (e.g., "2026-07-10").
        booktime: The appointment time slot (e.g., "11:00 AM - 12:00 PM").

    Returns:
        Confirmation of the booked appointment, or an error message if booking fails.
    """
    try:
        response = requests.post(f"{BASE_URL}/bookappointment.php", json={
            "mobile": mobile, "store_id": store_id, "bookdate": bookdate, "booktime": booktime
        }, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        return f"Error booking appointment: {str(e)}"


@tool
def human_handover(mobile: str) -> str:
    """
    Escalate the conversation to a human support agent for this client.
    Use this when the user explicitly asks to speak to a human, or when
    the query is too complex/sensitive for the AI to resolve.

    Args:
        mobile: The client's mobile number, including country code (e.g., "919908712226").

    Returns:
        Confirmation that the handover was triggered, or an error message if it fails.
    """
    try:
        response = requests.post(f"{BASE_URL}/humanhandover.php", params={"mobile": mobile}, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        return f"Error triggering human handover: {str(e)}"


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
@tool
def get_customer_addresses(mobile: str) -> str:
    """
    Fetch all saved addresses for a customer by mobile number.

    Args:
        mobile: The customer's mobile number (e.g., "7287874454").

    Returns:
        A list of saved addresses with their address_id, or a message indicating none found.
    """
    try:
        addresses = _fetch_saved_addresses(mobile)
        return str(addresses) if addresses else "No saved addresses found for this customer."
    except requests.exceptions.RequestException as e:
        return f"Error fetching addresses: {str(e)}"


@tool
def add_customer_address(mobile: str, address: str, locality: str, city: str,
                          pincode: str, address2: str = "") -> str:
    """
    Add a new address for a customer. Use this only when the customer has no
    saved addresses, or explicitly wants to add a new one.

    Args:
        mobile: The customer's mobile number, including country code (e.g., "919701667788").
        address: Primary address line (e.g., house/flat/building name).
        locality: Locality or neighborhood (e.g., "Kondapur").
        city: City name (e.g., "Hyderabad").
        pincode: 6-digit postal pincode.
        address2: Optional secondary address line/landmark (e.g., "Near Metro Station").

    Returns:
        Confirmation of the added address (including its address_id), or an error message.
    """
    try:
        address_id = _add_new_address(mobile, address, locality, city, pincode, address2)
        return f"Address added successfully. address_id: {address_id}" if address_id \
            else "Failed to add address — the CRM did not return an address_id."
    except requests.exceptions.RequestException as e:
        return f"Error adding address: {str(e)}"


@tool
def delete_customer_address(mobile: str, address_id: int = 0) -> str:
    """
    Delete a saved address for a customer. Handles the full flow: fetches the
    customer's saved addresses, and once the customer selects one, deletes it.

    Call this with just mobile first. If address_id is not yet known, this tool
    returns the list of saved addresses for the customer to choose from; call it
    again with address_id once the customer picks one.

    Args:
        mobile: The customer's mobile number.
        address_id: ID of the saved address to delete. Leave as 0 until the
            customer has selected one from the list.

    Returns:
        Either a request for the customer to pick an address_id, or confirmation
        that the address was deleted.
    """
    try:
        if not address_id:
            addresses = _fetch_saved_addresses(mobile)
            if not addresses:
                return ("NO_ADDRESSES: This customer has no saved addresses to delete. "
                         "Tell them this clearly and do not proceed further.")
            return (f"NEEDS_INFO: Saved addresses: {addresses}. Ask the customer which "
                     f"one to delete (by address_id), and confirm they really want to "
                     f"delete it before calling delete_customer_address again with that address_id.")

        response = requests.post(f"{BASE_URL}/deleteaddress.php", json={
            "mobile": mobile, "address_id": address_id
        }, timeout=10)
        response.raise_for_status()
        return f"Address {address_id} deleted successfully. {response.text}"
    except requests.exceptions.RequestException as e:
        return f"Error deleting address: {str(e)}"


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
@tool
def place_order(
    mobile: str,
    address_id: int = 0,
    new_address: str = "",
    new_locality: str = "",
    new_city: str = "",
    new_pincode: str = "",
    new_address2: str = "",
    pickup_day: str = "",
    pickup_time: int = 0,
) -> str:
    """
    Place a new order (schedule a fabric pickup) for a customer. Handles the full flow:
    checking/adding a delivery address, resolving the pickup date, and scheduling pickup.

    Call this progressively as you gather info from the customer — you don't need
    every argument on the first call. If something is missing, this tool tells you
    exactly what to ask the customer next; call it again once you have that answer.

    Args:
        mobile: Customer's mobile number. Always required.
        address_id: ID of a previously saved address the customer selected. Leave as 0
            if the customer hasn't picked a saved address yet.
        new_address: Address line 1, only needed if customer has no saved addresses
            and is adding a new one (e.g., "Flat 302, Green Meadows").
        new_locality: Locality/neighborhood for a new address (e.g., "Kondapur").
        new_city: City for a new address (e.g., "Hyderabad").
        new_pincode: 6-digit pincode for a new address.
        new_address2: Optional landmark/second line for a new address.
        pickup_day: Customer's chosen pickup day — must be exactly "today", "tomorrow",
            or "day after tomorrow". Leave blank if not yet chosen.
        pickup_time: Time slot — 1 for morning (9 AM-11 AM), 2 for afternoon (2 PM-5 PM).
            Leave as 0 if not yet chosen.

    Returns:
        Either a request for missing info (e.g., list of saved addresses to choose from),
        or the final order confirmation once everything is scheduled.
    """
    try:
        resolved_address_id, needs_info = _resolve_address_or_prompt(
            mobile, address_id, new_address, new_locality, new_city, new_pincode,
            new_address2, next_step_hint="call place_order again with that address_id"
        )
        if needs_info:
            return needs_info

        if not pickup_day:
            return ("NEEDS_INFO: Ask the customer to choose a pickup day: today, tomorrow, "
                     "or day after tomorrow. Then call place_order again with pickup_day set, "
                     "along with the address_id already resolved.")

        pickup_date_str = _resolve_pickup_date(pickup_day)
        if not pickup_date_str:
            return ('NEEDS_INFO: pickup_day must be exactly "today", "tomorrow", or '
                     '"day after tomorrow". Ask the customer again.')

        if pickup_time not in (1, 2):
            return ("NEEDS_INFO: Ask the customer to choose a time slot: 1 for morning "
                     "(9 AM-11 AM) or 2 for afternoon (2 PM-5 PM). Then call place_order "
                     "again with pickup_time set, along with address_id and pickup_day.")

        response = requests.post(f"{BASE_URL}/schedulepickup.php", json={
            "mobile": mobile,
            "pickup_date": pickup_date_str,
            "pickup_time": pickup_time,
            "address_id": resolved_address_id
        }, timeout=10)
        response.raise_for_status()
        data = response.json()

        slot_label = "9 AM - 11 AM" if pickup_time == 1 else "2 PM - 5 PM"
        return (f"Order placed successfully. Pickup scheduled for {pickup_date_str}, "
                f"{slot_label}. Details: {data}")

    except requests.exceptions.RequestException as e:
        return f"Order failed at API call: {str(e)}"
    except (KeyError, ValueError) as e:
        return f"Order failed: unexpected response format — {str(e)}"


@tool
def get_order_status(mobile: str) -> str:
    """
    Fetch the current status of a customer's order(s) by mobile number.
    Use this when a customer asks about their order status, tracking,
    or "where is my order".

    Args:
        mobile: The customer's mobile number (e.g., "9701667788").

    Returns:
        The current order status details, or an error message if the lookup fails.
    """
    try:
        response = requests.get(f"{BASE_URL}/orderstatus.php", params={"mobile": mobile}, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        return f"Error fetching order status: {str(e)}"


@tool
def modify_order(mobile: str, order_id: int, comment: str) -> str:
    """
    Request a modification to an existing order (e.g., a design/fitting change).
    Call get_order_status first if you don't already know the order_id.

    Args:
        mobile: The customer's mobile number, including country code (e.g., "919640864111").
        order_id: The ID of the order to modify. Get this from get_order_status if unknown.
        comment: Description of the requested change (e.g., "Please make the sleeves half instead of full").

    Returns:
        Confirmation that the modification request was submitted, or an error message.
    """
    try:
        response = requests.get(f"{BASE_URL}/modifyorder.php", params={
            "mobile": mobile, "comment": comment, "order_id": order_id
        }, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        return f"Error modifying order: {str(e)}"


@tool
def cancel_order(mobile: str, order_id: int, reason: str) -> str:
    """
    Cancel an existing order. Use this only when the customer explicitly confirms
    they want to cancel — always double-check with the customer before calling this,
    since cancellation cannot be undone. Call get_order_status first if you don't
    already know the order_id.

    Args:
        mobile: The customer's mobile number, including country code (e.g., "919701667788").
        order_id: The ID of the order to cancel. Get this from get_order_status if unknown.
        reason: The customer's reason for cancelling (e.g., "Changed my mind").

    Returns:
        Confirmation that the order was cancelled, or an error message.
    """
    try:
        response = requests.post(f"{BASE_URL}/cancelorder.php", json={
            "mobile": mobile, "order_id": order_id, "reason": reason
        }, timeout=10)
        response.raise_for_status()
        return str(response.json())
    except requests.exceptions.RequestException as e:
        return f"Error cancelling order: {str(e)}"


@tool
def alteration_pickup(
    mobile: str,
    order_id: int = 0,
    address_id: int = 0,
    new_address: str = "",
    new_locality: str = "",
    new_city: str = "",
    new_pincode: str = "",
    new_address2: str = "",
    pickup_day: str = "",
    pickup_time: int = 0,
    notes: str = "",
) -> str:
    """
    Schedule an alteration pickup for an eligible order. Handles the full flow:
    checking alteration-eligible orders, resolving pickup address, resolving
    pickup date/time, and booking the pickup.

    Call this progressively as you gather info from the customer — you don't need
    every argument on the first call. If something is missing, this tool tells you
    exactly what to ask the customer next; call it again once you have that answer.

    Args:
        mobile: Customer's mobile number. Always required.
        order_id: The order the customer wants altered. Leave as 0 until the customer
            has picked one from the eligible orders list.
        address_id: ID of a previously saved address the customer selected. Leave as 0
            if not yet chosen.
        new_address: Address line 1, only needed if customer has no saved addresses
            and is adding a new one.
        new_locality: Locality/neighborhood for a new address.
        new_city: City for a new address.
        new_pincode: 6-digit pincode for a new address.
        new_address2: Optional landmark/second line for a new address.
        pickup_day: Customer's chosen pickup day — must be exactly "today", "tomorrow",
            or "day after tomorrow". Leave blank if not yet chosen.
        pickup_time: Time slot — 1 for morning (9 AM-11 AM), 2 for afternoon (2 PM-5 PM).
            Leave as 0 if not yet chosen.
        notes: Description of the alteration needed (e.g., "Shorten sleeves by 1 inch").
            Required before booking.

    Returns:
        Either a request for missing info (e.g., list of eligible orders or saved
        addresses to choose from), or the final pickup confirmation once booked.
    """
    try:
        if not order_id:
            elig_resp = requests.get(f"{BASE_URL}/alterationeligibleorders.php",
                                      params={"mobile": mobile}, timeout=10)
            elig_resp.raise_for_status()
            try:
                eligible_orders = elig_resp.json()
            except ValueError:
                eligible_orders = None

            if not eligible_orders:
                return ("NO_ELIGIBLE_ORDERS: This customer has no orders currently "
                         "eligible for alteration. Tell them this clearly and do not "
                         "proceed further with this request.")

            return (f"NEEDS_INFO: Eligible orders found: {eligible_orders}. Ask the "
                     f"customer to pick one (by order_id), then call alteration_pickup "
                     f"again with that order_id.")

        resolved_address_id, needs_info = _resolve_address_or_prompt(
            mobile, address_id, new_address, new_locality, new_city, new_pincode,
            new_address2,
            next_step_hint=f"call alteration_pickup again with order_id={order_id} and that address_id"
        )
        if needs_info:
            return needs_info

        if not pickup_day:
            return ("NEEDS_INFO: Ask the customer to choose a pickup day: today, "
                     "tomorrow, or day after tomorrow. Then call alteration_pickup "
                     "again with pickup_day set, along with order_id and address_id "
                     "already resolved.")

        pickup_date_str = _resolve_pickup_date(pickup_day)
        if not pickup_date_str:
            return ('NEEDS_INFO: pickup_day must be exactly "today", "tomorrow", or '
                     '"day after tomorrow". Ask the customer again.')

        if pickup_time not in (1, 2):
            return ("NEEDS_INFO: Ask the customer to choose a time slot: 1 for morning "
                     "(9 AM-11 AM) or 2 for afternoon (2 PM-5 PM). Then call "
                     "alteration_pickup again with pickup_time set, along with order_id, "
                     "address_id, and pickup_day.")

        if not notes:
            return ("NEEDS_INFO: Ask the customer to describe the alteration needed "
                     "(e.g., 'shorten sleeves by 1 inch'). Then call alteration_pickup "
                     "again with notes set, along with everything already collected.")

        response = requests.post(f"{BASE_URL}/alterationpickup.php", json={
            "mobile": mobile,
            "pickup_date": pickup_date_str,
            "pickup_time": pickup_time,
            "address_id": resolved_address_id,
            "order_id": order_id,
            "notes": notes
        }, timeout=10)
        response.raise_for_status()
        data = response.json()

        slot_label = "9 AM - 11 AM" if pickup_time == 1 else "2 PM - 5 PM"
        return (f"Alteration pickup booked successfully for order {order_id} on "
                f"{pickup_date_str}, {slot_label}. Details: {data}")

    except requests.exceptions.RequestException as e:
        return f"Alteration pickup failed at API call: {str(e)}"
    except (KeyError, ValueError) as e:
        return f"Alteration pickup failed: unexpected response format — {str(e)}"


# ---------------------------------------------------------------------------
# Fabric drop-off
# ---------------------------------------------------------------------------
@tool
def drop_off_fabric(mobile: str, notes: str) -> str:
    """
    Register a fabric drop-off/shipment from the customer to the store.
    Use this when a customer wants to send/ship their fabric to us instead of
    scheduling a pickup. Store is fixed to the default location.

    Args:
        mobile: The customer's mobile number.
        notes: Description of the fabric being sent (e.g., "3 meters of cotton fabric for a shirt").

    Returns:
        Confirmation of the fabric drop-off registration, including a reminder
        to mark the client ID on the package, or an error message if it fails.
    """
    try:
        client_resp = requests.get(f"{BASE_URL}/getclient.php", params={"mobile": mobile}, timeout=10)
        client_resp.raise_for_status()
        try:
            client_data = client_resp.json()
        except ValueError:
            client_data = {}
        client_id = client_data.get("client_id")

        response = requests.post(f"{BASE_URL}/fabricdelivery.php", params={
            "mobile": mobile, "store_id": 1, "notes": notes
        }, timeout=10)
        response.raise_for_status()

        client_id_line = (
            f"Kindly mention your client ID ({client_id}) next to your name on the "
            f"pack while sending/shipping us the items."
            if client_id else
            "Kindly mention your client ID next to your name on the pack while "
            "sending/shipping us the items."
        )
        return f"Fabric drop-off registered successfully. {response.text}\n\n{client_id_line}"

    except requests.exceptions.RequestException as e:
        return f"Error registering fabric drop-off: {str(e)}"


# ---------------------------------------------------------------------------
# Export list
# ---------------------------------------------------------------------------
tools = [
    get_client_type,
    get_company_faq,
    register_client,
    book_appointment,
    human_handover,
    get_customer_addresses,
    add_customer_address,
    delete_customer_address,
    place_order,
    get_order_status,
    modify_order,
    cancel_order,
    alteration_pickup,
    drop_off_fabric,
]