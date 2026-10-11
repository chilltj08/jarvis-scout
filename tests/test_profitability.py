"""Hand-calculated financial examples and malformed-feed safety checks."""
from dataclasses import replace
from decimal import Decimal

import pytest

from jarvis_scout.models import AmazonListing, SupplierProduct
from jarvis_scout.profitability import ProfitAssumptions, calculate_profit


def supplier(**changes):
    return replace(SupplierProduct(
        source="test", sku="sample", title="Example", brand="Brand", model="M1",
        ean="5012345678900", variation="blue", pack_quantity=1,
        unit_cost=Decimal("10.00"), delivery_cost=Decimal("1.00"),
        supplier_url="https://supplier.example/item",
    ), **changes)


def listing(**changes):
    return replace(AmazonListing(
        asin="B000000001", title="Example", brand="Brand", model="M1",
        ean="5012345678900", variation="blue", pack_quantity=1,
        price=Decimal("30.00"), referral_fee_rate=Decimal("0.15"),
        fba_fee=Decimal("3.00"), demand_confidence=0.8,
        pricing_stability=0.8, competition=3, restriction_status="unknown",
    ), **changes)


def test_non_registered_complete_costs_and_conservative_roi():
    result = calculate_profit(supplier(), listing())
    # £30 revenue - £10 pack - £1 delivery - £4.50 referral - £3 FBA
    # - £0.50 packaging - £0.90 returns - £1.50 fee VAT = £8.60.
    assert result.sale_price == result.net_revenue == Decimal("30.00")
    assert result.purchase_cost == Decimal("10.00")
    assert result.supplier_delivery == Decimal("1.00")
    assert result.referral_fee == Decimal("4.50")
    assert result.fba_fee == Decimal("3.00")
    assert result.packaging == Decimal("0.50")
    assert result.returns_allowance == Decimal("0.90")
    assert result.vat_cost == Decimal("1.50")
    assert result.total_cost == Decimal("21.40")
    assert result.profit == Decimal("8.60")
    assert result.roi_percent == Decimal("40.19")
    assert result.margin_percent == Decimal("28.67")
    assert result.output_vat == result.recoverable_input_vat == Decimal("0.00")


def test_registered_output_vat_and_eligible_input_recovery():
    result = calculate_profit(supplier(), listing(), ProfitAssumptions(vat_registered=True))
    # Sales VAT = £5; recoverable purchase/delivery/prep VAT = £1.92;
    # fees £7.50 incur £1.50 recoverable VAT. Costs use net amounts.
    assert result.net_revenue == Decimal("25.00")
    assert result.output_vat == Decimal("5.00")
    assert result.purchase_cost == Decimal("8.33")
    assert result.supplier_delivery == Decimal("0.83")
    assert result.packaging == Decimal("0.42")
    assert result.returns_allowance == Decimal("0.75")
    assert result.vat_cost == Decimal("0.00")
    assert result.recoverable_input_vat == Decimal("3.42")
    assert result.total_cost == Decimal("17.83")
    assert result.profit == Decimal("7.17")
    assert result.roi_percent == Decimal("40.21")
    assert result.margin_percent == Decimal("28.68")


def test_registered_without_invoice_recovery_keeps_input_vat_costs():
    assumptions = ProfitAssumptions(vat_registered=True, reclaim_input_vat=False)
    result = calculate_profit(supplier(), listing(), assumptions)
    assert result.net_revenue == Decimal("25.00")
    assert result.purchase_cost == Decimal("10.00")
    assert result.vat_cost == Decimal("1.50")
    assert result.recoverable_input_vat == Decimal("0.00")
    assert result.total_cost == Decimal("21.25")
    assert result.profit == Decimal("3.75")


def test_vat_exclusive_supplier_quotes_add_vat_for_non_registrant():
    result = calculate_profit(supplier(supplier_vat_included=False), listing())
    assert result.purchase_cost == Decimal("12.00")
    assert result.supplier_delivery == Decimal("1.20")
    assert result.total_cost == Decimal("23.60")
    assert result.profit == Decimal("6.40")


def test_equivalent_inclusive_and_exclusive_supplier_quotes_have_same_registered_cost():
    assumptions = ProfitAssumptions(vat_registered=True)
    inclusive = calculate_profit(
        supplier(unit_cost=Decimal("12"), delivery_cost=Decimal("1.2")), listing(), assumptions
    )
    exclusive = calculate_profit(supplier(supplier_vat_included=False), listing(), assumptions)
    assert inclusive == exclusive


def test_reduced_or_zero_input_vat_is_configurable():
    assumptions = ProfitAssumptions(
        vat_registered=True, purchase_vat_rate=Decimal("0"),
        delivery_vat_rate=Decimal("0"), packaging_vat_rate=Decimal("0"),
        amazon_fees_vat_rate=Decimal("0"),
    )
    result = calculate_profit(supplier(), listing(), assumptions)
    assert result.purchase_cost == Decimal("10.00")
    assert result.supplier_delivery == Decimal("1.00")
    assert result.packaging == Decimal("0.50")
    assert result.recoverable_input_vat == Decimal("0.00")
    assert result.profit == Decimal("5.25")


