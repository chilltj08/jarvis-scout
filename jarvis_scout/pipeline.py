"""CSV import and atomic scan orchestration; network sources are explicit adapters."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from jarvis_scout.connectors.amazon import validate_listing
from jarvis_scout.connectors.csv_feed import CSVFeedConnector
from jarvis_scout.database import Database
from jarvis_scout.filters import OpportunityFilters, evaluate_opportunity
from jarvis_scout.matching import match_product
from jarvis_scout.models import json_safe
from jarvis_scout.profitability import ProfitAssumptions, calculate_profit

# Compatibility for callers of the first, CSV-only offline demo. These
# estimates are isolated from the exact-match scanner and never trigger alerts.
from jarvis_scout.legacy_demo import Opportunity, discover  # noqa: F401


@dataclass
class ImportReport:
    imported: int
    errors: list[str]


@dataclass
class ScanReport:
    scan_id: int
    total: int
    qualified: int
    review: int
    unmatched: int
    records: list[dict]


def import_feed(file, db: Database, source_override: str | None = None, *, is_demo: bool = False) -> ImportReport:
    result = CSVFeedConnector(file, source_override, is_demo=is_demo).read()
    return ImportReport(db.upsert_products(result.products), result.errors)


def run_scan(db: Database, adapter, assumptions: ProfitAssumptions | None = None,
             filters: OpportunityFilters | None = None, *, is_demo: bool | None = None) -> ScanReport:
    assumptions = assumptions or ProfitAssumptions()
    records = []
    for product in db.products(is_demo=is_demo):
        active_filters = filters or OpportunityFilters(require_eligible=product.is_demo,
                                                        require_market_signals=product.is_demo)
        # An adapter error aborts the scan; partial results are never silently saved as complete.
        candidates = adapter.search(product)
        for listing in candidates:
            validate_listing(listing)
        # A matching barcode cannot turn a fictional resale price into a real quote.
        excluded_mock = bool(not product.is_demo and any(row.simulated or row.provenance == "mock" for row in candidates))
        if not product.is_demo:
            candidates = [row for row in candidates if not row.simulated and row.provenance != "mock"]
        else:
            candidates = [row for row in candidates if row.simulated]
        match = match_product(product, candidates)
        profitability, qualified, reasons, score = None, False, [], 0.0
        incomplete, warnings = [], []
        listing = match.listing
        if not listing:
            incomplete.append("No unambiguous Amazon comparison with an observed resale price")
        else:
            for field, label in (("price", "Amazon selling price"), ("referral_fee_rate", "Amazon referral fee estimate"),
                                 ("fba_fee", "Fulfilment fee estimate")):
                if getattr(listing, field) is None:
                    incomplete.append(f"{label} is missing")
            if listing.provenance == "manual" and not listing.fees_confirmed:
                incomplete.append("Fee estimates have not been confirmed complete for this fulfilment method")
            if listing.provenance == "manual":
                warnings.append("Amazon price and fees are user-recorded observations/estimates, not API-verified market data")
            if listing.restriction_status == "unknown":
                warnings.append("Seller eligibility is unverified; this is a research candidate, not permission to sell")
            if listing.demand_confidence is None or listing.pricing_stability is None or listing.competition is None:
                warnings.append("Demand, price stability or competition evidence is unavailable; no values were invented")
        if product.vat_treatment == "unknown" or (not product.is_demo and product.vat_treatment is None):
            incomplete.append("Supplier VAT treatment is unknown")
        if not product.shipping_cost_known:
            incomplete.append("Supplier shipping cost is unknown")
        if excluded_mock:
            incomplete.append("Mock Amazon data was excluded from this real supplier record")
        if product.stock_status == "unknown" and not product.is_demo:
            warnings.append("Supplier stock status is unknown; verify availability and the minimum order before buying")
        if match.status == "exact" and listing is not None and not incomplete:
            profitability = calculate_profit(product, match.listing, assumptions)
            qualified, reasons, score = evaluate_opportunity(product, listing, profitability, active_filters)
        elif incomplete:
            reasons = list(incomplete)
        else:
            reasons = ["Exact pack and variation match requires verification"]
        if match.status != "exact":
            reasons.append("Exact identity requires manual verification")
        minimum_order_cost = None
        if product.shipping_cost_known and product.vat_treatment not in {"unknown", None}:
            purchase_rate = Decimal("0") if product.vat_treatment == "not_applicable" else Decimal(str(assumptions.purchase_vat_rate))
            delivery_rate = Decimal("0") if product.vat_treatment == "not_applicable" else Decimal(str(assumptions.delivery_vat_rate))
            gross_purchase = product.unit_cost * (1 if product.supplier_vat_included else 1 + purchase_rate)
            shipping = product.shipping_cost if product.shipping_cost is not None else product.delivery_cost * product.minimum_order_quantity
            gross_shipping = shipping * (1 if product.supplier_vat_included else 1 + delivery_rate)
            minimum_order_cost = (gross_purchase * product.minimum_order_quantity + gross_shipping).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
        records.append({
            "product_key": product.product_key, "supplier": json_safe(product),
            "listing": json_safe(match.listing),
            "match": {"status": match.status, "confidence": match.confidence, "reasons": match.reasons},
            "profitability": json_safe(profitability), "qualified": qualified,
            "filter_reasons": reasons, "score": score,
            "simulated": product.is_demo or bool(listing and listing.simulated),
            "data_mode": "demo" if product.is_demo else "real",
            "estimate_status": "complete" if profitability is not None else ("incomplete" if incomplete else "review"),
            "incomplete_reasons": incomplete, "warnings": warnings,
            "minimum_order_cost": json_safe(minimum_order_cost),
        })
    filter_snapshot = json_safe(filters) if filters else {
        "real": json_safe(OpportunityFilters(require_eligible=False, require_market_signals=False)),
        "demo": json_safe(OpportunityFilters()),
    }
    settings = {"assumptions": json_safe(assumptions), "filters": filter_snapshot,
                "adapter": type(adapter).__name__, "scope": "all" if is_demo is None else ("demo" if is_demo else "real")}
    scan_id, saved = db.save_scan(records, settings)
    return ScanReport(scan_id, len(saved), sum(row["qualified"] for row in saved),
                      sum(row["match"]["status"] == "review" for row in saved),
                      sum(row["match"]["status"] == "unmatched" for row in saved), saved)
