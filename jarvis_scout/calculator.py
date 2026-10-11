"""Transparent profit estimates for demo product matches."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


def decimal(value: object) -> Decimal:
    """Convert numeric input to Decimal without binary float artefacts."""
    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Estimate:
    purchase_cost: Decimal
    selling_price: Decimal
    referral_fee: Decimal
    fulfilment_cost: Decimal
    inbound_cost: Decimal
    other_costs: Decimal
    estimated_profit: Decimal
    roi_percent: Decimal
    margin_percent: Decimal


def calculate(
    purchase_cost: object,
    selling_price: object,
    referral_rate: object,
    fulfilment_cost: object,
    inbound_cost: object = "0",
    other_costs: object = "0",
) -> Estimate:
    """Estimate unit economics excluding any seller-specific VAT adjustments.

    referral_rate is a fraction: 0.15 means 15%.
    ROI denominator is purchase plus inbound shipping.
    """
    buy, sale, rate, fulfil, inbound, other = map(
        decimal,
        (purchase_cost, selling_price, referral_rate, fulfilment_cost, inbound_cost, other_costs),
    )
    if any(value < 0 for value in (buy, sale, fulfil, inbound, other)):
        raise ValueError("Prices and costs cannot be negative")
    if sale <= 0 or buy + inbound <= 0:
        raise ValueError("Selling price and invested cost must be positive")
    if not (Decimal("0") <= rate <= Decimal("1")):
        raise ValueError("referral_rate must be between 0 and 1")

    referral_fee = money(sale * rate)
    profit = money(sale - buy - referral_fee - fulfil - inbound - other)
    invested = buy + inbound
    return Estimate(
        purchase_cost=money(buy),
        selling_price=money(sale),
        referral_fee=referral_fee,
        fulfilment_cost=money(fulfil),
        inbound_cost=money(inbound),
        other_costs=money(other),
        estimated_profit=profit,
        roi_percent=money(profit / invested * 100),
        margin_percent=money(profit / sale * 100),
    )
