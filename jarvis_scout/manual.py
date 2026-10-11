"""User-recorded Amazon observations; no Seller Central account or scraping needed."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from jarvis_scout.connectors.amazon import MockAmazonAdapter, validate_listing
from jarvis_scout.matching import normalize_gtin
from jarvis_scout.models import AmazonListing, SupplierProduct, listing_from_dict


def validate_manual_listing(listing: AmazonListing) -> None:
    validate_listing(listing)
    if listing.simulated or listing.provenance != "manual":
        raise ValueError("Manual comparisons must be non-simulated, user-recorded observations")
    if not listing.evidence_source.strip() or len(listing.evidence_source) > 2000:
        raise ValueError("An evidence source of 1–2000 characters is required")
    if listing.price is not None and listing.price <= 0:
        raise ValueError("Observed selling price must be positive, or blank when unknown")
    if listing.ean and normalize_gtin(listing.ean) is None:
        raise ValueError("Listing EAN/GTIN must have a valid check digit, or be blank for review")
    try:
        observed = datetime.fromisoformat(listing.observed_at.replace("Z", "+00:00"))
        if observed.utcoffset() is None or observed > datetime.now(timezone.utc):
            raise ValueError
    except ValueError:
        raise ValueError("Observation timestamp must include a timezone and not be in the future") from None
    if listing.evidence_source.lower().startswith(("http://", "https://")):
        try:
            url = urlsplit(listing.evidence_source)
            port = url.port
            if not url.hostname or url.username is not None or url.password is not None or port == 0:
                raise ValueError
            if any(character.isspace() for character in listing.evidence_source):
                raise ValueError
        except ValueError:
            raise ValueError("Evidence URL must be an ordinary web link without embedded credentials") from None


def create_manual_listing(
    *, asin: str, title: str = "", brand: str = "", ean: str | None = None,
    pack_quantity: int = 1, price: Decimal | None = None,
    observed_at: str, evidence_source: str,
    model: str = "", variation: str = "", referral_fee_rate: Decimal | None = None,
    fulfilment_fee: Decimal | None = None, identifier_verified: bool = False,
    fees_confirmed: bool = False, restriction_status: str = "unknown",
) -> AmazonListing:
    """Save facts the user observed, preserving unknown prices, fees and demand.

    ``identifier_verified`` records the user's evidence check; it never means
    Amazon/GS1 verified a listing. The rate and fulfilment fee are estimates
    excluding VAT. Unknown demand/competition/price stability stay None.
    """
    asin = asin.strip().upper()
    listing = AmazonListing(
        asin=asin, title=title.strip() or f"Manual observation {asin}",
        brand=brand.strip(), model=model.strip(), ean=(ean or "").strip() or None,
        variation=variation.strip(), pack_quantity=pack_quantity,
        price=price, referral_fee_rate=referral_fee_rate, fba_fee=fulfilment_fee,
        demand_confidence=None, pricing_stability=None, competition=None,
        restriction_status=restriction_status, simulated=False, observed_at=observed_at,
        evidence_source=evidence_source.strip(), identifier_verified=identifier_verified,
        provenance="manual", fees_confirmed=fees_confirmed,
    )
    validate_manual_listing(listing)
    return listing


class ManualAmazonAdapter:
    """Only observations explicitly associated with this real supplier product."""

    simulated = False

    def __init__(self, db):
        self.db = db

    def search(self, product: SupplierProduct) -> list[AmazonListing]:
        if product.is_demo:
            return []
        return [listing_from_dict(row["listing"]) for row in self.db.manual_comparisons(product.product_key)]


class ResearchAmazonAdapter:
    """Keep mock catalog lookups confined to explicitly marked demonstration products."""

    simulated = False

    def __init__(self, db, catalog_path: str | Path | None = None):
        self.manual = ManualAmazonAdapter(db)
        self.catalog_path = catalog_path
        self._mock: MockAmazonAdapter | None = None

    def search(self, product: SupplierProduct) -> list[AmazonListing]:
        if not product.is_demo:
            return self.manual.search(product)
        if self._mock is None:
            self._mock = MockAmazonAdapter(self.catalog_path)
        return self._mock.search(product)
