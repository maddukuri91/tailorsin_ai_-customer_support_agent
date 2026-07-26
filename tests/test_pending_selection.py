from state import set_pending_selection, get_pending_selection, clear_pending_selection


def test_pending_selection_round_trip():
    thread_id = "telegram:9999999999"
    clear_pending_selection(thread_id)

    set_pending_selection(thread_id, "cancel_order_order", {"mobile": "9999999999"})

    pending = get_pending_selection(thread_id)
    assert pending is not None
    assert pending["type"] == "cancel_order_order"
    assert pending["payload"]["mobile"] == "9999999999"

    clear_pending_selection(thread_id)
    assert get_pending_selection(thread_id) is None
