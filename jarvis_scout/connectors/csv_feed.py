"""Bounded, validated supplier CSV imports with explicit real/demo provenance.

``unit_cost`` buys one sellable supplier pack. ``shipping_cost`` is the total
delivery charge for an order of ``minimum_order_quantity`` packs; the domain
record also stores its allocated per-pack ``delivery_cost``. The legacy
``delivery_cost`` column already denotes a per-pack charge. Unknown shipping
and VAT are retained as unknown so downstream estimates cannot invent costs.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from jarvis_scout.matching import normalize_gtin
from jarvis_scout.models import SupplierProduct, utc_now

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
MAX_FIELD_LENGTH = 2_000
MAX_COLUMNS = 128
REQUIRED_COLUMNS = {"sku", "title", "unit_cost", "pack_quantity", "supplier_url"}
REAL_REQUIRED_COLUMNS = REQUIRED_COLUMNS | {"currency", "observed_at"}
COLUMN_ALIASES = {
    "supplier_name": "source", "product_title": "title", "product_url": "supplier_url",
    "gtin": "ean", "ean/gtin": "ean", "ean_gtin": "ean",
}
PROVENANCE_COLUMNS = {"is_demo", "simulated", "is_simulated", "provenance"}
VAT_TREATMENTS = {"included", "excluded", "not_applicable", "unknown"}
STOCK_STATUSES = {"in_stock", "out_of_stock", "preorder", "unknown"}


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
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if result < (0 if zero_allowed else 1) or result > 10000000:
        raise ValueError(f"{name} is out of range")
    return result


def _vat_treatment(row: dict[str, str], is_demo: bool) -> str:
    treatment = row.get("vat_treatment", "").strip().lower().replace(" ", "_")
    if treatment and treatment not in VAT_TREATMENTS:
        raise ValueError("vat_treatment must be included, excluded, not_applicable or unknown")
    legacy = row.get("supplier_vat_included", "").strip().lower()
    if legacy and legacy not in {"true", "false"}:
        raise ValueError("supplier_vat_included must be true or false")
    legacy_treatment = {"true": "included", "false": "excluded"}.get(legacy)
    if treatment and legacy_treatment and treatment != legacy_treatment:
        raise ValueError("vat_treatment conflicts with supplier_vat_included")
    return treatment or legacy_treatment or ("included" if is_demo else "unknown")


def parse_row(
    row: dict[str, str], source_override: str | None = None, *, is_demo: bool = False,
) -> SupplierProduct:
    """Parse a row with canonical names; demo provenance is caller-controlled."""
    if not isinstance(is_demo, bool):
        raise ValueError("is_demo must be a boolean set by the application")
    if PROVENANCE_COLUMNS.intersection(row):
        raise ValueError("CSV provenance columns are not permitted; use the application's demo workflow")
    if any(not isinstance(value, str) or len(value) > MAX_FIELD_LENGTH for value in row.values()):
        raise ValueError(f"field exceeds {MAX_FIELD_LENGTH} characters or is not text")
    source = (source_override if source_override is not None else row.get("source", "")).strip()
    if not source:
        raise ValueError("source is required (supplier_name/source column or source override)")
    if len(source) > MAX_FIELD_LENGTH:
        raise ValueError(f"source exceeds {MAX_FIELD_LENGTH} characters")
    for field in ("sku", "title", "supplier_url", "unit_cost", "pack_quantity"):
        if not row.get(field, "").strip():
            raise ValueError(f"{field} is required")
    url = row["supplier_url"].strip()
    try:
        parsed_url = urlsplit(url)
        port = parsed_url.port
    except ValueError:
        raise ValueError("supplier_url is not a valid HTTP(S) link") from None
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url) or port == 0:
        raise ValueError("supplier_url must not contain whitespace, control characters or port zero")
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise ValueError("supplier_url must be an HTTP(S) link without credentials")
    currency = row.get("currency", "").strip().upper()
    if not currency and is_demo:
        currency = "GBP"
    if not currency:
        raise ValueError("currency is required")
    if currency != "GBP":
        raise ValueError("only GBP feeds are supported; currency conversion is not implemented")
    treatment = _vat_treatment(row, is_demo)
    observation = row.get("observed_at", "").strip()
    if not observation and is_demo:
        observation = utc_now()
    try:
        timestamp = datetime.fromisoformat(observation.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None or timestamp > datetime.now(timezone.utc):
            raise ValueError
        timestamp = timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("observed_at must be an ISO timestamp with timezone, not in the future") from None
    ean = row.get("ean", "").strip() or None
    if ean is not None and not is_demo:
        if not ean.isascii() or not ean.isdigit() or normalize_gtin(ean) is None:
            raise ValueError("ean must be an 8, 12, 13 or 14-digit ASCII GTIN with a valid check digit; keep it as text")
    minimum = positive_integer(row.get("minimum_order_quantity", "").strip() or "1", "minimum_order_quantity")
    shipping = row.get("shipping_cost", "").strip()
    legacy_delivery = row.get("delivery_cost", "").strip()
    if shipping and legacy_delivery:
        raise ValueError("shipping_cost is total order shipping; delivery_cost is per pack; supply only one")
    if shipping:
        shipping_cost = money(shipping, "shipping_cost")
        delivery_cost = shipping_cost / minimum
    elif legacy_delivery:
        delivery_cost = money(legacy_delivery, "delivery_cost")
        shipping_cost = delivery_cost * minimum
    else:
        shipping_cost, delivery_cost = None, Decimal("0")
    quantity = row.get("available_quantity", "").strip()
    available_quantity = positive_integer(quantity, "available_quantity", True) if quantity else None
    stock = row.get("stock_status", "").strip().lower().replace("-", "_").replace(" ", "_") or "unknown"
    if stock == "pre_order":
        stock = "preorder"
    if stock not in STOCK_STATUSES:
        raise ValueError("stock_status must be in_stock, out_of_stock, preorder or unknown")
    if (stock == "in_stock" and available_quantity == 0) or (stock == "out_of_stock" and available_quantity):
        raise ValueError("stock_status conflicts with available_quantity")
    return SupplierProduct(
        source=source, sku=row["sku"].strip(), title=row["title"].strip(),
        brand=row.get("brand", "").strip(), model=row.get("model", "").strip(),
        ean=ean, variation=row.get("variation", "").strip(),
        pack_quantity=positive_integer(row["pack_quantity"].strip(), "pack_quantity"),
        unit_cost=money(row["unit_cost"], "unit_cost"), delivery_cost=delivery_cost,
        supplier_url=url, currency=currency, supplier_vat_included=treatment != "excluded",
        observed_at=timestamp.isoformat(), available_quantity=available_quantity,
        is_demo=is_demo, minimum_order_quantity=minimum, shipping_cost=shipping_cost,
        shipping_cost_known=shipping_cost is not None, stock_status=stock, vat_treatment=treatment,
    )


class CSVFeedConnector:
    """Read UTF-8 CSVs without network requests or inferred demo provenance.

    Bad data rows may be rejected individually. Header, framing, encoding and
    size errors reject the whole feed, including rows read before that error.
    Reimported source/SKU records are upserted by the database layer.
    """

    def __init__(self, file: str | Path | TextIO, source_override: str | None = None, *, is_demo: bool = False):
        if not isinstance(is_demo, bool):
            raise ValueError("is_demo must be a boolean set by the application")
        self.file, self.source_override, self.is_demo = file, source_override, is_demo

    def read(self) -> FeedResult:
        try:
            if isinstance(self.file, (str, Path)):
                if Path(self.file).stat().st_size > MAX_FILE_BYTES:
                    return FeedResult([], ["CSV exceeds the 10 MiB file limit"])
                with Path(self.file).open(encoding="utf-8-sig", newline="") as handle:
                    return self._read(handle)
            return self._read(self.file)
        except UnicodeError:
            return FeedResult([], ["CSV must be UTF-8 encoded; export it as CSV UTF-8"])

    def _read(self, handle: TextIO) -> FeedResult:
        content = handle.read(MAX_FILE_BYTES + 1)
        if isinstance(content, bytes):
            if len(content) > MAX_FILE_BYTES:
                return FeedResult([], ["CSV exceeds the 10 MiB file limit"])
            content = content.decode("utf-8-sig")
        elif len(content.encode("utf-8")) > MAX_FILE_BYTES:
            return FeedResult([], ["CSV exceeds the 10 MiB file limit"])
        products, errors, seen = [], [], set()
        try:
            reader = csv.DictReader(StringIO(content), strict=True)
            original_columns = reader.fieldnames
            if not original_columns or len(original_columns) > MAX_COLUMNS:
                return FeedResult([], ["CSV needs a header with unique column names and at most 128 columns"])
            columns = [COLUMN_ALIASES.get(column.lstrip("\ufeff").strip().lower(), column.lstrip("\ufeff").strip().lower())
                       for column in original_columns]
            if any(not column or len(column) > MAX_FIELD_LENGTH for column in columns) or len(set(columns)) != len(columns):
                return FeedResult([], ["CSV needs unique nonempty column names; aliases may not duplicate a column"])
            if PROVENANCE_COLUMNS.intersection(columns):
                return FeedResult([], ["CSV provenance columns are not permitted; use the application's demo workflow"])
            reader.fieldnames = columns
            required = set(REQUIRED_COLUMNS if self.is_demo else REAL_REQUIRED_COLUMNS)
            if self.source_override is None:
                required.add("source")
            missing = required - set(columns)
            if missing:
                return FeedResult([], ["Missing columns: " + ", ".join(sorted(missing))])
            for row_number, row in enumerate(reader, start=1):
                if row_number > MAX_ROWS:
                    return FeedResult([], [f"CSV exceeds the {MAX_ROWS} row limit"])
                if None in row or any(value is None for value in row.values()):
                    return FeedResult([], [f"Row {reader.line_num}: column count does not match the header; feed rejected"])
                try:
                    product = parse_row(row, self.source_override, is_demo=self.is_demo)
                    if product.product_key in seen:
                        raise ValueError("duplicate source and SKU within this feed")
                    seen.add(product.product_key)
                    products.append(product)
                except ValueError as exc:
                    errors.append(f"Row {reader.line_num}: {exc}")
        except csv.Error:
            return FeedResult([], ["Malformed CSV quoting or oversized field; export a valid UTF-8 CSV"])
        return FeedResult(products, errors)

    def fetch_products(self):
        result = self.read()
        if result.errors:
            raise ValueError("; ".join(result.errors))
        return iter(result.products)
