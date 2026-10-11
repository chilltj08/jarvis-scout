"""Real supplier feeds retain provenance, cost meaning and missing evidence."""
import csv
from datetime import datetime
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path

import pytest

from jarvis_scout.connectors import csv_feed
from jarvis_scout.connectors.csv_feed import CSVFeedConnector


def supplier_row(**changes):
    row = {
        "supplier_name": "Test Wholesaler", "product_title": "Verified test pack",
        "brand": "Test Brand", "ean": "036000291452", "sku": "SKU-001",
        "unit_cost": "12.50", "currency": "GBP", "vat_treatment": "excluded",
        "minimum_order_quantity": "4", "pack_quantity": "2", "shipping_cost": "8.00",
        "stock_status": "in_stock", "product_url": "https://example.com/product/test",
        "observed_at": "2025-01-02T12:30:00+00:00", "model": "TB-2", "variation": "Blue",
        "available_quantity": "40",
    }
    row.update(changes)
    return row


def feed(*rows, columns=None):
    text = StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=columns or list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    text.seek(0)
    return text


def test_real_feed_preserves_identifier_and_allocates_total_order_shipping():
    result = CSVFeedConnector(feed(supplier_row())).read()
    assert not result.errors and len(result.products) == 1
    product = result.products[0]
    assert product.ean == "036000291452"
    assert product.source == "Test Wholesaler" and product.sku == "SKU-001"
    assert product.unit_cost == Decimal("12.50") and product.pack_quantity == 2
    assert product.minimum_order_quantity == 4
    assert product.shipping_cost == Decimal("8.00")
    assert product.delivery_cost == Decimal("2.00") and product.shipping_cost_known
    assert product.vat_treatment == "excluded" and not product.supplier_vat_included
    assert not product.is_demo and product.stock_status == "in_stock"
    assert product.observed_at == "2025-01-02T12:30:00+00:00"


def test_unknown_costs_and_identifiers_are_preserved_without_inventing_evidence():
    result = CSVFeedConnector(feed(supplier_row(
        ean="", brand="", shipping_cost="", vat_treatment="", stock_status="",
        minimum_order_quantity="", available_quantity="",
    ))).read()
    assert not result.errors
    product = result.products[0]
    assert product.ean is None and product.brand == ""
    assert product.shipping_cost is None and not product.shipping_cost_known
    assert product.vat_treatment == "unknown" and product.stock_status == "unknown"
    assert product.minimum_order_quantity == 1 and product.available_quantity is None


@pytest.mark.parametrize("ean", ["5012345678900", "4006381333931", "036000291452", "96385074", "00036000291452"])
def test_valid_gtin_lengths_are_preserved_as_original_text(ean):
    result = CSVFeedConnector(feed(supplier_row(ean=ean))).read()
    assert not result.errors and result.products[0].ean == ean


@pytest.mark.parametrize("changes,field", [
    ({"supplier_name": ""}, "source"), ({"product_title": " "}, "title"),
    ({"sku": ""}, "sku"), ({"unit_cost": ""}, "unit_cost"),
    ({"unit_cost": "-0.01"}, "unit_cost"), ({"unit_cost": "NaN"}, "unit_cost"),
    ({"unit_cost": "Infinity"}, "unit_cost"), ({"unit_cost": "10000001"}, "unit_cost"),
    ({"currency": ""}, "currency"), ({"currency": "USD"}, "GBP"),
    ({"pack_quantity": ""}, "pack_quantity"), ({"pack_quantity": "0"}, "pack_quantity"),
    ({"pack_quantity": "1.5"}, "pack_quantity"), ({"pack_quantity": "１"}, "pack_quantity"),
    ({"minimum_order_quantity": "0"}, "minimum_order_quantity"),
    ({"minimum_order_quantity": "-1"}, "minimum_order_quantity"),
    ({"minimum_order_quantity": "2.5"}, "minimum_order_quantity"),
    ({"shipping_cost": "-1"}, "shipping_cost"), ({"shipping_cost": "NaN"}, "shipping_cost"),
    ({"stock_status": "maybe"}, "stock_status"),
    ({"stock_status": "in_stock", "available_quantity": "0"}, "conflicts"),
    ({"stock_status": "out_of_stock", "available_quantity": "1"}, "conflicts"),
    ({"available_quantity": "-1"}, "available_quantity"),
    ({"vat_treatment": "yes"}, "vat_treatment"),
    ({"observed_at": ""}, "observed_at"), ({"observed_at": "2025-01-02"}, "observed_at"),
    ({"observed_at": "2025-01-02T12:30:00"}, "observed_at"),
    ({"observed_at": "not a date"}, "observed_at"),
    ({"observed_at": "0001-01-01T00:00:00+14:00"}, "observed_at"),
    ({"observed_at": "2099-01-02T12:30:00+00:00"}, "observed_at"),
    ({"ean": "4006381333932"}, "ean"), ({"ean": "00000000"}, "ean"),
    ({"ean": "12345"}, "ean"), ({"ean": "4.006381333931E+12"}, "ean"),
    ({"ean": "4006381333931.0"}, "ean"), ({"ean": "４００６３８１３３３９３１"}, "ean"),
    ({"ean": "4006-381333931"}, "ean"), ({"ean": "4006 381333931"}, "ean"),
    ({"product_url": ""}, "supplier_url"),
    ({"product_url": "javascript:alert(1)"}, "supplier_url"),
    ({"product_url": "https://user:secret@example.com/product"}, "supplier_url"),
    ({"product_url": "https://example.com:bad/product"}, "supplier_url"),
    ({"product_url": "https://example.com:0/product"}, "supplier_url"),
    ({"product_url": "https://exam ple.com/product"}, "supplier_url"),
    ({"product_url": "https://example.com/\x00product"}, "supplier_url"),
    ({"product_title": "x" * 2001}, "field"),
])
def test_bad_supplier_data_is_rejected_without_echoing_values(changes, field):
    result = CSVFeedConnector(feed(supplier_row(**changes))).read()
    assert not result.products and len(result.errors) == 1
    assert result.errors[0].startswith("Row 2:") and field in result.errors[0]
    assert "secret" not in result.errors[0]


