"""Read intentionally simple CSV-based demo data feeds."""
from dataclasses import dataclass
from decimal import Decimal
import csv
from pathlib import Path

from .calculator import decimal


@dataclass(frozen=True)
class SupplierOffer:
    ean: str
    name: str
    brand: str
    pack_quantity: int
    supplier: str
    buy_price: Decimal
    inbound_cost: Decimal


@dataclass(frozen=True)
class DemoAmazonListing:
    ean: str
    name: str
    brand: str
    pack_quantity: int
    selling_price: Decimal
    referral_rate: Decimal
    fulfilment_cost: Decimal


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_supplier_offers(path: Path) -> list[SupplierOffer]:
    return [
        SupplierOffer(
            ean=row["ean"].strip(),
            name=row["name"].strip(),
            brand=row["brand"].strip(),
            pack_quantity=int(row["pack_quantity"]),
            supplier=row["supplier"].strip(),
            buy_price=decimal(row["buy_price"]),
            inbound_cost=decimal(row["inbound_cost"]),
        )
        for row in _read_csv(path)
    ]


def load_demo_amazon_listings(path: Path) -> list[DemoAmazonListing]:
    return [
        DemoAmazonListing(
            ean=row["ean"].strip(),
            name=row["name"].strip(),
            brand=row["brand"].strip(),
            pack_quantity=int(row["pack_quantity"]),
            selling_price=decimal(row["selling_price"]),
            referral_rate=decimal(row["referral_rate"]),
            fulfilment_cost=decimal(row["fulfilment_cost"]),
        )
        for row in _read_csv(path)
    ]
