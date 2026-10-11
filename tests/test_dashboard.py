"""Exercise the user workflow, persisted review notes and saved scan snapshots."""
from pathlib import Path

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

    button(app, "Run simulated scan").click().run()
    assert any("Import a CSV" in item.value for item in app.warning)
    assert Database(database_path).latest_scan_id() is None

    button(app, "Import demonstration feed").click().run()
    assert not app.exception
    assert metric(app, "Supplier products") == "8"
    button(app, "Run simulated scan").click().run()
    assert not app.exception
    assert metric(app, "Qualified · simulated") == "1"
    assert metric(app, "Review / unmatched") == "4"
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
