import copy
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import sqlite3
import threading

import httpx
import pytest

from jarvis_scout.notifications import (
    AlertService,
    NotificationError,
    TelegramNotifier,
    format_deal_message,
)


@pytest.fixture
def deal():
    return {
        "product_key": "supplier-a:123",
        "qualified": True,
        "simulated": False,
        "match": {"status": "exact"},
        "estimate_status": "complete",
        "supplier": {"title": "Useful product <test>", "unit_cost": "10.00", "is_demo": False,
                     "supplier_url": "https://supplier.example/product/123"},
        "listing": {"asin": "B012345678", "price": "24.99", "simulated": False,
                    "referral_fee_rate": "0.15", "fba_fee": "3.0", "fees_confirmed": True,
                    "identifier_verified": True, "provenance": "manual", "restriction_status": "unknown",
                    "observed_at": "2026-10-11T12:00:00+00:00", "evidence_source": "Manual listing observation"},
        "profitability": {"profit": "7.15", "roi_percent": "62.1", "margin_percent": "28.6"},
    }


def test_telegram_plaintext_and_secret_redaction(deal, caplog):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        notifier = TelegramNotifier("123:secret-token", "chat-id", client=client)
        with caplog.at_level(logging.INFO, logger="httpx"):
            notifier.send(deal)
    payload = json.loads(requests[0].content)
    assert payload["chat_id"] == "chat-id"
    assert "parse_mode" not in payload
    assert "Useful product <test>" in payload["text"]
    assert "£7.15" in payload["text"]
    assert "62.1%" in payload["text"]
    assert "https://www.amazon.co.uk/dp/B012345678" in payload["text"]
    assert "secret-token" not in caplog.text


def test_simulation_disclosure_and_safe_links(deal):
    deal["simulated"] = True
    deal["supplier"]["is_demo"] = True
    deal["listing"].update(simulated=True, provenance="mock")
    deal["supplier"]["supplier_url"] = "http://unsafe.example/product"
    deal["listing"]["listing_url"] = "javascript:alert(1)"
    message = format_deal_message(deal)
    assert "SIMULATED DEMO" in message
    assert "Demonstration only" in message
    assert "unsafe.example" not in message
    assert "javascript" not in message


@pytest.mark.parametrize("explicit_url", [None, "https://www.amazon.co.uk/dp/B012345678"])
def test_simulated_asins_never_link_to_real_amazon_products(deal, explicit_url):
    deal["simulated"] = True
    deal["supplier"]["is_demo"] = True
    deal["listing"].update(simulated=True, provenance="mock")
    deal["listing"]["listing_url"] = explicit_url
    message = format_deal_message(deal)
    assert "Simulated ASIN: B012345678" in message
    assert "amazon.co.uk" not in message
    assert "Amazon: https://" not in message


@pytest.mark.parametrize("url", ["https://user:secret@example.com/p", "https://bad.example:wrong/p",
                                     "ftp://bad.example/p", "https://bad.example/p\ntext"])
def test_invalid_supplier_links_are_omitted(deal, url):
    deal["supplier"]["supplier_url"] = url
    assert "Supplier:" not in format_deal_message(deal)


def test_missing_credentials_never_attempt_network(deal, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    notifier = TelegramNotifier()
    assert not notifier.configured
    with pytest.raises(NotificationError, match="credentials are missing"):
        notifier.send(deal)


@pytest.mark.parametrize("status,payload,uncertain", [
    (401, {"description": "contains-secret"}, False),
    (200, {"ok": False, "description": "contains-secret"}, False),
    (503, {"description": "contains-secret"}, True),
    (200, {"description": "contains-secret"}, True),
])
def test_upstream_errors_do_not_expose_credentials_or_response(deal, status, payload, uncertain):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload))) as client:
        notifier = TelegramNotifier("123:private-secret", "chat", client=client)
        with pytest.raises(NotificationError) as caught:
            notifier.send(deal)
    assert caught.value.uncertain is uncertain
    assert "private-secret" not in str(caught.value)
    assert "contains-secret" not in str(caught.value)


@pytest.mark.parametrize("error_type,uncertain", [(httpx.ConnectError, False), (httpx.ReadTimeout, True)])
def test_network_errors_are_sanitized(deal, error_type, uncertain):
    def fail(request):
        raise error_type("sensitive-token-and-url", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(NotificationError) as caught:
            TelegramNotifier("123:private", "chat", client=client).send(deal)
    assert "sensitive" not in str(caught.value)
    assert caught.value.uncertain is uncertain


def test_unexpected_transport_errors_are_sanitized(deal):
    def fail(request):
        raise RuntimeError(f"secret upstream detail from {request.url}")

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(NotificationError) as caught:
            TelegramNotifier("123:private", "chat", client=client).send(deal)
    assert caught.value.uncertain
    assert "private" not in str(caught.value)
    assert "upstream detail" not in str(caught.value)


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, opportunity):
        self.sent.append(opportunity)


def test_cooldown_persists_and_expires(tmp_path, deal):
    database = tmp_path / "alerts.sqlite3"
    current_time = [1000.0]
    notifier = RecordingNotifier()
    assert AlertService(database, notifier, clock=lambda: current_time[0]).dispatch([deal])["sent"] == 1
    new_service = AlertService(database, notifier, clock=lambda: current_time[0])
    assert new_service.dispatch([deal, deal])["suppressed"] == 2
    current_time[0] += 24 * 3600
    assert new_service.dispatch([deal])["sent"] == 1
    assert len(notifier.sent) == 2


