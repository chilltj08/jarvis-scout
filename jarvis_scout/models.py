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

    @property
    def product_key(self) -> str:
        return hashlib.sha256(json.dumps([self.source, self.sku]).encode()).hexdigest()


@dataclass(frozen=True)
class AmazonListing:
    asin: str
    title: str
    brand: str
    model: str
    ean: str | None
    variation: str
    pack_quantity: int
    price: Decimal
    referral_fee_rate: Decimal
    fba_fee: Decimal
    demand_confidence: float
    pricing_stability: float
    competition: int
    restriction_status: str
    simulated: bool = True
    observed_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class MatchResult:
    listing: AmazonListing | None
    status: str
    confidence: float
    reasons: list[str]


def supplier_from_dict(data: dict) -> SupplierProduct:
    return SupplierProduct(**{**data, "unit_cost": Decimal(str(data["unit_cost"])),
                              "delivery_cost": Decimal(str(data["delivery_cost"]))})


def listing_from_dict(data: dict) -> AmazonListing:
    return AmazonListing(**{**data, **{key: Decimal(str(data[key])) for key in
                                      ("price", "referral_fee_rate", "fba_fee")}})