@pytest.mark.parametrize("missing", ["supplier_name", "product_title", "sku", "unit_cost", "currency", "pack_quantity", "product_url", "observed_at"])
def test_real_required_columns_are_required(missing):
    row = supplier_row()
    row.pop(missing)
    result = CSVFeedConnector(feed(row)).read()
    assert not result.products and "Missing columns" in result.errors[0]


def test_source_override_permits_missing_source_column():
    row = supplier_row()
    row.pop("supplier_name")
    result = CSVFeedConnector(feed(row), "Imported supplier").read()
    assert not result.errors and result.products[0].source == "Imported supplier"


def test_legacy_aliases_retain_per_pack_delivery_semantics():
    row = supplier_row()
    for modern, legacy in [("supplier_name", "source"), ("product_title", "title"), ("product_url", "supplier_url")]:
        row[legacy] = row.pop(modern)
    row.pop("shipping_cost")
    row.pop("vat_treatment")
    row["delivery_cost"] = "3.00"
    row["supplier_vat_included"] = "false"
    result = CSVFeedConnector(feed(row)).read()
    assert not result.errors
    product = result.products[0]
    assert product.delivery_cost == Decimal("3.00") and product.shipping_cost == Decimal("12.00")
    assert product.vat_treatment == "excluded" and not product.supplier_vat_included


def test_ambiguous_shipping_conventions_are_rejected():
    result = CSVFeedConnector(feed(supplier_row(delivery_cost="2.00"))).read()
    assert not result.products and "supply only one" in result.errors[0]


@pytest.mark.parametrize("treatment,included", [("included", True), ("excluded", False), ("not_applicable", True), ("unknown", True)])
def test_explicit_vat_treatments_are_retained(treatment, included):
    result = CSVFeedConnector(feed(supplier_row(vat_treatment=treatment))).read()
    assert not result.errors and result.products[0].vat_treatment == treatment
    assert result.products[0].supplier_vat_included is included


def test_conflicting_vat_columns_are_rejected():
    result = CSVFeedConnector(feed(supplier_row(supplier_vat_included="true"))).read()
    assert not result.products and "conflicts" in result.errors[0]


def test_partial_valid_data_and_feed_duplicates_have_useful_errors():
    good = supplier_row()
    result = CSVFeedConnector(feed(good, supplier_row(sku="SKU-BAD", unit_cost="NaN"), good)).read()
    assert len(result.products) == 1 and len(result.errors) == 2
    assert result.errors[0].startswith("Row 3:") and "unit_cost" in result.errors[0]
    assert result.errors[1].startswith("Row 4:") and "duplicate" in result.errors[1]


@pytest.mark.parametrize("column", ["source", "title", "supplier_url", "gtin"])
def test_duplicate_alias_headers_are_rejected(column):
    row = supplier_row()
    row[column] = "duplicate"
    result = CSVFeedConnector(feed(row)).read()
    assert not result.products and "aliases" in result.errors[0]


@pytest.mark.parametrize("column", ["is_demo", "simulated", "is_simulated", "provenance"])
def test_csv_cannot_supply_its_own_trust_or_demo_classification(column):
    result = CSVFeedConnector(feed(supplier_row(**{column: "false"}))).read()
    assert not result.products and "provenance" in result.errors[0]


