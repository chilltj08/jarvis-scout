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
    imported = import_feed(ROOT / "data" / "supplier_demo.csv", db, is_demo=True)
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
    import_feed(StringIO(HEADER + ROW), db, is_demo=True)
    first = run_scan(db, MockAmazonAdapter())
    old_cost = first.records[0]["profitability"]["purchase_cost"]
    import_feed(StringIO(HEADER + ROW.replace("9.99", "19.99")), db, is_demo=True)
    assert len(db.products()) == 1
    second = run_scan(db, MockAmazonAdapter(), ProfitAssumptions(packaging_cost=Decimal("1.00")))
    assert not second.records[0]["qualified"]
    assert db.opportunities(first.scan_id)[0]["profitability"]["purchase_cost"] == old_cost
    assert db.list_scans()[0]["settings"]["assumptions"]["packaging_cost"] == "1.00"
    assert db.list_scans()[1]["settings"]["assumptions"]["packaging_cost"] == "0.50"
    assert db.latest_scan_id() == second.scan_id


def test_invalid_rows_reported_good_rows_persist_and_duplicates_reported(db):
    bad = ROW.replace("sku1", "sku2").replace("9.99", "NaN")
    report = import_feed(StringIO(HEADER + ROW + bad + ROW), db, is_demo=True)
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
    report = CSVFeedConnector(text, is_demo=True).read()
    assert not report.products and len(report.errors) == 1


def test_csv_preserves_barcode_as_text_and_supports_source_override():
    feed = StringIO(HEADER + ROW.replace("9990000000012", "0999000000012").replace("Shop,", ","))
    report = CSVFeedConnector(feed, "New source", is_demo=True).read()
    assert report.errors == []
    assert report.products[0].ean == "0999000000012"
    assert report.products[0].source == "New source"


@pytest.mark.parametrize("text", ["", "sku,sku\na,b\n", "sku,title\na,b\n", HEADER + ROW.rstrip() + ",extra\n"])
def test_malformed_feed_explains_failure(text):
    report = CSVFeedConnector(StringIO(text), is_demo=True).read()
    assert report.errors and not report.products


def test_scan_adapter_failure_does_not_save_partial_scan(db):
    import_feed(ROOT / "data" / "supplier_demo.csv", db, is_demo=True)

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
    import_feed(StringIO(HEADER + ROW), db, is_demo=True)

    class InvalidAdapter:
        def search(self, product):
            return [replace(MockAmazonAdapter().search(product)[0], demand_confidence=float("nan"))]

    with pytest.raises(ValueError, match="demand_confidence"):
        run_scan(db, InvalidAdapter())
    assert db.list_scans() == []


def test_adapter_decimal_confidence_values_are_valid_ranking_inputs(db):
    import_feed(StringIO(HEADER + ROW), db, is_demo=True)

    class DecimalAdapter:
        def search(self, product):
            return [replace(MockAmazonAdapter().search(product)[0],
                            demand_confidence=Decimal("0.80"), pricing_stability=Decimal("0.85"))]

    report = run_scan(db, DecimalAdapter())
    assert report.qualified == 1 and 0 < report.records[0]["score"] <= 100


def test_stale_out_of_stock_and_stricter_rules_block_deal(db):
    old_observation = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    stale_header = HEADER.rstrip() + ",observed_at,available_quantity\n"
    stale_row = ROW.rstrip() + f",{old_observation},0\n"
    import_feed(StringIO(stale_header + stale_row), db, is_demo=True)
    report = run_scan(db, MockAmazonAdapter(), filters=OpportunityFilters(min_profit=Decimal("100")))
    assert report.qualified == 0
    assert {"Supplier observation is stale or invalid", "Supplier reports out of stock", "Profit below minimum"}.issubset(set(report.records[0]["filter_reasons"]))


def test_manual_notes_persist_without_approving(db):
    import_feed(ROOT / "data" / "supplier_demo.csv", db, is_demo=True)
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


