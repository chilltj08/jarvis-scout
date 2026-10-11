"""Amazon boundaries. The bundled adapter provides simulated data only."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import json
import math
from pathlib import Path
import re
from typing import Protocol

from jarvis_scout.matching import normalize_gtin, normalize_text
from jarvis_scout.models import AmazonListing, SupplierProduct, listing_from_dict, utc_now


class AmazonAdapter(Protocol):
    def search(self, product: SupplierProduct) -> list[AmazonListing]:
        """Find potential catalog listings, including current commercial data."""
        ...


def validate_listing(listing: AmazonListing) -> None:
    """Reject malformed economic evidence at an adapter boundary.

    Missing identity attributes/GTINs remain valid input for manual review; the
    matching engine decides whether the products can be identified safely.
    This validation checks structure, never truth of a market observation.
    """
    if not isinstance(listing, AmazonListing):
        raise ValueError("Amazon adapter must return AmazonListing records.")
    if not isinstance(listing.asin, str) or not re.fullmatch(r"[A-Z0-9]{10}", listing.asin):
        raise ValueError("Amazon ASIN must be 10 uppercase ASCII letters/digits.")
    if not isinstance(listing.title, str) or not listing.title.strip():
        raise ValueError("Amazon title must be a nonempty string.")
    for name in ("brand", "model", "variation"):
        if not isinstance(getattr(listing, name), str):
            raise ValueError(f"Amazon {name} must be a string (blank requires manual review).")
    if listing.ean is not None and not isinstance(listing.ean, str):
        raise ValueError("Amazon GTIN must be a string or null; do not lose leading zeroes.")
    if type(listing.simulated) is not bool:
        raise ValueError("Amazon simulated flag must be a boolean.")
    if not isinstance(listing.provenance, str) or listing.provenance not in {"mock", "manual", "official"}:
        raise ValueError("Amazon provenance must be mock, manual or official.")
    if listing.simulated is not (listing.provenance == "mock"):
        raise ValueError("Mock Amazon data must be simulated; manual/official observations must not be simulated.")
    if type(listing.identifier_verified) is not bool or type(listing.fees_confirmed) is not bool:
        raise ValueError("Amazon identifier_verified and fees_confirmed must be booleans.")
    if not isinstance(listing.evidence_source, str):
        raise ValueError("Amazon evidence_source must be a string.")
    manual = listing.provenance == "manual"
    if manual and not listing.evidence_source.strip():
        raise ValueError("Manual Amazon observations require an evidence source.")
    for name in ("price", "referral_fee_rate", "fba_fee"):
        value = getattr(listing, name)
        if manual and value is None:
            continue
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise ValueError(f"Amazon {name} must be a finite, nonnegative Decimal.")
    if listing.referral_fee_rate is not None and listing.referral_fee_rate > 1:
        raise ValueError("Amazon referral_fee_rate must be between 0 and 1.")
    for name in ("demand_confidence", "pricing_stability"):
        value = getattr(listing, name)
        if manual and value is None:
            continue
        valid_type = not isinstance(value, bool) and isinstance(value, (int, float, Decimal))
        if not valid_type:
            raise ValueError(f"Amazon {name} must be a finite score between 0 and 1.")
        finite = value.is_finite() if isinstance(value, Decimal) else (
            math.isfinite(value) if isinstance(value, float) else True
        )
        if not finite or not 0 <= value <= 1:
            raise ValueError(f"Amazon {name} must be a finite score between 0 and 1.")
    if not (manual and listing.competition is None) and (
            type(listing.competition) is not int or listing.competition < 0):
        raise ValueError("Amazon competition must be a nonnegative integer.")
    if type(listing.pack_quantity) is not int or listing.pack_quantity < 1:
        raise ValueError("Amazon pack_quantity must be a positive integer.")
    if not isinstance(listing.restriction_status, str) or listing.restriction_status not in {
        "eligible", "restricted", "unknown"
    }:
        raise ValueError("Amazon restriction_status must be eligible, restricted or unknown.")
    if not isinstance(listing.observed_at, str):
        raise ValueError("Amazon observed_at must be an ISO timestamp with a timezone.")
    try:
        observed = datetime.fromisoformat(listing.observed_at)
    except ValueError as exc:
        raise ValueError("Amazon observed_at must be an ISO timestamp with a timezone.") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("Amazon observed_at must include a timezone.")


class MockAmazonAdapter:
    """Local synthetic catalog; it performs no network requests or market checks."""

    simulated = True

    def __init__(self, catalog_path: str | Path | None = None) -> None:
        self.catalog_path = (
            Path(catalog_path)
            if catalog_path is not None
            else Path(__file__).resolve().parents[2] / "data" / "amazon_demo.json"
        )
        with self.catalog_path.open(encoding="utf-8") as handle:
            records = json.load(handle)
        if not isinstance(records, list):
            raise ValueError("Mock Amazon catalog must be a JSON list of simulated listings.")
        self.catalog = [
            listing_from_dict({**record, "simulated": True, "provenance": "mock",
                               "identifier_verified": False, "fees_confirmed": False,
                               "observed_at": utc_now()})
            for record in records
        ]
        for listing in self.catalog:
            validate_listing(listing)

    def search(self, product: SupplierProduct) -> list[AmazonListing]:
        gtin = normalize_gtin(product.ean)
        brand = normalize_text(product.brand)
        model = normalize_text(product.model)
        title = normalize_text(product.title)
        results: list[AmazonListing] = []
        for listing in self.catalog:
            same_gtin = bool(gtin and gtin == normalize_gtin(listing.ean))
            same_model = bool(
                brand and model
                and brand == normalize_text(listing.brand)
                and model == normalize_text(listing.model)
            )
            same_title = bool(title and title == normalize_text(listing.title))
            if same_gtin or same_model or same_title:
                # This timestamp means a simulated observation was generated now.
                # It is never evidence that a real Amazon offer was checked.
                results.append(replace(listing, observed_at=utc_now(), simulated=True))
        return results


class OfficialAmazonAdapter:
    """Extension boundary for authorised Amazon Selling Partner API access.

    Connect Catalog Items, Product Pricing, Product Fees, and Listings
    Restrictions APIs using their official SDKs/authentication and UK marketplace
    configuration. Eligibility must be seller-account-specific. Demand and price
    stability require an independently permitted historical data source; catalog
    and offer endpoints alone do not establish sales volume. No scraping fallback
    is provided. This class intentionally makes no live-data claims.
    """

    simulated = False

    def search(self, product: SupplierProduct) -> list[AmazonListing]:
        raise NotImplementedError(
            "Live Amazon integration is not configured. Implement authorised Catalog Items, "
            "pricing, fees and seller eligibility lookups before returning verified listings."
        )

    def catalog(self, gtin: str) -> list[dict]:
        raise NotImplementedError("Connect the authorised SP-API Catalog Items API for Amazon UK.")

    def pricing(self, asin: str) -> Decimal:
        raise NotImplementedError("Connect the authorised SP-API Product Pricing API for Amazon UK.")

    def fees(self, asin: str, sale_price: Decimal) -> dict[str, Decimal]:
        raise NotImplementedError("Connect the authorised SP-API Product Fees API for Amazon UK.")

    def eligibility(self, asin: str) -> str:
        raise NotImplementedError(
            "Connect the SP-API Listings Restrictions API for the authenticated seller account."
        )
