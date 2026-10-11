"""Identity safety regression checks: similar listings are not interchangeable."""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from jarvis_scout.connectors.amazon import MockAmazonAdapter, OfficialAmazonAdapter, validate_listing
from jarvis_scout.matching import match_product, normalize_gtin
from jarvis_scout.models import AmazonListing, SupplierProduct


@pytest.fixture
def product():
    return SupplierProduct(
        source="demo", sku="SKU-1", title="Northline storage box",
        brand="Northline Storage", model="NLS-750", ean="9990000000012",
        variation="Teal", pack_quantity=1, unit_cost=Decimal("9.99"),
        delivery_cost=Decimal("0.50"), supplier_url="https://example.com/demo",
    )


@pytest.fixture
def listing():
    return AmazonListing(
        asin="B0DEMO0001", title="Northline storage box", brand="Northline Storage",
        model="NLS-750", ean="9990000000012", variation="Teal", pack_quantity=1,
        price=Decimal("29.99"), referral_fee_rate=Decimal("0.15"),
        fba_fee=Decimal("3.20"), demand_confidence=0.82, pricing_stability=0.88,
        competition=4, restriction_status="eligible", simulated=True,
    )


@pytest.mark.parametrize("identifier", ["96385074", "036000291452", "4006381333931", "04006381333931"])
def test_supported_gtin_lengths(identifier):
    assert normalize_gtin(identifier) == identifier.zfill(14)


@pytest.mark.parametrize("identifier", [None, "", "00000000", "9990000000013", "1234", "1234X678", "４００６３８１３３３９３１"])
def test_invalid_identifiers(identifier):
    assert normalize_gtin(identifier) is None


def test_exact_match_requires_shared_identifier_and_sellable_identity(product, listing):
    result = match_product(product, [listing])
    assert result.status == "exact"
    assert result.listing == listing


def test_equivalent_zero_padded_gtin_can_match(product, listing):
    assert match_product(product, [replace(listing, ean="09990000000012")]).status == "exact"


@pytest.mark.parametrize("change", [
    {"brand": "Other Brand"}, {"model": "NLS-800"},
    {"variation": "Blue"}, {"pack_quantity": 3},
    {"brand": ""}, {"model": ""}, {"variation": ""},
])
def test_identifier_never_overrides_conflicting_or_missing_attributes(product, listing, change):
    result = match_product(product, [replace(listing, **change)])
    assert result.status == "review"
    assert len(result.reasons) > 1


def test_pack_conversion_is_never_automatic(product, listing):
    result = match_product(product, [replace(listing, pack_quantity=3)])
    assert result.status == "review"
    assert "no pack conversion" in " ".join(result.reasons)


def test_meaningful_variation_punctuation_is_not_erased(product, listing):
    product = replace(product, variation="Size 1/2")
    result = match_product(product, [replace(listing, variation="Size 1-2")])
    assert result.status == "review"


@pytest.mark.parametrize("identifier", [None, "9990000000013"])
def test_missing_or_bad_check_digit_requires_review(product, listing, identifier):
    result = match_product(replace(product, ean=identifier), [listing])
    assert result.status == "review"
    assert "invalid check digit" in " ".join(result.reasons)


def test_distinct_valid_identifiers_cannot_be_overridden_by_identical_text(product, listing):
    result = match_product(product, [replace(listing, ean="9990000000029")])
    assert result.status == "unmatched"
    assert result.listing is None


def test_multiple_asins_with_shared_identifier_require_review(product, listing):
    result = match_product(product, [listing, replace(listing, asin="B0DEMO0999")])
    assert result.status == "review"
    assert result.listing is None
    assert "Multiple" in result.reasons[0]


def test_multiple_missing_identifier_candidates_require_review(product, listing):
    result = match_product(replace(product, ean=None), [listing, replace(listing, asin="B0DEMO0999")])
    assert result.status == "review"
    assert result.listing is None


def test_repeated_identical_asin_does_not_manufacture_ambiguity(product, listing):
    assert match_product(product, [listing, listing]).status == "exact"


