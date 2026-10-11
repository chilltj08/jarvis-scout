"""CSV import and atomic scan orchestration; network sources are explicit adapters."""
from dataclasses import dataclass

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


def import_feed(file, db: Database, source_override: str | None = None) -> ImportReport:
    result = CSVFeedConnector(file, source_override).read()
    return ImportReport(db.upsert_products(result.products), result.errors)


def run_scan(db: Database, adapter, assumptions: ProfitAssumptions | None = None,
             filters: OpportunityFilters | None = None) -> ScanReport:
    assumptions = assumptions or ProfitAssumptions()
    filters = filters or OpportunityFilters()
    records = []
    for product in db.products():
        # An adapter error aborts the scan; partial results are never silently saved as complete.
        candidates = adapter.search(product)
        for listing in candidates:
            validate_listing(listing)
        match = match_product(product, candidates)
        profitability, qualified, reasons, score = None, False, [], 0.0
        if match.status == "exact" and match.listing is not None:
            profitability = calculate_profit(product, match.listing, assumptions)
            qualified, reasons, score = evaluate_opportunity(product, match.listing, profitability, filters)
        else:
            reasons = ["Exact pack and variation match requires verification"]
        records.append({
            "product_key": product.product_key, "supplier": json_safe(product),
            "listing": json_safe(match.listing),
            "match": {"status": match.status, "confidence": match.confidence, "reasons": match.reasons},
            "profitability": json_safe(profitability), "qualified": qualified,
            "filter_reasons": reasons, "score": score,
            "simulated": bool(match.listing.simulated) if match.listing else bool(getattr(adapter, "simulated", False)),
        })
    settings = {"assumptions": json_safe(assumptions), "filters": json_safe(filters),
                "adapter": type(adapter).__name__}
    scan_id, saved = db.save_scan(records, settings)
    return ScanReport(scan_id, len(saved), sum(row["qualified"] for row in saved),
                      sum(row["match"]["status"] == "review" for row in saved),
                      sum(row["match"]["status"] == "unmatched" for row in saved), saved)
