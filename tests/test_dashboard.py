"""Exercise the user workflow, persisted review notes and saved scan snapshots."""
from pathlib import Path
from io import BytesIO, StringIO

from jarvis_scout.models import json_safe, utc_now
from jarvis_scout.pipeline import import_feed

from streamlit.testing.v1 import AppTest

from jarvis_scout.database import Database
from jarvis_scout.notifications import TelegramNotifier


APP = Path(__file__).resolve().parents[1] / "app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def metric(app, label):
    return next(item.value for item in app.metric if item.label == label)


def prepare_app(tmp_path, monkeypatch):
    database_path = tmp_path / "dashboard.sqlite3"
    monkeypatch.setenv("SCOUT_DB_PATH", str(database_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dashboard-test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "dashboard-test-chat")
    root = APP.parent
    monkeypatch.setenv("SCOUT_FEED_PATH", str(root / "data" / "supplier_demo.csv"))
    monkeypatch.setenv("SCOUT_AMAZON_CATALOG", str(root / "data" / "amazon_demo.json"))
    return AppTest.from_file(str(APP), default_timeout=20).run(), database_path


def test_demo_import_scan_and_review_notes_without_sending_alerts(tmp_path, monkeypatch):
    sends = []
    monkeypatch.setattr(TelegramNotifier, "send", lambda self, record: sends.append(record))
    app, database_path = prepare_app(tmp_path, monkeypatch)
    assert not app.exception
    assert metric(app, "Supplier products") == "0"
    assert button(app, "Import uploaded CSV").disabled

    button(app, "Run research scan").click().run()
    assert any("Import a CSV" in item.value for item in app.warning)
    assert Database(database_path).latest_scan_id() is None

    button(app, "Import demonstration feed").click().run()
    assert not app.exception
    assert metric(app, "Supplier products") == "8"
    button(app, "Run simulated scan").click().run()
    assert not app.exception
    assert metric(app, "Qualified · simulated") == "1"
    assert metric(app, "Review / incomplete") == "4"
    assert all(record["simulated"] for record in Database(database_path).opportunities())

    app.text_area[0].input("Checked the supplier pack; still requires verification.").run()
    button(app, "Save review note").click().run()
    assert not app.exception
    records = Database(database_path).opportunities()
    assert sum(record["review_note"] == "Checked the supplier pack; still requires verification."
               for record in records) == 1
    assert sends == []


def test_filter_changes_create_new_snapshot_without_changing_history(tmp_path, monkeypatch):
    app, database_path = prepare_app(tmp_path, monkeypatch)
    button(app, "Import demonstration feed").click().run()
    button(app, "Run simulated scan").click().run()
    assert not app.exception
    first_scan = Database(database_path).latest_scan_id()
    assert metric(app, "Qualified · simulated") == "1"

    next(item for item in app.number_input if item.label == "Minimum ROI (%)").set_value(1000.0).run()
    # Draft settings do not rewrite the saved snapshot while a user adjusts them.
    assert metric(app, "Qualified · simulated") == "1"
    button(app, "Run simulated scan").click().run()
    assert not app.exception
    assert metric(app, "Qualified · simulated") == "0"
    scans = Database(database_path).list_scans()
    assert len(scans) == 2
    assert scans[0]["settings"]["filters"]["min_roi_percent"] == "1000.0"
    assert sum(record["qualified"] for record in Database(database_path).opportunities(first_scan)) == 1

    next(item for item in app.selectbox if item.label == "Saved scan").set_value(first_scan).run()
    assert not app.exception
    assert metric(app, "Qualified · simulated") == "1"


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def add_real_supplier(database_path):
    csv = StringIO(
        "supplier_name,product_title,brand,ean,sku,unit_cost,currency,vat_treatment,minimum_order_quantity,pack_quantity,shipping_cost,stock_status,product_url,observed_at\n"
        f"Permitted UK Wholesale,Example refill pair,Example,4006381333931,REAL-1,3.00,GBP,included,4,2,4.00,in_stock,https://example.com/real-product,{utc_now()}\n"
    )
    report = import_feed(csv, Database(database_path), is_demo=False)
    assert report.imported == 1 and not report.errors


def complete_comparison_form(app, *, price="20.00", evidence="https://www.amazon.co.uk/dp/B012345678"):
    for label, value in [
        ("Amazon ASIN", "B012345678"), ("Amazon listing title", "Example refill pair"),
        ("Amazon listing brand", "Example"), ("Amazon listing EAN / GTIN", "4006381333931"),
        ("Observed Amazon selling price (£)", price), ("Estimated referral fee (%)", "15"),
        ("Estimated fulfilment fee / pack (£)", "2.00"), ("Evidence source (required)", evidence),
    ]:
        widget(app, "text_input", label).input(value)
    widget(app, "number_input", "Amazon items per sellable pack").set_value(2)
    widget(app, "checkbox", "I checked the listing GTIN, brand and pack against evidence").check()
    widget(app, "checkbox", "Entered fee estimates cover this fulfilment method").check()
    button(app, "Save Amazon comparison").click().run()


def test_real_supplier_manual_comparison_scan_and_provenance(tmp_path, monkeypatch):
    sends = []
    monkeypatch.setattr(TelegramNotifier, "send", lambda self, record: sends.append(record))
    app, database_path = prepare_app(tmp_path, monkeypatch)
    add_real_supplier(database_path)
    app.run()
    assert not app.exception
    assert widget(app, "selectbox", "Research scope").value == "Real suppliers"
    assert metric(app, "Supplier products") == "1"
    assert widget(app, "text_input", "Observed Amazon selling price (£)").value == ""
    assert widget(app, "text_input", "Amazon listing EAN / GTIN").value == ""
    assert widget(app, "text_input", "Amazon listing brand").value == ""
    assert widget(app, "text_input", "Estimated referral fee (%)").value == ""
    assert widget(app, "text_input", "Estimated fulfilment fee / pack (£)").value == ""
    assert widget(app, "selectbox", "Listing eligibility").value == "unknown"
    assert not any("SIMULATED AMAZON DATA" in item.value for item in app.markdown)

    complete_comparison_form(app)
    assert not app.exception
    assert not app.error
    saved = Database(database_path).manual_comparisons()
    assert len(saved) == 1
    assert saved[0]["listing"]["price"] == "20.00"
    assert saved[0]["listing"]["demand_confidence"] is None
    button(app, "Run research scan").click().run()
    assert not app.exception
    assert metric(app, "Financial candidates") == "1"
    records = Database(database_path).opportunities()
    assert len(records) == 1
    assert records[0]["data_mode"] == "real" and not records[0]["simulated"]
    assert records[0]["match"]["status"] == "exact"
    assert records[0]["profitability"]["profit"] == "8.90"
    assert records[0]["minimum_order_cost"] == "16.00"
    assert any("eligibility" in text.lower() for text in records[0]["warnings"])
    assert any("unavailable" in text.lower() for text in records[0]["warnings"])
    assert any("no values were invented" in warning.value for warning in app.warning)
    assert sends == []

    # Importing a demonstration stays isolated and selecting real research restores its quote.
    button(app, "Import demonstration feed").click().run()
    assert widget(app, "selectbox", "Research scope").value == "Demo"
    assert metric(app, "Supplier products") == "8"
    widget(app, "selectbox", "Research scope").set_value("Real suppliers").run()
    assert not app.exception
    assert metric(app, "Supplier products") == "1"
    assert metric(app, "Financial candidates") == "1"


def test_manual_blank_price_is_saved_as_unknown_and_incomplete(tmp_path, monkeypatch):
    app, database_path = prepare_app(tmp_path, monkeypatch)
    add_real_supplier(database_path)
    app.run()
    complete_comparison_form(app, price="")
    assert not app.exception and not app.error
    comparison = Database(database_path).manual_comparisons()[0]
    assert comparison["listing"]["price"] is None
    button(app, "Run research scan").click().run()
    assert not app.exception
    record = Database(database_path).opportunities()[0]
    assert record["profitability"] is None and not record["qualified"]
    assert record["estimate_status"] == "incomplete"
    assert "Amazon selling price is missing" in record["incomplete_reasons"]
    assert metric(app, "Review / incomplete") == "1"
    assert any("Incomplete estimate" in item.value for item in app.warning)


def test_manual_observation_requires_evidence_and_can_update_saved_comparison(tmp_path, monkeypatch):
    app, database_path = prepare_app(tmp_path, monkeypatch)
    add_real_supplier(database_path)
    app.run()
    complete_comparison_form(app, evidence="")
    assert not app.exception
    assert any("evidence source" in error.value.lower() for error in app.error)
    assert Database(database_path).manual_comparisons() == []
    widget(app, "text_input", "Evidence source (required)").input("Manually checked listing and current fee calculator")
    button(app, "Save Amazon comparison").click().run()
    assert not app.exception and not app.error
    comparison_id = Database(database_path).manual_comparisons()[0]["id"]
    widget(app, "selectbox", "New or saved observation").set_value(comparison_id).run()
    assert widget(app, "text_input", "Observed Amazon selling price (£)").value == "20.00"
    widget(app, "text_input", "Observed Amazon selling price (£)").input("21.50")
    button(app, "Save Amazon comparison").click().run()
    assert not app.exception and not app.error
    saved = Database(database_path).manual_comparisons()
    assert len(saved) == 1 and saved[0]["id"] == comparison_id
    assert saved[0]["listing"]["price"] == "21.50"


def test_dashboard_upload_validates_rows_and_deduplicates_real_quotes(tmp_path, monkeypatch):
    observed_at = utc_now()
    content = (
        "supplier_name,product_title,brand,ean,sku,unit_cost,currency,vat_treatment,minimum_order_quantity,pack_quantity,shipping_cost,stock_status,product_url,observed_at\n"
        f"Permitted UK Wholesale,Example refill pair,Example,4006381333931,REAL-1,3.00,GBP,included,4,2,4.00,in_stock,https://example.com/product,{observed_at}\n"
        f"Permitted UK Wholesale,Malformed quote,Example,4006381333931,BAD-1,not-a-price,GBP,included,4,2,4.00,in_stock,https://example.com/product,{observed_at}\n"
    ).encode("utf-8")
    # AppTest does not offer a file uploader setter; provide the upload through
    # Streamlit's public input boundary and exercise the actual import button.
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: BytesIO(content))
    app, database_path = prepare_app(tmp_path, monkeypatch)
    assert not button(app, "Import uploaded CSV").disabled
    button(app, "Import uploaded CSV").click().run()
    assert not app.exception and not app.error
    products = Database(database_path).products()
    assert len(products) == 1 and not products[0].is_demo
    assert products[0].sku == "REAL-1"
    assert any("feed issue" in warning.value for warning in app.warning)
    assert metric(app, "Supplier products") == "1"
    assert widget(app, "selectbox", "Research scope").value == "Real suppliers"
    button(app, "Import uploaded CSV").click().run()
    assert not app.exception
    assert len(Database(database_path).products()) == 1
    assert any("updated 0" in message.value for message in app.success)