def test_conflicting_records_for_same_asin_do_not_silently_use_first_price(product, listing):
    result = match_product(product, [listing, replace(listing, price=Decimal("49.99"))])
    assert result.status == "review"
    assert result.listing is None
    assert "Conflicting catalog records" in result.reasons[0]


def test_supplier_sku_is_not_a_cross_source_product_identifier(product, listing):
    product = replace(product, sku=listing.asin, ean=None, title="Unknown", brand="Other", model="Different")
    assert match_product(product, [listing]).status == "unmatched"


def test_no_candidates_are_unmatched(product):
    assert match_product(product, []).status == "unmatched"


def test_mock_adapter_returns_decimal_simulated_data(product):
    results = MockAmazonAdapter().search(product)
    assert len(results) == 1
    assert isinstance(results[0].price, Decimal)
    assert results[0].simulated is True
    assert match_product(product, results).status == "exact"


def test_mock_adapter_cannot_mark_catalog_as_verified(tmp_path, product, listing):
    from jarvis_scout.models import json_safe
    path = tmp_path / "mock.json"
    path.write_text(json.dumps([json_safe(replace(listing, simulated=False))]), encoding="utf-8")
    assert MockAmazonAdapter(path).search(product)[0].simulated is True


def test_live_adapter_is_explicitly_unimplemented(product):
    with pytest.raises(NotImplementedError, match="Live Amazon integration"):
        OfficialAmazonAdapter().search(product)


@pytest.mark.parametrize("change", [
    {"price": Decimal("NaN")}, {"price": Decimal("Infinity")},
    {"price": Decimal("-0.01")}, {"price": 10.0},
    {"fba_fee": Decimal("-1")}, {"fba_fee": Decimal("NaN")},
    {"referral_fee_rate": Decimal("1.01")}, {"referral_fee_rate": Decimal("-0.01")},
    {"referral_fee_rate": Decimal("Infinity")},
    {"demand_confidence": float("nan")}, {"demand_confidence": 1.1},
    {"demand_confidence": -0.1}, {"demand_confidence": "0.8"},
    {"pricing_stability": float("inf")}, {"pricing_stability": True},
    {"pricing_stability": Decimal("NaN")}, {"pricing_stability": Decimal("sNaN")},
    {"pricing_stability": 10**1000},
    {"competition": -1}, {"competition": 2.5}, {"competition": True},
    {"pack_quantity": 0}, {"pack_quantity": 1.5}, {"pack_quantity": True},
    {"restriction_status": "unrestricted"}, {"restriction_status": ["eligible"]},
    {"simulated": "false"}, {"simulated": 1},
    {"asin": ""}, {"asin": "B0DEMO000"}, {"asin": "b0demo0001"},
    {"asin": "B0DEMO000!"}, {"title": "  "}, {"title": None},
    {"brand": None}, {"model": 12}, {"variation": False}, {"ean": 9990000000012},
    {"observed_at": "yesterday"}, {"observed_at": "2026-10-11T10:00:00"},
    {"observed_at": None},
])
def test_corrupt_adapter_evidence_is_rejected(listing, change):
    with pytest.raises(ValueError):
        validate_listing(replace(listing, **change))


def test_missing_identity_fields_remain_available_for_manual_review(product, listing):
    missing = replace(listing, brand="", model="", variation="", ean=None)
    validate_listing(missing)
    assert match_product(product, [missing]).status == "review"


def test_valid_listing_boundaries(listing):
    validate_listing(replace(
        listing, price=Decimal("0"), fba_fee=Decimal("0"), referral_fee_rate=Decimal("1"),
        demand_confidence=0, pricing_stability=1, competition=0,
        observed_at="2026-10-11T10:00:00+01:00",
    ))


def test_mock_catalog_rejects_corrupt_economics(tmp_path, listing):
    from jarvis_scout.models import json_safe
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps([json_safe(replace(listing, price=Decimal("NaN")))]), encoding="utf-8")
    with pytest.raises(ValueError, match="finite, nonnegative"):
        MockAmazonAdapter(path)