def real_feed(**changes):
    """Current supplier quote; keep identifiers identical to a demo safety fixture."""
    import csv
    row = {
        "supplier_name": "Authorised UK supplier", "product_title": "Storage box",
        "brand": "Northline Storage", "ean": "9990000000012", "sku": "real-sku",
        "unit_cost": "9.99", "currency": "GBP", "vat_treatment": "included",
        "minimum_order_quantity": "2", "pack_quantity": "1", "shipping_cost": "4.00",
        "stock_status": "in_stock", "product_url": "https://example.com/supplier/product",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "model": "NLS-750", "variation": "Teal",
    }
    row.update(changes)
    feed = StringIO()
    writer = csv.DictWriter(feed, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    feed.seek(0)
    return feed


def manual_comparison(**changes):
    from jarvis_scout.manual import create_manual_listing
    values = {
        "asin": "B0MANUAL01", "title": "Storage box", "brand": "Northline Storage",
        "ean": "9990000000012", "pack_quantity": 1, "price": Decimal("50.00"),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence_source": "Dated supplier/listing barcode photo and recorded UK offer",
        "model": "NLS-750", "variation": "Teal", "referral_fee_rate": Decimal("0.15"),
        "fulfilment_fee": Decimal("3.20"), "identifier_verified": True,
        "fees_confirmed": True,
    }
    values.update(changes)
    return create_manual_listing(**values)


def test_real_import_matching_demo_barcode_never_uses_mock_resale_price(db):
    assert import_feed(real_feed(), db).imported == 1
    result = run_scan(db, MockAmazonAdapter())
    row = result.records[0]
    assert result.total == 1 and result.qualified == 0
    assert row["supplier"]["unit_cost"] == "9.99"
    assert not row["supplier"]["is_demo"] and row["data_mode"] == "real"
    assert row["listing"] is None and row["profitability"] is None
    assert row["estimate_status"] == "incomplete"
    assert any("Mock Amazon data was excluded" in reason for reason in row["incomplete_reasons"])


def test_real_scan_without_manual_price_does_not_even_open_demo_catalogue(db, tmp_path):
    from jarvis_scout.manual import ResearchAmazonAdapter
    import_feed(real_feed(), db)
    adapter = ResearchAmazonAdapter(db, tmp_path / "does-not-exist-demo.json")
    row = run_scan(db, adapter).records[0]
    assert row["data_mode"] == "real" and not row["simulated"]
    assert row["listing"] is None and row["profitability"] is None
    assert not row["qualified"] and row["estimate_status"] == "incomplete"


def test_real_manual_estimate_allocates_minimum_order_and_keeps_unknown_market_evidence(db):
    from jarvis_scout.manual import ManualAmazonAdapter
    import_feed(real_feed(), db)
    product = db.products()[0]
    db.save_manual_comparison(product.product_key, manual_comparison())
    assumptions = ProfitAssumptions(inbound_shipping_cost=Decimal("1.00"), other_costs=Decimal("0.25"))
    report = run_scan(db, ManualAmazonAdapter(db), assumptions)
    row = report.records[0]
    assert report.qualified == 1 and row["estimate_status"] == "complete"
    assert row["match"]["status"] == "exact" and row["data_mode"] == "real"
    assert row["minimum_order_cost"] == "23.98"
    assert row["supplier"]["unit_cost"] == "9.99"
    assert row["supplier"]["shipping_cost"] == "4.00"
    assert row["profitability"]["supplier_delivery"] == "2.00"
    assert row["profitability"]["inbound_shipping"] == "1.00"
    assert row["profitability"]["other_costs"] == "0.25"
    assert row["profitability"]["profit"] == "21.92"
    assert row["profitability"]["total_cost"] == "28.08"
    assert row["listing"]["price"] == "50.00" and not row["simulated"]
    for key in ("demand_confidence", "pricing_stability", "competition"):
        assert row["listing"][key] is None
    assert any("Seller eligibility is unverified" in warning for warning in row["warnings"])
    assert any("evidence is unavailable" in warning for warning in row["warnings"])


@pytest.mark.parametrize("changes, missing", [
    ({"price": None}, "Amazon selling price is missing"),
    ({"referral_fee_rate": None}, "Amazon referral fee estimate is missing"),
    ({"fulfilment_fee": None}, "Fulfilment fee estimate is missing"),
    ({"fees_confirmed": False}, "Fee estimates have not been confirmed"),
])
def test_real_manual_missing_costs_do_not_become_zero_cost_profit(db, changes, missing):
    from jarvis_scout.manual import ManualAmazonAdapter
    import_feed(real_feed(), db)
    db.save_manual_comparison(db.products()[0].product_key, manual_comparison(**changes))
    row = run_scan(db, ManualAmazonAdapter(db)).records[0]
    assert row["estimate_status"] == "incomplete" and row["profitability"] is None
    assert not row["qualified"]
    assert any(missing in reason for reason in row["incomplete_reasons"])


@pytest.mark.parametrize("changes, missing", [
    ({"vat_treatment": ""}, "VAT treatment is unknown"),
    ({"shipping_cost": ""}, "shipping cost is unknown"),
])
def test_supplier_unknown_cost_inputs_block_manual_estimate(db, changes, missing):
    from jarvis_scout.manual import ManualAmazonAdapter
    assert import_feed(real_feed(**changes), db).imported == 1
    db.save_manual_comparison(db.products()[0].product_key, manual_comparison())
    row = run_scan(db, ManualAmazonAdapter(db)).records[0]
    assert row["estimate_status"] == "incomplete" and not row["qualified"]
    assert row["profitability"] is None and row["minimum_order_cost"] is None
    assert any(missing in reason for reason in row["incomplete_reasons"])


@pytest.mark.parametrize("changes", [
    {"identifier_verified": False}, {"brand": "Other brand"}, {"pack_quantity": 2},
    {"variation": "Black"}, {"model": "Other model"},
])
def test_real_unverified_or_incompatible_product_is_review_without_profit(db, changes):
    from jarvis_scout.manual import ManualAmazonAdapter
    import_feed(real_feed(), db)
    db.save_manual_comparison(db.products()[0].product_key, manual_comparison(**changes))
    row = run_scan(db, ManualAmazonAdapter(db)).records[0]
    assert row["match"]["status"] == "review" and row["estimate_status"] == "review"
    assert row["profitability"] is None and not row["qualified"]


def test_real_manual_updates_do_not_rewrite_saved_scan_evidence(db):
    from jarvis_scout.manual import ManualAmazonAdapter
    import_feed(real_feed(), db)
    product_key = db.products()[0].product_key
    db.save_manual_comparison(product_key, manual_comparison())
    first = run_scan(db, ManualAmazonAdapter(db))
    db.save_manual_comparison(product_key, manual_comparison(price=Decimal("15.00")))
    second = run_scan(db, ManualAmazonAdapter(db))
    assert len(db.manual_comparisons(product_key)) == 1
    assert first.qualified == 1 and second.qualified == 0
    historical = Database(db.path).opportunities(first.scan_id)[0]
    assert historical["listing"]["price"] == "50.00"
    assert historical["profitability"] == first.records[0]["profitability"]
    assert db.opportunities(second.scan_id)[0]["listing"]["price"] == "15.00"


def test_real_default_scan_and_demo_scope_preserve_independent_provenance(db):
    from jarvis_scout.manual import ResearchAmazonAdapter
    import_feed(ROOT / "data" / "supplier_demo.csv", db, is_demo=True)
    import_feed(real_feed(), db)
    adapter = ResearchAmazonAdapter(db)
    all_results = run_scan(db, adapter)
    assert all_results.total == 9
    real = next(row for row in all_results.records if row["data_mode"] == "real")
    assert real["listing"] is None and not real["qualified"]
    demo = run_scan(db, adapter, is_demo=True)
    only_real = run_scan(db, adapter, is_demo=False)
    assert (demo.total, demo.qualified, only_real.total, only_real.qualified) == (8, 1, 1, 0)
    assert all(row["data_mode"] == "demo" and row["simulated"] for row in demo.records)
    assert only_real.records[0]["data_mode"] == "real"