def test_legacy_mock_comparison_is_excluded_from_real_shortlist(tmp_path, monkeypatch):
    app, database_path = prepare_app(tmp_path, monkeypatch)
    add_real_supplier(database_path)
    database = Database(database_path)
    product = database.products()[0]
    database.save_scan([{
        "product_key": product.product_key, "supplier": json_safe(product),
        "listing": {"asin": "B012345678", "price": "20.00", "simulated": True},
        "match": {"status": "exact", "confidence": 1, "reasons": []},
        "profitability": {"profit": "14.00", "roi_percent": "100.00"},
        "qualified": True, "filter_reasons": [], "score": 90, "simulated": True,
    }], {})
    app.run()
    assert not app.exception
    assert metric(app, "Financial candidates") == "0"
    assert metric(app, "Review / incomplete") == "1"
    assert any("SIMULATED Amazon prices" in message.value for message in app.warning)
    legacy_tables = [frame.value for frame in app.dataframe if "Estimate" in frame.value.columns]
    assert len(legacy_tables) == 1
    assert legacy_tables[0].iloc[0]["Estimate"] == "Legacy simulated estimate"
    assert not legacy_tables[0].iloc[0]["Qualified"]


def test_remove_obsolete_observation_resolves_ambiguity_without_rewriting_history(tmp_path, monkeypatch):
    app, database_path = prepare_app(tmp_path, monkeypatch)
    add_real_supplier(database_path)
    app.run()
    complete_comparison_form(app)
    assert not app.exception and not app.error
    database = Database(database_path)
    obsolete_id = database.manual_comparisons()[0]["id"]
    widget(app, "selectbox", "New or saved observation").set_value(obsolete_id).run()
    widget(app, "text_input", "Amazon ASIN").input("B098765432")
    button(app, "Save Amazon comparison").click().run()
    assert not app.exception and not app.error
    assert len(database.manual_comparisons()) == 2
    button(app, "Run research scan").click().run()
    assert not app.exception
    previous_scan = database.latest_scan_id()
    original_snapshot = database.opportunities(previous_scan)
    assert original_snapshot[0]["match"]["status"] == "review"
    assert not original_snapshot[0]["qualified"]

    widget(app, "selectbox", "New or saved observation").set_value(obsolete_id).run()
    assert any("different ASIN creates an additional comparison" in caption.value for caption in app.caption)
    button(app, "Remove selected observation").click().run()
    assert not app.exception and not app.error
    assert widget(app, "selectbox", "New or saved observation").value is None
    remaining = database.manual_comparisons()
    assert len(remaining) == 1 and remaining[0]["listing"]["asin"] == "B098765432"
    assert database.opportunities(previous_scan) == original_snapshot
    button(app, "Run research scan").click().run()
    assert not app.exception
    new_scan = database.latest_scan_id()
    assert new_scan != previous_scan
    refreshed = database.opportunities(new_scan)[0]
    assert refreshed["match"]["status"] == "exact" and refreshed["qualified"]
    assert refreshed["listing"]["asin"] == "B098765432"
    assert database.opportunities(previous_scan) == original_snapshot
