import customer_segment


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    @property
    def text(self):
        return "{}"


def test_classify_customer_caches_results(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=10):
        calls.append(params)
        return DummyResponse({"type": "client", "client": {"id": "123", "cname": "Alice"}})

    monkeypatch.setattr(customer_segment.requests_session, "get", fake_get)
    customer_segment._classification_cache.clear()

    first = customer_segment.classify_customer("919701667788")
    second = customer_segment.classify_customer("919701667788")

    assert first.segment == "client"
    assert second.segment == "client"
    assert len(calls) == 1
