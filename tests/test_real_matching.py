"""Real research cannot gain confidence from synthetic or absent evidence."""
from dataclasses import replace
from decimal import Decimal

import pytest

from jarvis_scout.connectors.amazon import validate_listing
from jarvis_scout.filters import OpportunityFilters, evaluate_opportunity
from jarvis_scout.matching import match_product
from jarvis_scout.models import AmazonListing, SupplierProduct
from jarvis_scout.profitability import calculate_profit


@pytest.fixture
def supplier():
    return SupplierProduct(
        source="UK Supplier", sku="BOX-1", title="Storage box", brand="Northline",
        model="", variation="", ean="4006381333931", pack_quantity=1,
        unit_cost=Decimal("8"), delivery_cost=Decimal("1"),
        supplier_url="https://supplier.example/box", is_demo=False,
        stock_status="in_stock", vat_treatment="included",
    )


@pytest.fixture
def observation():
    return AmazonListing(
        asin="B000000001", title="Storage box", brand="Northline",
        model="", variation="", ean="4006381333931", pack_quantity=1,
        price=Decimal("30"), referral_fee_rate=Decimal("0.15"), fba_fee=Decimal("3"),
        demand_confidence=None, pricing_stability=None, competition=None,
        restriction_status="unknown", simulated=False, provenance="manual",
        evidence_source="Amazon UK page checked by user", identifier_verified=True,
        fees_confirmed=True,
    )


def test_verified_gtin_brand_pack_can_match_without_an_inapplicable_model(supplier, observation):
    validate_listing(observation)
    assert match_product(supplier, [observation]).status == "exact"


@pytest.mark.parametrize("changes", [
    {"identifier_verified": False}, {"brand": "Other brand"}, {"brand": ""},
    {"pack_quantity": 2}, {"model": "M1"}, {"variation": "Blue"},
    {"ean": "4006381333932"},
])
def test_real_match_needs_confirmed_consistent_identity(supplier, observation, changes):
    assert match_product(supplier, [replace(observation, **changes)]).status == "review"


@pytest.mark.parametrize("field", ["price", "referral_fee_rate", "fba_fee"])
def test_manual_missing_money_stays_unknown_not_zero(observation, field):
    missing = replace(observation, **{field: None})
    validate_listing(missing)
    assert getattr(missing, field) is None


@pytest.mark.parametrize("field", ["price", "referral_fee_rate", "fba_fee",
                                       "demand_confidence", "pricing_stability", "competition"])
def test_mock_catalog_still_requires_complete_synthetic_values(observation, field):
    mock = replace(observation, simulated=True, provenance="mock",
                   demand_confidence=.8, pricing_stability=.8, competition=3,
                   **{field: None}) if field not in {"demand_confidence", "pricing_stability", "competition"} else replace(
        observation, simulated=True, provenance="mock", **{field: None})
    with pytest.raises(ValueError):
        validate_listing(mock)


@pytest.mark.parametrize("changes", [
    {"provenance": "mock"}, {"simulated": True}, {"evidence_source": "  "},
    {"identifier_verified": 1}, {"fees_confirmed": "true"}, {"provenance": "scraped"},
    {"price": Decimal("NaN")}, {"fba_fee": Decimal("-1")},
    {"referral_fee_rate": Decimal("1.1")}, {"demand_confidence": Decimal("NaN")},
    {"pricing_stability": 2}, {"competition": -1},
])
def test_manual_observation_structure_is_validated(observation, changes):
    with pytest.raises(ValueError):
        validate_listing(replace(observation, **changes))


def test_unknown_market_data_does_not_establish_a_market_opportunity(supplier, observation):
    profit = calculate_profit(supplier, observation)
    qualified, reasons, score = evaluate_opportunity(supplier, observation, profit, OpportunityFilters())
    assert not qualified
    assert "Demand confidence is unknown" in reasons
    assert "Pricing stability is unknown" in reasons
    assert "Competition is unknown" in reasons
    assert score == 0


def test_manual_research_can_filter_on_complete_finances_without_market_claims(supplier, observation):
    profit = calculate_profit(supplier, observation)
    filters = OpportunityFilters(require_eligible=False, require_market_signals=False)
    qualified, reasons, score = evaluate_opportunity(supplier, observation, profit, filters)
    assert qualified
    assert reasons == []
    # Only profit and ROI contribute. Unknown competition never adds points.
    expected = round(100 * (.25 * min(float(profit.roi_percent) / 100, 1)
                            + .25 * min(float(profit.profit) / 15, 1)), 1)
    assert score == expected
    assert observation.demand_confidence is observation.competition is None


def test_explicit_restriction_is_never_optional(supplier, observation):
    observation = replace(observation, restriction_status="restricted")
    qualified, reasons, score = evaluate_opportunity(
        supplier, observation, calculate_profit(supplier, observation),
        OpportunityFilters(require_eligible=False, require_market_signals=False),
    )
    assert not qualified
    assert "Listing eligibility is restricted or unverified" in reasons
    assert score == 0


@pytest.mark.parametrize("stock_status", ["out_of_stock", "preorder"])
def test_unavailable_supplier_stock_cannot_qualify(supplier, observation, stock_status):
    supplier = replace(supplier, stock_status=stock_status)
    qualified, reasons, _ = evaluate_opportunity(
        supplier, observation, calculate_profit(supplier, observation),
        OpportunityFilters(require_eligible=False, require_market_signals=False),
    )
    assert not qualified
    assert "Supplier reports out of stock" in reasons


def test_reported_stock_must_cover_the_supplier_minimum_order(supplier, observation):
    supplier = replace(supplier, minimum_order_quantity=24, available_quantity=12)
    qualified, reasons, _ = evaluate_opportunity(
        supplier, observation, calculate_profit(supplier, observation),
        OpportunityFilters(require_eligible=False, require_market_signals=False),
    )
    assert not qualified
    assert "Supplier stock is below minimum order quantity" in reasons


@pytest.mark.parametrize("field", ["require_eligible", "require_market_signals"])
def test_filter_switches_require_explicit_booleans(field):
    with pytest.raises(ValueError, match=field):
        OpportunityFilters(**{field: "false"})
