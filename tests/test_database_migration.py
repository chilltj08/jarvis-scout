"""Migration, immutable evidence, latest supplier feeds and manual persistence."""
from dataclasses import replace
from decimal import Decimal
import json
import sqlite3

import pytest

from jarvis_scout.database import Database, LEGACY_DEMO_SOURCE, SCHEMA_VERSION
from jarvis_scout.models import AmazonListing, SupplierProduct, json_safe


OBSERVED = "2026-01-01T12:00:00+00:00"


def product(**changes):
    result = SupplierProduct(
        source="Genuine wholesaler", sku="SKU-1", title="Storage box", brand="Storage Brand",
        model="BOX-1", ean="9990000000012", variation="Teal", pack_quantity=1,
        unit_cost=Decimal("9.99"), delivery_cost=Decimal("0.75"),
        supplier_url="https://example.com/product", observed_at=OBSERVED,
    )
    return replace(result, **changes)


def listing(**changes):
    result = AmazonListing(
        asin="B000000001", title="Storage box", brand="Storage Brand", model="BOX-1",
        ean="9990000000012", variation="Teal", pack_quantity=1, price=Decimal("29.99"),
        referral_fee_rate=Decimal("0.15"), fba_fee=Decimal("3.50"),
        demand_confidence=None, pricing_stability=None, competition=None,
        restriction_status="unknown", simulated=False, observed_at=OBSERVED,
        evidence_source="https://www.amazon.co.uk/dp/B000000001", identifier_verified=True,
        provenance="manual", fees_confirmed=False,
    )
    return replace(result, **changes)