def test_simulated_and_unqualified_never_sent_without_opt_in(tmp_path, deal):
    simulated = copy.deepcopy(deal)
    simulated["listing"]["simulated"] = True
    unqualified = {**deal, "qualified": False}
    uncertain_match = {**deal, "match": {"status": "manual_review"}}
    notifier = RecordingNotifier()
    service = AlertService(tmp_path / "alerts.db", notifier)
    result = service.dispatch([simulated, unqualified, uncertain_match])
    assert result["skipped_simulated"] == 1
    assert result["skipped_unqualified"] == 2
    assert not notifier.sent
    # A real supplier must never trigger a mock-price alert, even after demo opt-in.
    assert AlertService(tmp_path / "alerts.db", notifier, allow_simulated=True).dispatch([simulated])["skipped_simulated"] == 1
    simulated["supplier"]["is_demo"] = True
    simulated["listing"]["provenance"] = "mock"
    simulated["simulated"] = True
    assert AlertService(tmp_path / "alerts.db", notifier, allow_simulated=True).dispatch([simulated])["sent"] == 1


def test_missing_provenance_is_simulated(tmp_path, deal):
    del deal["simulated"]
    result = AlertService(tmp_path / "alerts.db", RecordingNotifier()).dispatch([deal])
    assert result["skipped_simulated"] == 1


def test_definite_failure_allows_retry_but_uncertain_delivery_is_cooled_down(tmp_path, deal):
    class FlakyNotifier:
        error = NotificationError("Rejected")

        def send(self, opportunity):
            if self.error is not None:
                raise self.error

    notifier = FlakyNotifier()
    service = AlertService(tmp_path / "retry.db", notifier)
    assert service.dispatch([deal])["failed"] == 1
    notifier.error = None
    assert service.dispatch([deal])["sent"] == 1
    uncertain = AlertService(tmp_path / "uncertain.db", notifier)
    notifier.error = NotificationError("Uncertain", uncertain=True)
    assert uncertain.dispatch([deal])["failed"] == 1
    notifier.error = None
    assert uncertain.dispatch([deal])["suppressed"] == 1


def test_two_scan_instances_reserve_before_network(tmp_path, deal):
    entered = threading.Event()
    release = threading.Event()

    class BlockingNotifier:
        def send(self, opportunity):
            entered.set()
            assert release.wait(5)

    database = tmp_path / "concurrent.db"
    first = AlertService(database, BlockingNotifier())
    second = AlertService(database, RecordingNotifier())
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.dispatch, [deal])
        try:
            assert entered.wait(5)
            assert second.dispatch([deal])["suppressed"] == 1
        finally:
            release.set()
        assert pending.result(timeout=5)["sent"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*), status FROM alert_events").fetchone() == (1, "sent")


def test_corrupted_identity_is_rejected(tmp_path, deal):
    deal["product_key"] = ""
    assert AlertService(tmp_path / "alerts.db", RecordingNotifier()).dispatch([deal])["invalid"] == 1


def test_manual_alert_clearly_reports_observation_and_unknown_eligibility(deal):
    deal["research_warnings"] = ["Demand and pricing stability are unknown"]
    message = format_deal_message(deal)
    assert "REAL SUPPLIER · MANUAL AMAZON OBSERVATION" in message
    assert "Manual listing observation" in message
    assert "2026-10-11T12:00:00+00:00" in message
    assert "Seller eligibility is unknown" in message
    assert "Demand and pricing stability are unknown" in message
    assert "verified deal" not in message.lower()


@pytest.mark.parametrize("field,value", [
    ("identifier_verified", False), ("fees_confirmed", False), ("evidence_source", ""),
    ("price", None), ("referral_fee_rate", None), ("fba_fee", "NaN"),
    ("price", "-1"), ("referral_fee_rate", "1.1"), ("fba_fee", "-1"),
    ("restriction_status", "restricted"),
])
def test_manual_alert_requires_complete_explicit_evidence(tmp_path, deal, field, value):
    deal["listing"][field] = value
    notifier = RecordingNotifier()
    result = AlertService(tmp_path / "alerts.db", notifier).dispatch([deal])
    assert result["skipped_unqualified"] == 1
    assert not notifier.sent


def test_incomplete_estimate_never_alerts(tmp_path, deal):
    deal["estimate_status"] = "incomplete"
    assert AlertService(tmp_path / "alerts.db", RecordingNotifier()).dispatch([deal])["skipped_unqualified"] == 1


def test_missing_explicit_source_is_not_a_real_alert(tmp_path, deal):
    del deal["supplier"]["is_demo"]
    assert AlertService(tmp_path / "alerts.db", RecordingNotifier(), allow_simulated=True).dispatch([deal])["skipped_simulated"] == 1


def test_direct_telegram_notifier_rejects_real_supplier_mock_combination(deal):
    deal["listing"].update(simulated=True, provenance="mock")
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        with pytest.raises(NotificationError, match="Mixed, unverified or incomplete"):
            TelegramNotifier("123:private", "chat", client=client).send(deal)
    assert not requests


def test_direct_telegram_notifier_rejects_an_uncertain_match(deal):
    deal["match"]["status"] = "review"
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        with pytest.raises(NotificationError, match="Mixed, unverified or incomplete"):
            TelegramNotifier("123:private", "chat", client=client).send(deal)
    assert not requests
