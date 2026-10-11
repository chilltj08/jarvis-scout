"""Conservative qualification rules and transparent ranking of matched packs."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import math

from jarvis_scout.models import AmazonListing, SupplierProduct


@dataclass(frozen=True)
class OpportunityFilters:
    min_profit: Decimal = Decimal("3")
    min_roi_percent: Decimal = Decimal("30")
    min_demand_confidence: float = 0.5
    min_pricing_stability: float = 0.5
    max_competition: int = 10
    max_data_age_hours: float = 48

    def __post_init__(self):
        for name in ("min_profit", "min_roi_percent"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative Decimal")
        for name in ("min_demand_confidence", "min_pricing_stability"):
            if not math.isfinite(getattr(self, name)) or not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if isinstance(self.max_competition, bool) or not isinstance(self.max_competition, int) or self.max_competition < 0:
            raise ValueError("max_competition must be a nonnegative integer")
        if not math.isfinite(self.max_data_age_hours) or self.max_data_age_hours <= 0:
            raise ValueError("max_data_age_hours must be positive")


def _fresh(timestamp: str, hours: float) -> bool:
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if observed.utcoffset() is None:
            return False
        age = (datetime.now(timezone.utc) - observed).total_seconds() / 3600
        return 0 <= age <= hours
    except (ValueError, TypeError):
        return False


def evaluate_opportunity(product: SupplierProduct, listing: AmazonListing, profit, filters: OpportunityFilters) -> tuple[bool, list[str], float]:
    reasons = []
    # Adapters may provide Decimal probabilities; scores are ranking inputs,
    # while monetary calculations remain Decimal throughout.
    demand_confidence = float(listing.demand_confidence)
    pricing_stability = float(listing.pricing_stability)
    if profit.profit < filters.min_profit:
        reasons.append("Profit below minimum")
    if profit.roi_percent is None or profit.roi_percent < filters.min_roi_percent:
        reasons.append("ROI below minimum or undefined")
    if demand_confidence < filters.min_demand_confidence:
        reasons.append("Demand confidence below minimum")
    if pricing_stability < filters.min_pricing_stability:
        reasons.append("Pricing stability below minimum")
    if listing.competition > filters.max_competition:
        reasons.append("Competition above maximum")
    if listing.restriction_status != "eligible":
        reasons.append("Listing eligibility is restricted or unverified")
    if product.available_quantity == 0:
        reasons.append("Supplier reports out of stock")
    if not _fresh(product.observed_at, filters.max_data_age_hours):
        reasons.append("Supplier observation is stale or invalid")
    if not _fresh(listing.observed_at, filters.max_data_age_hours):
        reasons.append("Amazon observation is stale or invalid")
    # Ranking is a heuristic, never a sales-volume prediction. Eligibility is a hard gate.
    roi = max(0, min(float(profit.roi_percent or 0) / 100, 1))
    money = max(0, min(float(profit.profit) / 15, 1))
    competition = 1 / (1 + listing.competition)
    score = round(100 * (0.25 * roi + 0.25 * money + 0.20 * demand_confidence
                         + 0.20 * pricing_stability + 0.10 * competition), 1)
    if listing.restriction_status != "eligible":
        score = 0.0
    return not reasons, reasons, score