def test_referral_percentage_uses_full_vat_inclusive_sale_price():
    result = calculate_profit(supplier(), listing(), ProfitAssumptions(vat_registered=True))
    assert result.referral_fee == Decimal("4.50")  # 15% of £30, not £25.


def test_cost_breakdown_reconciles_and_tax_half_penny_rounding_does_not_invent_money():
    assumptions = ProfitAssumptions(
        vat_registered=True, packaging_cost=Decimal("0.03"), returns_rate=Decimal("0")
    )
    result = calculate_profit(
        supplier(unit_cost=Decimal("0.03"), delivery_cost=Decimal("0")),
        listing(price=Decimal("0.03"), fba_fee=Decimal("0"), referral_fee_rate=Decimal("0")),
        assumptions,
    )
    assert result.output_vat == Decimal("0.01")
    assert result.net_revenue == Decimal("0.02")
    assert result.purchase_cost == result.packaging == Decimal("0.02")
    assert result.recoverable_input_vat == Decimal("0.02")
    costs = (result.purchase_cost, result.supplier_delivery, result.referral_fee,
             result.fba_fee, result.packaging, result.returns_allowance, result.vat_cost)
    assert sum(costs) == result.total_cost
    assert result.profit == result.net_revenue - result.total_cost


def test_zero_total_cost_produces_undefined_roi_instead_of_false_zero():
    assumptions = ProfitAssumptions(packaging_cost=Decimal("0"), returns_rate=Decimal("0"))
    result = calculate_profit(
        supplier(unit_cost=Decimal("0"), delivery_cost=Decimal("0")),
        listing(referral_fee_rate=Decimal("0"), fba_fee=Decimal("0")), assumptions,
    )
    assert result.total_cost == Decimal("0.00")
    assert result.profit == Decimal("30.00")
    assert result.roi_percent is None
    assert result.margin_percent == Decimal("100.00")


def test_zero_sales_and_loss_are_valid_but_margin_is_undefined():
    result = calculate_profit(supplier(), listing(price=Decimal("0")))
    assert result.net_revenue == Decimal("0.00")
    assert result.profit == -result.total_cost
    assert result.roi_percent == Decimal("-100.00")
    assert result.margin_percent is None


def test_full_returns_reserve_preserves_loss_instead_of_clamping_profit():
    result = calculate_profit(supplier(), listing(), ProfitAssumptions(returns_rate=Decimal("1")))
    assert result.returns_allowance == Decimal("30.00")
    assert result.profit < 0


@pytest.mark.parametrize("field", ["unit_cost", "delivery_cost"])
@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity"), None, True, "bad"])
def test_invalid_supplier_money_is_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        calculate_profit(supplier(**{field: value}), listing())


@pytest.mark.parametrize("field", ["price", "fba_fee"])
@pytest.mark.parametrize("value", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_amazon_money_is_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        calculate_profit(supplier(), listing(**{field: value}))


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("1.01"), Decimal("NaN")])
def test_invalid_referral_rates_are_rejected(value):
    with pytest.raises(ValueError, match="referral_fee_rate"):
        calculate_profit(supplier(), listing(referral_fee_rate=value))


@pytest.mark.parametrize("field", [
    "returns_rate", "vat_rate", "amazon_fees_vat_rate", "purchase_vat_rate",
    "delivery_vat_rate", "packaging_vat_rate",
])
@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("1.01"), Decimal("Infinity")])
def test_invalid_assumption_rates_are_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        calculate_profit(supplier(), listing(), ProfitAssumptions(**{field: value}))


@pytest.mark.parametrize("value", [Decimal("-1"), Decimal("NaN"), "broken"])
def test_invalid_packaging_is_rejected(value):
    with pytest.raises(ValueError, match="packaging_cost"):
        calculate_profit(supplier(), listing(), ProfitAssumptions(packaging_cost=value))


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_pack_quantities_are_rejected(value):
    with pytest.raises(ValueError, match="pack_quantity"):
        calculate_profit(supplier(pack_quantity=value), listing())


def test_complete_matching_packs_are_costed_once_and_mismatches_are_rejected():
    single = calculate_profit(supplier(), listing())
    multipack = calculate_profit(supplier(pack_quantity=6), listing(pack_quantity=6))
    assert single == multipack  # Pack purchase cost is not multiplied by six.
    with pytest.raises(ValueError, match="match exactly"):
        calculate_profit(supplier(pack_quantity=6), listing(pack_quantity=1))


def test_currency_conversion_requires_an_explicit_external_integration():
    with pytest.raises(ValueError, match="GBP"):
        calculate_profit(supplier(currency="USD"), listing())


@pytest.mark.parametrize("field", ["vat_registered", "reclaim_input_vat"])
def test_boolean_assumptions_do_not_accept_truthy_strings(field):
    with pytest.raises(ValueError, match=field):
        calculate_profit(supplier(), listing(), ProfitAssumptions(**{field: "false"}))


def test_supplier_vat_inclusion_must_be_an_actual_boolean():
    with pytest.raises(ValueError, match="supplier_vat_included"):
        calculate_profit(supplier(supplier_vat_included="false"), listing())