def test_supplier_names_containing_demo_do_not_change_provenance():
    result = CSVFeedConnector(feed(supplier_row(supplier_name="Demo Distribution Ltd"))).read()
    assert not result.errors and not result.products[0].is_demo


def test_explicit_demo_mode_allows_legacy_synthetic_rows():
    row = supplier_row(ean="fake-demo-barcode", currency="", observed_at="", vat_treatment="")
    result = CSVFeedConnector(feed(row), is_demo=True).read()
    assert not result.errors and result.products[0].is_demo
    assert result.products[0].currency == "GBP" and result.products[0].vat_treatment == "included"
    assert datetime.fromisoformat(result.products[0].observed_at).tzinfo is not None


def test_real_and_demo_source_sku_keys_are_distinct():
    text = feed(supplier_row()).getvalue()
    real = CSVFeedConnector(StringIO(text)).read().products[0]
    demo = CSVFeedConnector(StringIO(text), is_demo=True).read().products[0]
    assert real.product_key != demo.product_key


def test_case_whitespace_bom_and_extra_supplier_columns_are_supported():
    row = {f"  {key.upper()}  ": value for key, value in supplier_row().items()}
    row["Supplier category"] = "Stationery"
    content = "\ufeff" + feed(row).getvalue()
    result = CSVFeedConnector(StringIO(content)).read()
    assert not result.errors and result.products[0].source == "Test Wholesaler"


def test_timezone_is_normalized_to_utc():
    result = CSVFeedConnector(feed(supplier_row(observed_at="2025-01-02T13:30:00+01:00"))).read()
    assert not result.errors and result.products[0].observed_at == "2025-01-02T12:30:00+00:00"


@pytest.mark.parametrize("suffix", ["incomplete,row\n", "too,many," + "," * 40 + "\n", '"unclosed'])
def test_framing_failure_discards_previously_valid_rows(suffix):
    text = feed(supplier_row()).getvalue() + suffix
    result = CSVFeedConnector(StringIO(text)).read()
    assert not result.products and result.errors


def test_windows_crlf_utf8_and_quoted_multiline_product_titles_are_supported(tmp_path):
    row = supplier_row(product_title='Box, "large"\nBlue')
    path = tmp_path / "supplier stock £.csv"
    path.write_bytes(("\ufeff" + feed(row).getvalue().replace("\n", "\r\n")).encode("utf-8"))
    result = CSVFeedConnector(path).read()
    assert not result.errors and result.products[0].title == 'Box, "large"\r\nBlue'


def test_binary_uploaded_csv_is_supported_without_modifying_the_source():
    uploaded = BytesIO(feed(supplier_row()).getvalue().encode("utf-8-sig"))
    result = CSVFeedConnector(uploaded).read()
    assert not result.errors and len(result.products) == 1


def test_bad_utf8_gets_actionable_error():
    result = CSVFeedConnector(BytesIO(b"\xff\xfeinvalid")).read()
    assert not result.products and "UTF-8" in result.errors[0]


def test_maximum_file_size_is_enforced_before_parsing(monkeypatch):
    monkeypatch.setattr(csv_feed, "MAX_FILE_BYTES", 20)
    result = CSVFeedConnector(feed(supplier_row())).read()
    assert not result.products and "file limit" in result.errors[0]


def test_maximum_row_count_rejects_the_whole_feed(monkeypatch):
    monkeypatch.setattr(csv_feed, "MAX_ROWS", 1)
    result = CSVFeedConnector(feed(supplier_row(), supplier_row(sku="SKU-002"))).read()
    assert not result.products and "row limit" in result.errors[0]


def test_header_only_downloadable_template_has_no_fictional_quotes():
    path = Path(__file__).resolve().parents[1] / "data" / "supplier_template.csv"
    result = CSVFeedConnector(path).read()
    assert not result.errors and not result.products
    columns = next(csv.reader(StringIO(path.read_text(encoding="utf-8"))))
    assert {"supplier_name", "product_title", "shipping_cost", "minimum_order_quantity", "observed_at"}.issubset(columns)


def test_fetch_products_refuses_a_partly_rejected_feed():
    connector = CSVFeedConnector(feed(supplier_row(), supplier_row(sku="BAD", unit_cost="NaN")))
    with pytest.raises(ValueError, match="unit_cost"):
        list(connector.fetch_products())


@pytest.mark.parametrize("is_demo", ["false", 0, 1, None])
def test_demo_mode_requires_application_boolean(is_demo):
    with pytest.raises(ValueError, match="is_demo"):
        CSVFeedConnector(feed(supplier_row()), is_demo=is_demo)
