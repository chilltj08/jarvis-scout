"""Validate local CSV feeds, preserving identifiers as strings and reporting bad rows."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from jarvis_scout.models import SupplierProduct, utc_now

REQUIRED_COLUMNS = {"sku", "title", "unit_cost", "pack_quantity", "delivery_cost", "supplier_url"}


@dataclass
class FeedResult:
    products: list[SupplierProduct]
    errors: list[str]


def money(value: str, name: str) -> Decimal:
    try:
        result = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{name} must be a decimal number") from None
    if not result.is_finite() or result < 0 or result > Decimal("10000000"):
        raise ValueError(f"{name} must be finite, nonnegative and at most 10000000")
    return result


def positive_integer(value: str, name: str, zero_allowed: bool = False) -> int:
    if not value.isdigit():
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if result < (0 if zero_allowed else 1) or result > 10000000:
        raise ValueError(f"{name} is out of range")
    return result


def parse_row(row: dict[str, str], source_override: str | None = None) -> SupplierProduct:
    source = (source_override or row.get("source", "")).strip()
    if not source:
        raise ValueError("source is required (column or source override)")
    for field in ("sku", "title", "supplier_url"):
        if not row.get(field, "").strip():
            raise ValueError(f"{field} is required")
    if any(len(value) > 2000 for value in row.values()):
        raise ValueError("field exceeds 2000 characters")
    url = row["supplier_url"].strip()
    try:
        parsed_url = urlsplit(url)
        port = parsed_url.port
    except ValueError:
        raise ValueError("supplier_url is not a valid HTTP(S) link") from None
    if any(character.isspace() for character in url) or port == 0:
        raise ValueError("supplier_url must not contain whitespace or port zero")
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise ValueError("supplier_url must be an HTTP(S) link without credentials")
    currency = row.get("currency", "GBP").strip().upper() or "GBP"
    if currency != "GBP":
        raise ValueError("only GBP feeds are supported; currency conversion is not implemented")
    vat_text = row.get("supplier_vat_included", "true").strip().lower() or "true"
    if vat_text not in {"true", "false"}:
        raise ValueError("supplier_vat_included must be true or false")
    observation = row.get("observed_at", "").strip() or utc_now()
    try:
        timestamp = datetime.fromisoformat(observation.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None or timestamp > datetime.now(timezone.utc):
            raise ValueError
    except ValueError:
        raise ValueError("observed_at must be an ISO timestamp with timezone, not in the future") from None
    quantity = row.get("available_quantity", "").strip()
    return SupplierProduct(
        source=source, sku=row["sku"].strip(), title=row["title"].strip(),
        brand=row.get("brand", "").strip(), model=row.get("model", "").strip(),
        ean=row.get("ean", "").strip() or None, variation=row.get("variation", "").strip(),
        pack_quantity=positive_integer(row["pack_quantity"].strip(), "pack_quantity"),
        unit_cost=money(row["unit_cost"], "unit_cost"),
        delivery_cost=money(row["delivery_cost"], "delivery_cost"), supplier_url=url,
        currency=currency, supplier_vat_included=vat_text == "true", observed_at=timestamp.isoformat(),
        available_quantity=positive_integer(quantity, "available_quantity", True) if quantity else None,
    )


class CSVFeedConnector:
    def __init__(self, file: str | Path | TextIO, source_override: str | None = None):
        self.file, self.source_override = file, source_override

    def read(self) -> FeedResult:
        if isinstance(self.file, (str, Path)):
            with Path(self.file).open(encoding="utf-8-sig", newline="") as handle:
                return self._read(handle)
        return self._read(self.file)

    def _read(self, handle: TextIO) -> FeedResult:
        products, errors, seen = [], [], set()
        try:
            reader = csv.DictReader(handle, strict=True)
            columns = reader.fieldnames
            if columns:
                columns[0] = columns[0].lstrip("\ufeff")
            if not columns or len(set(columns)) != len(columns):
                return FeedResult([], ["CSV needs a header with unique column names"])
            missing = REQUIRED_COLUMNS - set(columns)
            if missing:
                return FeedResult([], ["Missing columns: " + ", ".join(sorted(missing))])
            for row in reader:
                try:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("column count does not match the header")
                    product = parse_row(row, self.source_override)
                    if product.product_key in seen:
                        raise ValueError("duplicate source and SKU within this feed")
                    seen.add(product.product_key)
                    products.append(product)
                except ValueError as exc:
                    errors.append(f"Row {reader.line_num}: {exc}")
        except csv.Error as exc:
            errors.append(f"Malformed CSV: {exc}")
        return FeedResult(products, errors)

    def fetch_products(self):
        result = self.read()
        if result.errors:
            raise ValueError("; ".join(result.errors))
        return iter(result.products)
