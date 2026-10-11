"""Conservative product identity checks; pack conversion is never inferred."""
from __future__ import annotations

import re
from dataclasses import replace

from jarvis_scout.models import AmazonListing, MatchResult, SupplierProduct


def normalize_text(value: str | None) -> str:
    """Normalize words for candidate retrieval, never for proof of identity."""
    return " ".join(re.sub(r"[\W_]+", " ", value or "").casefold().split())


def _normalize_attribute(value: str | None) -> str:
    # Punctuation in models/sizes can be meaningful (e.g. 1/2 versus 1-2).
    return " ".join((value or "").casefold().split())


def normalize_gtin(value: str | None) -> str | None:
    """Validate a GTIN-8/12/13/14 check digit and return its 14-digit form.

    CSV identifiers must be read as text to preserve leading zeroes. Spaces and
    hyphens used to format a barcode are allowed; other nondigits are rejected.
    A check digit validates structure, not whether GS1 assigned this identifier.
    """
    if value is None:
        return None
    digits = re.sub(r"[\s-]", "", str(value))
    if len(digits) not in (8, 12, 13, 14) or not digits.isascii() or not digits.isdigit():
        return None
    if not any(char != "0" for char in digits):
        return None
    weighted_sum = sum(
        int(char) * (3 if position % 2 == 0 else 1)
        for position, char in enumerate(reversed(digits[:-1]))
    )
    if (-weighted_sum) % 10 != int(digits[-1]):
        return None
    return digits.zfill(14)


def _identity_reasons(product: SupplierProduct, listing: AmazonListing) -> list[str]:
    reasons: list[str] = []
    for attribute in ("brand", "model", "variation"):
        supplier_value = _normalize_attribute(getattr(product, attribute))
        amazon_value = _normalize_attribute(getattr(listing, attribute))
        label = attribute.capitalize()
        if not supplier_value or not amazon_value:
            reasons.append(f"{label} is missing on one or both products; verify manually.")
        elif supplier_value != amazon_value:
            reasons.append(
                f"{label} differs: supplier '{getattr(product, attribute)}', "
                f"Amazon '{getattr(listing, attribute)}'."
            )
    if product.pack_quantity < 1 or listing.pack_quantity < 1:
        reasons.append("Pack quantity is invalid; verify the sellable unit manually.")
    elif product.pack_quantity != listing.pack_quantity:
        reasons.append(
            f"Pack quantity differs: supplier {product.pack_quantity}, "
            f"Amazon {listing.pack_quantity}; no pack conversion was assumed."
        )
    return reasons


def _fallback_identity(product: SupplierProduct, listing: AmazonListing) -> bool:
    brand = normalize_text(product.brand)
    model = normalize_text(product.model)
    title = normalize_text(product.title)
    brand_and_model = (
        bool(brand and model)
        and brand == normalize_text(listing.brand)
        and model == normalize_text(listing.model)
    )
    return brand_and_model or bool(title and title == normalize_text(listing.title))


def match_product(product: SupplierProduct, candidates: list[AmazonListing]) -> MatchResult:
    """Return an exact match only when GTIN and all sellable attributes agree.

    Missing/invalid identifiers and any conflicting or missing identity fields
    need human review. Supplier SKU is source-local and never matches an ASIN.
    Multiple candidate ASINs sharing the matching GTIN also need human review.
    """
    product_gtin = normalize_gtin(product.ean)
    identifier_matches: list[tuple[AmazonListing, list[str]]] = []
    fallbacks: list[tuple[AmazonListing, list[str]]] = []
    # Repeated copies of a catalog record should not manufacture ambiguity.
    seen: dict[str, AmazonListing] = {}
    for listing in candidates:
        if listing.asin in seen:
            previous = seen[listing.asin]
            if replace(previous, observed_at="") != replace(listing, observed_at=""):
                duplicate_is_relevant = any(
                    bool(product_gtin and product_gtin == normalize_gtin(item.ean))
                    or _fallback_identity(product, item)
                    for item in (previous, listing)
                )
                if duplicate_is_relevant:
                    return MatchResult(
                        None, "review", 0.25,
                        [f"Conflicting catalog records exist for ASIN {listing.asin}; verify manually."],
                    )
            continue
        seen[listing.asin] = listing
        listing_gtin = normalize_gtin(listing.ean)
        if product_gtin and listing_gtin:
            if product_gtin == listing_gtin:
                identifier_matches.append((listing, _identity_reasons(product, listing)))
            # Distinct valid identifiers must not be overridden by a text match.
            continue
        if _fallback_identity(product, listing):
            reasons = _identity_reasons(product, listing)
            identifier_reason = (
                "GTIN is missing or has an invalid check digit on one or both products; "
                "text similarity cannot verify an exact Amazon product."
            )
            fallbacks.append((listing, [identifier_reason, *reasons]))

    if len(identifier_matches) > 1:
        asins = ", ".join(listing.asin for listing, _ in identifier_matches)
        return MatchResult(
            listing=None,
            status="review",
            confidence=0.5,
            reasons=[f"Multiple Amazon ASINs share the valid GTIN ({asins}); choose manually."],
        )
    if identifier_matches:
        listing, issues = identifier_matches[0]
        if issues:
            return MatchResult(listing, "review", 0.65, ["Valid GTIN matches.", *issues])
        return MatchResult(
            listing,
            "exact",
            1.0,
            ["Valid GTIN, brand, model, variation and pack quantity all match."],
        )
    if len(fallbacks) > 1:
        asins = ", ".join(listing.asin for listing, _ in fallbacks)
        return MatchResult(
            None,
            "review",
            0.25,
            [f"Multiple text candidates ({asins}) lack a verified shared GTIN; choose manually."],
        )
    if fallbacks:
        listing, reasons = fallbacks[0]
        return MatchResult(listing, "review", 0.45, reasons)
    return MatchResult(
        None,
        "unmatched",
        0.0,
        ["No candidate has a matching valid GTIN or sufficient brand/model/title evidence."],
    )
