"""Compatibility for the original synthetic CSV demonstration.

This simplified legacy demo does not establish verified product identity or
seller eligibility. The upgraded dashboard/CLI use pipeline.run_scan instead,
with conservative GTIN/variation/pack checks and explicit VAT assumptions.
"""
from dataclasses import dataclass
from pathlib import Path

from .calculator import Estimate, calculate
from .sourcing import (
    SupplierOffer,
    DemoAmazonListing,
    load_supplier_offers,
    load_demo_amazon_listings,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SUPPLIERS = ROOT / "data" / "example_supplier.csv"
EXAMPLE_AMAZON = ROOT / "data" / "example_amazon.csv"


@dataclass(frozen=True)
class Opportunity:
    supplier_offer: SupplierOffer
    amazon_listing: DemoAmazonListing
    estimate: Estimate


def _match_key(ean: str, brand: str, quantity: int) -> tuple[str, str, int]:
    return (ean.strip(), brand.strip().casefold(), quantity)


def discover(
    supplier_path: Path = EXAMPLE_SUPPLIERS,
    amazon_path: Path = EXAMPLE_AMAZON,
    minimum_profit: float = 0,
    minimum_roi: float = 0,
) -> list[Opportunity]:
    """Return demo matches. A matching catalogue entry does NOT imply sale eligibility."""
    offers = load_supplier_offers(Path(supplier_path))
    listings = load_demo_amazon_listings(Path(amazon_path))
    amazon_by_key = {
        _match_key(row.ean, row.brand, row.pack_quantity): row
        for row in listings
    }
    found = []
    for offer in offers:
        listing = amazon_by_key.get(
            _match_key(offer.ean, offer.brand, offer.pack_quantity)
        )
        if listing is None:
            continue
        estimate = calculate(
            purchase_cost=offer.buy_price,
            selling_price=listing.selling_price,
            referral_rate=listing.referral_rate,
            fulfilment_cost=listing.fulfilment_cost,
            inbound_cost=offer.inbound_cost,
        )
        if (
            estimate.estimated_profit >= str_to_decimal(minimum_profit)
            and estimate.roi_percent >= str_to_decimal(minimum_roi)
        ):
            found.append(Opportunity(offer, listing, estimate))
    return sorted(found, key=lambda item: item.estimate.roi_percent, reverse=True)


def str_to_decimal(value: float):
    from .calculator import decimal
    return decimal(value)


if __name__ == "__main__":
    opportunities = discover()
    print(f"Found {len(opportunities)} SIMULATED matches (not real deals).")
    for item in opportunities:
        print(
            f"{item.supplier_offer.name}: "
            f"£{item.estimate.estimated_profit} estimated profit, "
            f"{item.estimate.roi_percent}% ROI "
            f"[SELLING PERMISSION NOT VERIFIED]"
        )
