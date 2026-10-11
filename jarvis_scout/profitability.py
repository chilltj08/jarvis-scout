"""Penny-precise UK resale estimates, with explicit VAT and ROI assumptions.

The supplier price buys one complete supplier pack. Only an equal Amazon pack
can be costed: splitting or combining packs needs a separate verified workflow.
Amazon selling prices include sales VAT; Amazon referral and FBA fees are
VAT-exclusive. The referral percentage is applied to the full selling price.

For a non-registrant, purchase, delivery, packaging, inbound shipping and other
costs include their input VAT. Supplier treatment controls purchase and delivery
VAT; ``not_applicable`` means no VAT is charged on those supplier inputs.
For a VAT registrant, sales VAT is removed from revenue. Input VAT is recovered
only when ``reclaim_input_vat`` is enabled; that assumption requires valid VAT
invoices and eligibility for each input. The model assumes standard VAT
accounting, not the Flat Rate Scheme, margin schemes or cross-border treatment.

``vat_cost`` adds unrecoverable Amazon-fee VAT only. The other cost fields
already include any unrecoverable VAT, so VAT must not be added to them again.
``output_vat`` and ``recoverable_input_vat`` are informational reconciliations.
Returns are a conservative reserve against net revenue, not a second fee or a
prediction of refunded taxes. ROI is profit divided by *all* modelled costs;
margin is profit divided by net revenue. A zero denominator yields ``None``.

Each cost/tax category is rounded to pennies, then summed, so the displayed
breakdown reconciles exactly. Actual Amazon fees and invoice-level tax rounding
can differ from an estimate. There are no assumptions about buying or listing.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from .models import AmazonListing, SupplierProduct


PENNY = Decimal("0.01")
ZERO = Decimal("0.00")
ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ProfitAssumptions:
    """Rates are fractions (0.20 means 20%); extra costs are VAT-inclusive.

    ``supplier_vat_included`` on the supplier record applies to both the pack
    price and its per-pack allocated delivery charge. Zero rates support
    non-VAT-charging suppliers and zero-rated inputs. Input recovery is an
    explicit eligibility assumption, not an automatic result of registration.
    ``inbound_shipping_cost`` covers transport to fulfilment or outbound postage
    not already included in the listing's fulfilment estimate. ``other_costs``
    covers per-pack storage, preparation or other expenses. Each has an explicit
    VAT rate; split mixed-VAT costs before entering them or use a defensible
    effective rate. Do not enter the same cost in more than one category.
    """

    packaging_cost: Decimal = Decimal("0.50")
    returns_rate: Decimal = Decimal("0.03")
    vat_registered: bool = False
    vat_rate: Decimal = Decimal("0.20")
    amazon_fees_vat_rate: Decimal = Decimal("0.20")
    purchase_vat_rate: Decimal = Decimal("0.20")
    delivery_vat_rate: Decimal = Decimal("0.20")
    packaging_vat_rate: Decimal = Decimal("0.20")
    reclaim_input_vat: bool = True
    inbound_shipping_cost: Decimal = ZERO
    other_costs: Decimal = ZERO
    inbound_shipping_vat_rate: Decimal = Decimal("0.20")
    other_costs_vat_rate: Decimal = Decimal("0.20")

    def __post_init__(self) -> None:
        # A scan may contain only unmatched products. Validate settings before
        # any matching or persistence, rather than waiting for a calculation.
        for name in ("packaging_cost", "inbound_shipping_cost", "other_costs"):
            _decimal(getattr(self, name), name)
        for name in (
            "returns_rate", "vat_rate", "amazon_fees_vat_rate", "purchase_vat_rate",
            "delivery_vat_rate", "packaging_vat_rate", "inbound_shipping_vat_rate",
            "other_costs_vat_rate",
        ):
            _rate(getattr(self, name), name)
        for name in ("vat_registered", "reclaim_input_vat"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class ProfitResult:
    sale_price: Decimal
    net_revenue: Decimal
    purchase_cost: Decimal
    supplier_delivery: Decimal
    referral_fee: Decimal
    fba_fee: Decimal
    packaging: Decimal
    returns_allowance: Decimal
    vat_cost: Decimal
    total_cost: Decimal
    profit: Decimal
    roi_percent: Decimal | None
    margin_percent: Decimal | None
    output_vat: Decimal
    recoverable_input_vat: Decimal
    inbound_shipping: Decimal = ZERO
    other_costs: Decimal = ZERO


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise ValueError(f"{name} must be a finite nonnegative number")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a finite nonnegative number") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return amount


def _rate(value: object, name: str) -> Decimal:
    rate = _decimal(value, name)
    if rate > ONE:
        raise ValueError(f"{name} must be a fraction between 0 and 1")
    return rate


def _round(value: Decimal) -> Decimal:
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


def _vat_split(amount: Decimal, rate: Decimal, included: bool) -> tuple[Decimal, Decimal, Decimal]:
    """Return rounded net, tax and gross amounts which reconcile to pennies."""
    amount = _round(amount)
    if included:
        tax = _round(amount * rate / (ONE + rate))
        return amount - tax, tax, amount
    tax = _round(amount * rate)
    return amount, tax, amount + tax


def calculate_profit(
    product: SupplierProduct,
    listing: AmazonListing,
    assumptions: ProfitAssumptions | None = None,
) -> ProfitResult:
    """Calculate one complete matched-pack sale; reject unsupported inputs.

    Supplier delivery must be allocated to this pack by the import feed, not an
    entire unrelated bulk order. Demand, restrictions and match confidence are
    evaluated separately and do not turn this estimate into a verified deal.
    Missing price/fee inputs, unknown delivery costs or unknown VAT treatment
    cannot produce a complete estimate and raise ``ValueError``.
    """
    assumptions = assumptions if assumptions is not None else ProfitAssumptions()
    if not isinstance(assumptions, ProfitAssumptions):
        raise ValueError("assumptions must be a ProfitAssumptions record")
    if product.currency != "GBP":
        raise ValueError("Only GBP supplier prices are supported")
    for name, pack in (("supplier", product.pack_quantity), ("Amazon", listing.pack_quantity)):
        if isinstance(pack, bool) or not isinstance(pack, int) or pack <= 0:
            raise ValueError(f"{name} pack_quantity must be a positive integer")
    if product.pack_quantity != listing.pack_quantity:
        raise ValueError("Supplier and Amazon pack quantities must match exactly")
    for name, value in (
        ("supplier_vat_included", product.supplier_vat_included),
        ("shipping_cost_known", product.shipping_cost_known),
        ("vat_registered", assumptions.vat_registered),
        ("reclaim_input_vat", assumptions.reclaim_input_vat),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
    if not product.shipping_cost_known:
        raise ValueError("Supplier shipping cost is unknown; estimate is incomplete")
    treatment = product.vat_treatment
    if treatment == "unknown":
        raise ValueError("Supplier VAT treatment is unknown; estimate is incomplete")
    if treatment not in (None, "included", "excluded", "not_applicable"):
        raise ValueError("Unsupported supplier vat_treatment")
    if treatment in ("included", "excluded") and product.supplier_vat_included != (treatment == "included"):
        raise ValueError("Supplier VAT treatment conflicts with supplier_vat_included")

    purchase = _decimal(product.unit_cost, "unit_cost")
    delivery = _decimal(product.delivery_cost, "delivery_cost")
    price = _decimal(listing.price, "price")
    fba = _decimal(listing.fba_fee, "fba_fee")
    packaging = _decimal(assumptions.packaging_cost, "packaging_cost")
    inbound_shipping = _decimal(assumptions.inbound_shipping_cost, "inbound_shipping_cost")
    other_costs = _decimal(assumptions.other_costs, "other_costs")
    referral_rate = _rate(listing.referral_fee_rate, "referral_fee_rate")
    rates = {
        name: _rate(getattr(assumptions, name), name)
        for name in (
            "returns_rate", "vat_rate", "amazon_fees_vat_rate",
            "purchase_vat_rate", "delivery_vat_rate", "packaging_vat_rate",
            "inbound_shipping_vat_rate", "other_costs_vat_rate",
        )
    }
    if treatment == "not_applicable":
        rates["purchase_vat_rate"] = rates["delivery_vat_rate"] = ZERO
    reclaim = assumptions.vat_registered and assumptions.reclaim_input_vat

    # Extra precision avoids binary floats and intermediate penny rounding.
    # Enormous malformed money still fails with a useful input error.
    try:
        with localcontext() as context:
            context.prec = 40
            sale_price = _round(price)
            _, possible_output_vat, _ = _vat_split(sale_price, rates["vat_rate"], True)
            output_vat = possible_output_vat if assumptions.vat_registered else ZERO
            net_revenue = sale_price - output_vat

            purchase_net, purchase_tax, purchase_gross = _vat_split(
                purchase, rates["purchase_vat_rate"], product.supplier_vat_included
            )
            delivery_net, delivery_tax, delivery_gross = _vat_split(
                delivery, rates["delivery_vat_rate"], product.supplier_vat_included
            )
            packaging_net, packaging_tax, packaging_gross = _vat_split(
                packaging, rates["packaging_vat_rate"], True
            )
            inbound_net, inbound_tax, inbound_gross = _vat_split(
                inbound_shipping, rates["inbound_shipping_vat_rate"], True
            )
            other_net, other_tax, other_gross = _vat_split(
                other_costs, rates["other_costs_vat_rate"], True
            )
            purchase_cost = purchase_net if reclaim else purchase_gross
            supplier_delivery = delivery_net if reclaim else delivery_gross
            packaging_cost = packaging_net if reclaim else packaging_gross
            inbound_shipping_cost = inbound_net if reclaim else inbound_gross
            extra_costs = other_net if reclaim else other_gross
            referral_fee = _round(sale_price * referral_rate)
            fba_fee = _round(fba)
            fee_tax = _round(referral_fee * rates["amazon_fees_vat_rate"]) + _round(
                fba_fee * rates["amazon_fees_vat_rate"]
            )
            vat_cost = ZERO if reclaim else fee_tax
            recoverable_input_vat = (
                purchase_tax + delivery_tax + packaging_tax + inbound_tax + other_tax + fee_tax
                if reclaim else ZERO
            )
            returns_allowance = _round(net_revenue * rates["returns_rate"])
            total_cost = sum(
                (purchase_cost, supplier_delivery, referral_fee, fba_fee,
                 packaging_cost, inbound_shipping_cost, extra_costs,
                 returns_allowance, vat_cost), ZERO
            )
            profit = net_revenue - total_cost
            roi_percent = _round(profit / total_cost * HUNDRED) if total_cost else None
            margin_percent = _round(profit / net_revenue * HUNDRED) if net_revenue else None
    except InvalidOperation:
        raise ValueError("Monetary amounts exceed supported Decimal precision") from None

    return ProfitResult(
        sale_price=sale_price,
        net_revenue=net_revenue,
        purchase_cost=purchase_cost,
        supplier_delivery=supplier_delivery,
        referral_fee=referral_fee,
        fba_fee=fba_fee,
        packaging=packaging_cost,
        returns_allowance=returns_allowance,
        vat_cost=vat_cost,
        total_cost=total_cost,
        profit=profit,
        roi_percent=roi_percent,
        margin_percent=margin_percent,
        output_vat=output_vat,
        recoverable_input_vat=recoverable_input_vat,
        inbound_shipping=inbound_shipping_cost,
        other_costs=extra_costs,
    )
