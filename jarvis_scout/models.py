"""Portable domain records. Money uses Decimal; a supplier price buys one pack."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


@dataclass(frozen=True)
class SupplierProduct:
    source: str
    sku: str
    title: str
    brand: str
    model: str
    ean: str | None
    variation: str
    pack_quantity: int
    unit_cost: Decimal
    delivery_cost: Decimal
    supplier_url: str
    currency: str = "GBP"
    supplier_vat_included: bool = True
    observed_at: str = field(default_factory=utc_now)
    available_quantity: int | None = None
    is_demo: bool = False
    minimum_order_quantity: int = 1
    shipping_cost: Decimal | None = None
    shipping_cost_known: bool = True
    stock_status: str = "unknown"
    vat_treatment: str | None = None

    @property
    def product_key(self) -> str:
        identity = [self.source, self.sku] if not self.is_demo else ["demo", self.source, self.sku]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


@dataclass(frozen=True)
class AmazonListing:
    asin: str
    title: str
    brand: str
    model: str
    ean: str | None
    variation: str
    pack_quantity: int
    price: Decimal | None
    referral_fee_rate: Decimal | None
    fba_fee: Decimal | None
    demand_confidence: float | None
    pricing_stability: float | None
    competition: int | None
    restriction_status: str
    simulated: bool = True
    observed_at: str = field(default_factory=utc_now)
    evidence_source: str = ""
    identifier_verified: bool = False
    provenance: str = "mock"
    fees_confirmed: bool = False


@dataclass(frozen=True)
class MatchResult:
    listing: AmazonListing | None
    status: str
    confidence: float
    reasons: list[str]


def supplier_from_dict(data: dict) -> SupplierProduct:
    result = {**data, "unit_cost": Decimal(str(data["unit_cost"])),
              "delivery_cost": Decimal(str(data["delivery_cost"]))}
    if data.get("shipping_cost") is not None:
        result["shipping_cost"] = Decimal(str(data["shipping_cost"]))
    return SupplierProduct(**result)


def listing_from_dict(data: dict) -> AmazonListing:
    return AmazonListing(**{**data, **{key: Decimal(str(data[key])) if data[key] is not None else None for key in
                                      ("price", "referral_fee_rate", "fba_fee")}})
