from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from jarvis_scout.manual import create_manual_listing, validate_manual_listing


def observation(**changes):
    values = dict(asin="B0REAL0001", title="Observed listing", brand="Known brand", ean="9990000000012",
                  pack_quantity=1, price=Decimal("29.99"), referral_fee_rate=Decimal("0.15"),
                  fulfilment_fee=Decimal("3.00"), observed_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                  evidence_source="Amazon UK listing inspected by me; screenshot retained locally",
                  identifier_verified=True, fees_confirmed=True)
    values.update(changes)
    return create_manual_listing(**values)


def test_manual_observation_preserves_unknowns_and_provenance():
    listing = observation(price=None, referral_fee_rate=None, fulfilment_fee=None, fees_confirmed=False)
    assert listing.price is None and listing.referral_fee_rate is None and listing.fba_fee is None
    assert listing.demand_confidence is None and listing.pricing_stability is None and listing.competition is None
    assert listing.restriction_status == "unknown"
    assert not listing.simulated and listing.provenance == "manual"
    validate_manual_listing(listing)


def test_asin_is_normalised_without_filling_in_product_identity():
    listing = observation(asin=" b0real0001 ", title="", brand="", ean=None, identifier_verified=False)
    assert listing.asin == "B0REAL0001"
    assert listing.title == "Manual observation B0REAL0001"
    assert listing.brand == "" and listing.ean is None and not listing.identifier_verified


@pytest.mark.parametrize("changes, message", [
    ({"asin": "123"}, "ASIN"),
    ({"ean": "9990000000013"}, "check digit"),
    ({"price": Decimal("0")}, "positive"),
    ({"price": Decimal("NaN")}, "price"),
    ({"fulfilment_fee": Decimal("-1")}, "fba_fee"),
    ({"referral_fee_rate": Decimal("1.1")}, "referral_fee_rate"),
    ({"evidence_source": ""}, "evidence"),
    ({"evidence_source": "x" * 2001}, "evidence"),
    ({"evidence_source": "https://secret:credential@example.com/item"}, "credentials"),
    ({"evidence_source": "https://example.com:bad/item"}, "Evidence URL"),
    ({"observed_at": "2026-10-11"}, "timezone"),
    ({"observed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, "future"),
    ({"pack_quantity": 0}, "pack_quantity"),
    ({"identifier_verified": "yes"}, "identifier_verified"),
    ({"fees_confirmed": "yes"}, "fees_confirmed"),
])
def test_malformed_observations_are_rejected(changes, message):
    with pytest.raises(ValueError, match=message):
        observation(**changes)
