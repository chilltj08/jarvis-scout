from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from jarvis_scout.connectors.amazon import MockAmazonAdapter
from jarvis_scout.connectors.csv_feed import CSVFeedConnector
from jarvis_scout.database import Database
from jarvis_scout.filters import OpportunityFilters
from jarvis_scout.pipeline import import_feed, run_scan
from jarvis_scout.profitability import ProfitAssumptions

ROOT = Path(__file__).resolve().parents[1]
HEADER = "source,sku,title,brand,model,ean,variation,pack_quantity,unit_cost,delivery_cost,supplier_url\n"
ROW = "Shop,sku1,Storage box,Northline Storage,NLS-750,9990000000012,Teal,1,9.99,0.75,https://example.com/product\n"


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "var" / "scout.sqlite3")


def test_end_to_end_demo_produces_conservative_shortlist(db):
    imported = import_feed(ROOT / "data" / "supplier_demo.csv", db)
    assert imported.imported == 8 and imported.errors == []
    result = run_scan(db, MockAmazonAdapter())
    assert (result.total, result.qualified, result.review, result.unmatched) == (8, 1, 3, 1)
    deal = next(row for row in result.records if row["qualified"])
    assert deal["supplier"]["sku"] == "NLS750"
    assert deal["simulated"] and deal["listing"]["simulated"]
    assert deal["profitability"]["profit"] == "8.61"
    assert deal["profitability"]["total_cost"] == "21.38"
    assert deal["profitability"]["roi_percent"] == "40.27"
    for row in result.records:
        if row["match"]["status"] != "exact":
            assert row["profitability"] is None and not row["qualified"]
        if row["listing"] and row["listing"]["restriction_status"] != "eligible":
            assert not row["qualified"] and row["score"] == 0
    assert Database(db.path).opportunities(result.scan_id) == list(reversed(result.records))


def test_upsert_keeps_history_and_assumptions_immutable(db):
    import_feed(StringIO(HEADER + ROW), db)
    first = run_scan(db, MockAmazonAdapter())
    old_cost = first.records[0]["profitability"]["purchase_cost"]
    import_feed(StringIO(HEADER + ROW.replace("9.99", "19.99")), db)
    assert len(db.products()) == 1
    second = run_scan(db, MockAmazonAdapter(), ProfitAssumptions(packaging_cost=Decimal("1.00")))
    assert not second.records[0]["qualified"]
    assert db.opportunities(first.scan_id)[0]["profitability"]["purchase_cost"] == old_cost
    assert db.list_scans()[0]["settings"]["assumptions"]["packaging_cost"] == "1.00"
    assert db.list_scans()[1]["settings"]["assumptions"]["packaging_cost"] == "0.50"
    assert db.latest_scan_id() == second.scan_id


def test_invalid_rows_reported_good_rows_persist_and_duplicates_reported(db):
    bad = ROW.replace("sku1", "sku2").replace("9.99", "NaN")
    report = import_feed(StringIO(HEADER + ROW + bad + ROW), db)
    assert report.imported == 1 and len(report.errors) == 2
    assert "unit_cost" in report.errors[0]
    assert "duplicate" in report.errors[1]
    assert len(db.products()) == 1