def make_v1(path):
    """Use the previous schema and JSON, rather than the current initializer."""
    suppliers = [product(source=LEGACY_DEMO_SOURCE), product(), product(source="Demo Supplies Ltd")]
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE products(product_key TEXT PRIMARY KEY,payload TEXT NOT NULL,imported_at TEXT NOT NULL);
            CREATE TABLE scans(scan_id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,
                total INTEGER NOT NULL,qualified INTEGER NOT NULL,review INTEGER NOT NULL,
                unmatched INTEGER NOT NULL,settings TEXT NOT NULL);
            CREATE TABLE opportunities(id INTEGER PRIMARY KEY AUTOINCREMENT,scan_id INTEGER NOT NULL
                REFERENCES scans(scan_id),product_key TEXT NOT NULL,payload TEXT NOT NULL,
                review_note TEXT NOT NULL DEFAULT '');
            CREATE TABLE alert_events(product_key TEXT NOT NULL,asin TEXT NOT NULL,status TEXT NOT NULL,
                attempted_at REAL NOT NULL,PRIMARY KEY(product_key,asin));
            PRAGMA user_version=1;
        """)
        connection.execute("INSERT INTO scans VALUES(1,?,3,1,1,1,?)", (OBSERVED, '{"legacy": true}'))
        for index, supplier in enumerate(suppliers, 1):
            payload = json_safe(supplier)
            for name in ("is_demo", "minimum_order_quantity", "shipping_cost", "shipping_cost_known", "stock_status", "vat_treatment"):
                payload.pop(name)
            connection.execute("INSERT INTO products VALUES(?,?,?)", (supplier.product_key, json.dumps(payload), OBSERVED))
            snapshot = {"product_key": supplier.product_key, "supplier": payload,
                        "listing": {"asin": "B000000001", "simulated": True}, "simulated": True,
                        "qualified": index == 1, "profitability": {"profit": "8.61"},
                        "match": {"status": "exact", "confidence": 1.0, "reasons": []}}
            connection.execute("INSERT INTO opportunities VALUES(?,1,?,?,?)",
                               (index, supplier.product_key, json.dumps(snapshot), "Original reviewer note"))
            connection.execute("INSERT INTO alert_events VALUES(?,?,?,?)", (supplier.product_key, "B000000001", "sent", 1000.0))
    return suppliers


def raw_state(path):
    with sqlite3.connect(path) as connection:
        return {name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
                for name in ("scans", "opportunities", "alert_events")}


def test_v1_upgrade_preserves_snapshots_notes_and_alerts(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    old_products = make_v1(path)
    before = raw_state(path)
    db = Database(path)
    assert raw_state(path) == before
    assert len(db.products()) == 3
    assert len(db.products(is_demo=True)) == 1
    assert {item.source for item in db.products(is_demo=False)} == {"Genuine wholesaler", "Demo Supplies Ltd"}
    demo = db.products(is_demo=True)[0]
    assert demo.product_key != old_products[0].product_key
    assert db.product_rows(is_demo=True)[0]["product_key"] == demo.product_key
    for row in db.product_rows():
        assert row["imported_at"] == row["updated_at"] == row["observed_at"] == OBSERVED
        assert row["supplier"]["minimum_order_quantity"] == 1
    history = db.opportunities()
    assert history[-1]["supplier"]["is_demo"] is True
    assert history[-1]["data_mode"] == "demo"
    assert all(item["data_mode"] == "mixed" for item in history[:-1])
    assert all(item["profitability"] == {"profit": "8.61"} for item in history)
    assert all(item["review_note"] == "Original reviewer note" for item in history)
    with db.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert Database(path).product_rows() == db.product_rows()
    assert raw_state(path) == before


def test_explicit_legacy_real_flag_overrides_fictional_source_name(tmp_path):
    path = tmp_path / "explicit.sqlite3"
    products = make_v1(path)
    with sqlite3.connect(path) as connection:
        payload = json_safe(products[0])
        connection.execute("UPDATE products SET payload=? WHERE product_key=?", (json.dumps(payload), products[0].product_key))
        old_snapshot = json.loads(connection.execute("SELECT payload FROM opportunities WHERE id=1").fetchone()[0])
        old_snapshot["supplier"]["is_demo"] = False
        connection.execute("UPDATE opportunities SET payload=? WHERE id=1", (json.dumps(old_snapshot),))
    db = Database(path)
    assert db.products(is_demo=True) == []
    assert db.opportunities(1)[-1]["data_mode"] == "mixed"


def test_schema_zero_database_is_initialized_and_reusable(tmp_path):
    path = tmp_path / "new" / "scout.sqlite3"
    db = Database(path)
    assert db.products() == db.product_rows() == db.manual_comparisons() == []
    assert db.opportunities() == db.list_scans() == []
    assert Database(path).latest_scan_id() is None


def test_future_database_version_is_rejected_without_changing_tables(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
        connection.execute("CREATE TABLE future_marker(value TEXT)")
        connection.execute("INSERT INTO future_marker VALUES('keep')")
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("future_marker",)]


def test_failed_migration_rolls_back_schema_and_product_changes(tmp_path):
    path = tmp_path / "broken.sqlite3"
    suppliers = make_v1(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE products SET payload='not JSON' WHERE product_key=?", (suppliers[1].product_key,))
        before = connection.execute("SELECT * FROM products ORDER BY product_key").fetchall()
    with pytest.raises(json.JSONDecodeError):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "updated_at" not in {row[1] for row in connection.execute("PRAGMA table_info(products)")}
        assert connection.execute("SELECT * FROM products ORDER BY product_key").fetchall() == before
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='manual_comparisons'").fetchall() == []


def test_sqlite_backup_of_current_database_preserves_manual_and_supplier_records(tmp_path):
    db = Database(tmp_path / "source.sqlite3")
    supplier = product()
    db.upsert_products([supplier])
    db.save_manual_comparison(supplier.product_key, listing())
    copy = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(db.path) as source, sqlite3.connect(copy) as destination:
        source.backup(destination)
    snapshot = Database(copy)
    assert snapshot.product_rows() == db.product_rows()
    assert snapshot.manual_comparisons() == db.manual_comparisons()


def test_demo_and_real_product_keys_are_independent(tmp_path):
    db = Database(tmp_path / "scout.sqlite3")
    real, demo = product(), product(is_demo=True)
    assert real.product_key != demo.product_key
    assert db.upsert_products([real, demo]) == 2
    assert len(db.products()) == 2
    assert db.products(False) == [real]
    assert db.products(True) == [demo]
    with pytest.raises(ValueError, match="filter"):
        db.products("false")


def test_latest_feed_wins_and_older_feed_does_not_refresh_storage_timestamp(tmp_path):
    db = Database(tmp_path / "scout.sqlite3")
    latest = product(observed_at="2026-01-01T13:00:00+00:00")
    assert db.upsert_products([latest]) == 1
    original = db.product_rows()[0]
    older = replace(latest, unit_cost=Decimal("1.00"), observed_at="2026-01-01T13:30:00+01:00")
    assert db.upsert_products([older]) == 0
    assert db.product_rows()[0] == original
    assert db.upsert_products([latest]) == 0
    newer = replace(latest, unit_cost=Decimal("7.00"), observed_at="2026-01-01T09:00:00-05:00")
    assert db.upsert_products([newer]) == 1
    saved = db.product_rows()[0]
    assert saved["supplier"]["unit_cost"] == "7.00"
    assert saved["imported_at"] == original["imported_at"]
    assert saved["updated_at"] >= original["updated_at"]


def test_batch_duplicates_use_latest_observation_and_count_unique_applied_products(tmp_path):
    db = Database(tmp_path / "scout.sqlite3")
    old = product()
    new = replace(old, observed_at="2026-01-02T00:00:00+00:00", unit_cost=Decimal("8.50"))
    assert db.upsert_products([old, new, old]) == 1
    assert db.products() == [new]


@pytest.mark.parametrize("observed", ["2026-01-01", "bad", None])
def test_invalid_supplier_timestamp_rejects_entire_batch(tmp_path, observed):
    db = Database(tmp_path / "scout.sqlite3")
    with pytest.raises(ValueError, match="observed_at"):
        db.upsert_products([product(), product(sku="SKU-2", observed_at=observed)])
    assert db.products() == []


def test_manual_comparison_upsert_keeps_id_and_missing_values(tmp_path):
    db = Database(tmp_path / "scout.sqlite3")
    supplier = product()
    db.upsert_products([supplier])
    incomplete = listing(price=None, referral_fee_rate=None, fba_fee=None)
    saved_id = db.save_manual_comparison(supplier.product_key, incomplete)
    saved = db.manual_comparisons(supplier.product_key)[0]
    assert saved["id"] == saved_id
    assert saved["listing"]["price"] is saved["listing"]["referral_fee_rate"] is saved["listing"]["fba_fee"] is None
    assert saved["listing"]["demand_confidence"] is None
    assert db.save_manual_comparison(supplier.product_key, listing()) == saved_id
    assert len(db.manual_comparisons()) == 1
    assert Database(db.path).manual_comparisons()[0]["listing"]["price"] == "29.99"
    assert db.manual_comparisons("missing") == []
    db.delete_manual_comparison(saved_id)
    assert db.manual_comparisons() == []
    with pytest.raises(ValueError, match="does not exist"):
        db.delete_manual_comparison(saved_id)


@pytest.mark.parametrize("change", [{"simulated": True}, {"evidence_source": ""}, {"asin": "invalid"}])
def test_invalid_manual_observations_are_not_persisted(tmp_path, change):
    db = Database(tmp_path / "scout.sqlite3")
    supplier = product()
    db.upsert_products([supplier])
    with pytest.raises(ValueError):
        db.save_manual_comparison(supplier.product_key, listing(**change))
    assert db.manual_comparisons() == []


def test_manual_comparison_requires_existing_real_supplier(tmp_path):
    db = Database(tmp_path / "scout.sqlite3")
    demo = product(is_demo=True)
    db.upsert_products([demo])
    with pytest.raises(ValueError, match="real supplier"):
        db.save_manual_comparison(demo.product_key, listing())
    with pytest.raises(ValueError, match="does not exist"):
        db.save_manual_comparison("unknown-product", listing())
    assert db.manual_comparisons() == []


def test_history_annotations_distinguish_real_manual_from_real_mock(tmp_path):
    db = Database(tmp_path / "scout.sqlite3")
    supplier = product()
    base = {"product_key": supplier.product_key, "supplier": json_safe(supplier),
            "match": {"status": "exact"}, "qualified": False}
    records = [{**base, "listing": json_safe(listing()), "simulated": False},
               {**base, "listing": json_safe(listing(simulated=True)), "simulated": True}]
    scan_id, saved = db.save_scan(records, {})
    assert [row["data_mode"] for row in saved] == ["real", "mixed"]
    assert db.opportunities(scan_id) == list(reversed(saved))