@pytest.mark.parametrize("change", [
    {"pack_quantity": "0"}, {"pack_quantity": "1.5"},
    {"unit_cost": "-1"}, {"unit_cost": "Infinity"}, {"delivery_cost": "NaN"},
    {"supplier_url": "javascript:alert(1)"}, {"supplier_url": "https://user:password@example.com"},
    {"supplier_url": "https://example.com:bad/product"}, {"supplier_url": "https://exam ple.com"},
    {"currency": "USD"}, {"supplier_vat_included": "yes"},
    {"available_quantity": "-1"}, {"observed_at": "2026-01-01"},
    {"observed_at": "2099-01-01T00:00:00+00:00"}, {"source": ""}, {"sku": ""},
])
def test_csv_invalid_inputs_are_not_imported(change):
    import csv
    text = StringIO()
    row = next(csv.DictReader(StringIO(HEADER + ROW)))
    row.update(change)
    writer = csv.DictWriter(text, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    text.seek(0)
    report = CSVFeedConnector(text).read()
    assert not report.products and len(report.errors) == 1


def test_csv_preserves_barcode_as_text_and_supports_source_override():
    feed = StringIO(HEADER + ROW.replace("9990000000012", "0999000000012").replace("Shop,", ","))
    report = CSVFeedConnector(feed, "New source").read()
    assert report.errors == []
    assert report.products[0].ean == "0999000000012"
    assert report.products[0].source == "New source"


@pytest.mark.parametrize("text", ["", "sku,sku\na,b\n", "sku,title\na,b\n", HEADER + ROW.rstrip() + ",extra\n"])
def test_malformed_feed_explains_failure(text):
    report = CSVFeedConnector(StringIO(text)).read()
    assert report.errors and not report.products


def test_scan_adapter_failure_does_not_save_partial_scan(db):
    import_feed(ROOT / "data" / "supplier_demo.csv", db)

    class FailingAdapter:
        def __init__(self):
            self.calls = 0

        def search(self, product):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("Unavailable")
            return MockAmazonAdapter().search(product)

    with pytest.raises(RuntimeError, match="Unavailable"):
        run_scan(db, FailingAdapter())
    assert db.list_scans() == [] and db.opportunities() == []


def test_corrupt_external_evidence_aborts_scan(db):
    import_feed(StringIO(HEADER + ROW), db)

    class InvalidAdapter:
        def search(self, product):
            return [replace(MockAmazonAdapter().search(product)[0], demand_confidence=float("nan"))]

    with pytest.raises(ValueError, match="demand_confidence"):
        run_scan(db, InvalidAdapter())
    assert db.list_scans() == []


def test_adapter_decimal_confidence_values_are_valid_ranking_inputs(db):
    import_feed(StringIO(HEADER + ROW), db)

    class DecimalAdapter:
        def search(self, product):
            return [replace(MockAmazonAdapter().search(product)[0],
                            demand_confidence=Decimal("0.80"), pricing_stability=Decimal("0.85"))]

    report = run_scan(db, DecimalAdapter())
    assert report.qualified == 1 and 0 < report.records[0]["score"] <= 100


def test_stale_out_of_stock_and_stricter_rules_block_deal(db):
    import_feed(StringIO(HEADER + ROW), db)
    fresh = db.products()[0]
    db.upsert_products([replace(fresh, observed_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
                                available_quantity=0)])
    report = run_scan(db, MockAmazonAdapter(), filters=OpportunityFilters(min_profit=Decimal("100")))
    assert report.qualified == 0
    assert {"Supplier observation is stale or invalid", "Supplier reports out of stock", "Profit below minimum"}.issubset(set(report.records[0]["filter_reasons"]))


def test_manual_notes_persist_without_approving(db):
    import_feed(ROOT / "data" / "supplier_demo.csv", db)
    result = run_scan(db, MockAmazonAdapter())
    row = next(row for row in result.records if row["match"]["status"] == "review")
    db.set_review_note(row["id"], "Supplier photo shows a different pack; do not buy.")
    saved = next(item for item in Database(db.path).opportunities() if item["id"] == row["id"])
    assert saved["review_note"].startswith("Supplier photo")
    assert not saved["qualified"] and saved["match"]["status"] == "review"
    with pytest.raises(ValueError):
        db.set_review_note(999999, "missing")
    with pytest.raises(ValueError):
        db.set_review_note(row["id"], "x" * 4001)


@pytest.mark.parametrize("kwargs", [
    {"min_profit": Decimal("NaN")}, {"min_roi_percent": Decimal("-1")},
    {"min_demand_confidence": float("nan")}, {"min_pricing_stability": 1.1},
    {"max_competition": -1}, {"max_competition": True}, {"max_data_age_hours": 0},
])
def test_filter_settings_reject_invalid_values(kwargs):
    with pytest.raises(ValueError):
        OpportunityFilters(**kwargs)
