import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

import pytest

import jarvis_scout.__main__ as cli
import jarvis_scout.config as config_module
from jarvis_scout.__main__ import main
from jarvis_scout.database import Database
from jarvis_scout.scheduler import Scheduler


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def module_cli(tmp_path):
    """Run the public Windows/macOS command, without a shell or real credentials."""
    env = os.environ.copy()
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "SCOUT_DB_PATH": str(tmp_path / "environment database" / "scout.sqlite3"),
        "SCOUT_FEED_PATH": str(PROJECT_ROOT / "data" / "supplier_demo.csv"),
        "SCOUT_AMAZON_CATALOG": str(PROJECT_ROOT / "data" / "amazon_demo.json"),
        "TELEGRAM_BOT_TOKEN": "test-only-token-never-print-this",
        "TELEGRAM_CHAT_ID": "test-only-chat-never-print-this",
    })

    def run(*arguments):
        return subprocess.run(
            [sys.executable, "-m", "jarvis_scout", *map(str, arguments)],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=15, check=False,
        )

    return run


@pytest.mark.parametrize("arguments", [
    ("--help",), ("scan", "--help"), ("watch", "--help"),
    ("history", "--help"), ("import-csv", "--help"), ("demo", "--help"),
])
def test_module_entry_point_help_is_read_only(module_cli, tmp_path, arguments):
    result = module_cli(*arguments)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "No module named" not in result.stderr
    assert "test-only-token-never-print-this" not in result.stdout + result.stderr
    assert "test-only-chat-never-print-this" not in result.stdout + result.stderr
    assert not (tmp_path / "environment database").exists()


def test_module_help_lists_required_commands(module_cli):
    result = module_cli("--help")
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in ("scan", "watch", "history"))
    assert "SIMULATED" in result.stdout


def test_help_does_not_disclose_dotenv_secrets_or_create_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    # Record the original values with monkeypatch before dotenv mutates environ.
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SCOUT_DB_PATH"):
        monkeypatch.setenv(name, "temporary test placeholder")
        monkeypatch.delenv(name)
    database = tmp_path / "local database" / "scout.sqlite3"
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=dotenv-only-token-never-print-this\n"
        "TELEGRAM_CHAT_ID=dotenv-only-chat-never-print-this\n"
        f"SCOUT_DB_PATH={database}\n", encoding="utf-8",
    )
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    output = capsys.readouterr()
    assert "dotenv-only-token-never-print-this" not in output.out + output.err
    assert "dotenv-only-chat-never-print-this" not in output.out + output.err
    assert not database.parent.exists()


def test_module_import_scan_and_history_persist_across_processes(module_cli, tmp_path):
    # Spaces and Unicode are common in user directories on both Windows and macOS.
    local_folder = tmp_path / "Supplier files £"
    local_folder.mkdir()
    feed = local_folder / "supplier stock.csv"
    shutil.copyfile(PROJECT_ROOT / "data" / "supplier_demo.csv", feed)
    database = local_folder / "scout history.sqlite3"
    imported = module_cli("--db", database, "import-csv", feed, "--source", "Local CSV", "--demo")
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout) == {"imported": 8, "errors": []}

    scanned = module_cli("--db", database, "scan", "--scope", "demo", "--min-profit", "1.25", "--min-roi", "10")
    assert scanned.returncode == 0, scanned.stderr
    summary = json.loads(scanned.stdout)
    assert summary["total"] == 8 and summary["scan_id"] == 1
    assert "SIMULATED" in summary["data_mode"] and "alerts" not in summary

    history = module_cli("--db", database, "history")
    assert history.returncode == 0, history.stderr
    rows = json.loads(history.stdout)
    assert len(rows) == 1 and rows[0]["scan_id"] == summary["scan_id"]
    assert rows[0]["settings"]["filters"]["min_profit"] == "1.25"
    assert rows[0]["settings"]["filters"]["min_roi_percent"] == "10"
    assert rows[0]["settings"]["adapter"] == "ResearchAmazonAdapter"
    assert {product.source for product in Database(database).products()} == {"Local CSV"}


def test_module_demo_uses_environment_database(module_cli, tmp_path):
    demo = module_cli("demo")
    assert demo.returncode == 0, demo.stderr
    assert "SIMULATED" in json.loads(demo.stdout)["data_mode"]
    history = module_cli("history")
    assert history.returncode == 0, history.stderr
    assert len(json.loads(history.stdout)) == 1
    assert (tmp_path / "environment database" / "scout.sqlite3").is_file()


@pytest.mark.parametrize("arguments, message", [
    (("invalid-command",), "invalid choice"),
    (("scan", "--min-roi", "NaN"), "finite decimal"),
    (("scan", "--packaging", "not-money"), "decimal number"),
])
def test_module_argument_errors_do_not_create_database(module_cli, tmp_path, arguments, message):
    result = module_cli(*arguments)
    assert result.returncode == 2
    assert message in result.stderr and "Traceback" not in result.stderr
    assert "test-only-token-never-print-this" not in result.stderr
    assert "test-only-chat-never-print-this" not in result.stderr
    assert not (tmp_path / "environment database").exists()


def test_module_missing_csv_has_actionable_error(module_cli, tmp_path):
    result = module_cli("--db", tmp_path / "scout.sqlite3", "import-csv", tmp_path / "missing.csv")
    assert result.returncode == 2
    assert "Setup/input error:" in result.stderr and "missing.csv" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("interval_argument, expected_interval", [([], 0.125), (["--interval", "0.25"], 0.25)])
def test_watch_integrates_scheduler_and_database_without_alerts(
        tmp_path, monkeypatch, capsys, interval_argument, expected_interval):
    instances = []

    class FiniteScheduler(Scheduler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, wait=lambda _delay: False, **kwargs)
            instances.append(self)

        def run(self):
            return super().run(max_iterations=2)

    monkeypatch.setattr(cli, "Scheduler", FiniteScheduler)
    monkeypatch.setenv("SCOUT_SCAN_INTERVAL_SECONDS", "0.125")
    database = tmp_path / "watch history.sqlite3"
    main(["--db", str(database), "demo"])
    capsys.readouterr()
    previous_handler = signal.getsignal(signal.SIGINT)
    assert main(["--db", str(database), "watch", "--scope", "demo", *interval_argument]) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(output) == 2 and all(row["total"] == 8 for row in output)
    assert all("SIMULATED" in row["data_mode"] and "alerts" not in row for row in output)
    assert len(Database(database).list_scans()) == 3
    assert instances[0].interval_seconds == expected_interval
    assert instances[0].last_error is None
    assert signal.getsignal(signal.SIGINT) is previous_handler


def test_cli_alert_optin_persists_deduplication_without_network(tmp_path, monkeypatch, capsys):
    sent = []

    class LocalNotifier:
        configured = True

        def send(self, opportunity):
            sent.append(opportunity)

    monkeypatch.setattr(cli, "TelegramNotifier", LocalNotifier)
    monkeypatch.setenv("SCOUT_ALERT_COOLDOWN_HOURS", "24")
    database = tmp_path / "alert history.sqlite3"
    main(["--db", str(database), "demo"])
    capsys.readouterr()
    assert main(["--db", str(database), "scan", "--scope", "demo", "--alerts"]) == 0
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["alerts"]["skipped_simulated"] == 1 and not sent

    arguments = ["--db", str(database), "scan", "--scope", "demo", "--alerts", "--allow-simulated-alerts"]
    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert first["alerts"]["sent"] == 1 and repeated["alerts"]["suppressed"] == 1
    assert len(sent) == 1 and sent[0]["simulated"] is True
    assert len(Database(database).list_scans()) == 4


def test_cli_alerts_require_credentials_without_leaking_values(tmp_path, monkeypatch, capsys):
    # A fake unconfigured notifier prevents dotenv or a developer's credentials
    # from affecting this test and makes any attempted network use impossible.
    class UnconfiguredNotifier:
        configured = False

        def send(self, _opportunity):
            pytest.fail("An unconfigured notifier must never send a message")

    monkeypatch.setattr(cli, "TelegramNotifier", UnconfiguredNotifier)
    database = tmp_path / "scout.sqlite3"
    main(["--db", str(database), "demo"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        main(["--db", str(database), "scan", "--scope", "demo", "--alerts"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID securely" in output.err
    assert "Traceback" not in output.err and output.out == ""
    assert len(Database(database).list_scans()) == 1


def test_cli_demo_and_history(tmp_path, capsys):
    path = str(tmp_path / "scout.sqlite3")
    assert main(["--db", path, "demo"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == 8 and result["qualified"] == 1
    assert "SIMULATED" in result["data_mode"] and "alerts" not in result
    assert main(["--db", path, "history"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_empty_scan_has_clear_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(["--db", str(tmp_path / "empty.sqlite3"), "scan"])
    assert error.value.code == 2
    assert "No supplier products" in capsys.readouterr().err


@pytest.mark.parametrize("option, value, expected", [
    ("--packaging", "-1", "packaging_cost"),
    ("--returns-rate", "3", "returns_rate"),
    ("--vat-rate", "1.01", "vat_rate"),
])
@pytest.mark.parametrize("command", ["scan", "watch"])
def test_unmatched_products_cannot_hide_invalid_cost_assumptions(tmp_path, capsys, command, option, value, expected):
    from dataclasses import replace
    database = tmp_path / "unmatched.sqlite3"
    main(["--db", str(database), "demo"])
    db = Database(database)
    # None of these products can match, so validation must not depend on a sale calculation.
    products = [replace(product, ean=None, brand="Unknown", model="Unlisted", title="Unlisted item")
                for product in db.products()]
    db.upsert_products(products)
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        main(["--db", str(database), command, "--scope", "demo", option, value])
    assert error.value.code == 2
    assert expected in capsys.readouterr().err
    assert len(db.list_scans()) == 1


def test_simulation_alert_optin_requires_explicit_alert_action(tmp_path, capsys):
    path = str(tmp_path / "scout.sqlite3")
    main(["--db", path, "demo"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        main(["--db", path, "scan", "--allow-simulated-alerts"])
    assert error.value.code == 2 and "requires --alerts" in capsys.readouterr().err


def test_cli_input_vat_recovery_requires_explicit_optin(tmp_path, capsys):
    from jarvis_scout.database import Database
    path = str(tmp_path / "scout.sqlite3")
    main(["--db", path, "demo"])
    main(["--db", path, "scan", "--scope", "demo", "--vat-registered"])
    assert not Database(path).list_scans()[0]["settings"]["assumptions"]["reclaim_input_vat"]
    main(["--db", path, "scan", "--scope", "demo", "--vat-registered", "--reclaim-vat"])
    assert Database(path).list_scans()[0]["settings"]["assumptions"]["reclaim_input_vat"]


def write_real_supplier_csv(path):
    import csv
    from datetime import datetime, timezone
    columns = (
        "supplier_name", "product_title", "brand", "ean", "sku", "unit_cost", "currency",
        "vat_treatment", "minimum_order_quantity", "pack_quantity", "shipping_cost",
        "stock_status", "product_url", "observed_at",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerow((
            "Authorised CSV supplier", "Recorded storage box", "Northline Storage", "9990000000012",
            "real-sku", "9.99", "GBP", "included", "2", "1", "4.00", "in_stock",
            "https://example.com/supplier/product", datetime.now(timezone.utc).isoformat(),
        ))


def save_cli_manual_comparison(database, price="50.00"):
    from datetime import datetime, timezone
    from decimal import Decimal
    from jarvis_scout.manual import create_manual_listing
    db = Database(database)
    product = db.products(is_demo=False)[0]
    listing = create_manual_listing(
        asin="B0MANUAL01", title="Recorded storage box", brand="Northline Storage",
        ean="9990000000012", pack_quantity=1, price=Decimal(price),
        observed_at=datetime.now(timezone.utc).isoformat(),
        evidence_source="Dated UK offer screenshot and verified product barcode",
        referral_fee_rate=Decimal("0.15"), fulfilment_fee=Decimal("3.20"),
        identifier_verified=True, fees_confirmed=True,
    )
    db.save_manual_comparison(product.product_key, listing)


def test_cli_real_import_and_default_scan_cannot_inherit_demo_price(module_cli, tmp_path):
    database = tmp_path / "real history.sqlite3"
    feed = tmp_path / "supplier.csv"
    write_real_supplier_csv(feed)
    assert module_cli("--db", database, "demo").returncode == 0
    imported = module_cli("--db", database, "import-csv", feed)
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout) == {"imported": 1, "errors": []}
    scanned = module_cli("--db", database, "scan")
    assert scanned.returncode == 0, scanned.stderr
    summary = json.loads(scanned.stdout)
    assert summary["total"] == 1 and summary["qualified"] == 0 and summary["incomplete"] == 1
    assert "REAL SUPPLIERS" in summary["data_mode"]
    assert "SIMULATED" not in summary["data_mode"]
    row = Database(database).opportunities(summary["scan_id"])[0]
    assert row["listing"] is None and row["profitability"] is None
    assert row["supplier"]["unit_cost"] == "9.99" and not row["supplier"]["is_demo"]


def test_cli_saved_manual_observation_works_without_seller_central(module_cli, tmp_path):
    database = tmp_path / "manual comparison.sqlite3"
    feed = tmp_path / "supplier.csv"
    write_real_supplier_csv(feed)
    assert module_cli("--db", database, "import-csv", feed).returncode == 0
    save_cli_manual_comparison(database)
    result = module_cli("--db", database, "scan", "--inbound-shipping", "1.00", "--other-costs", "0.25")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["total"] == 1 and summary["qualified"] == 1 and summary["incomplete"] == 0
    assert "REAL SUPPLIERS" in summary["data_mode"] and "alerts" not in summary
    db = Database(database)
    saved = db.opportunities(summary["scan_id"])[0]
    assert saved["listing"]["price"] == "50.00" and saved["listing"]["provenance"] == "manual"
    assert saved["profitability"]["profit"] == "21.92"
    assert db.list_scans()[0]["settings"]["scope"] == "real"
    assert db.list_scans()[0]["settings"]["assumptions"]["inbound_shipping_cost"] == "1.00"


def test_cli_strict_evidence_flags_exclude_unverified_market_signals(module_cli, tmp_path):
    database = tmp_path / "strict.sqlite3"
    feed = tmp_path / "supplier.csv"
    write_real_supplier_csv(feed)
    assert module_cli("--db", database, "import-csv", feed).returncode == 0
    save_cli_manual_comparison(database)
    result = module_cli("--db", database, "scan", "--require-market-signals", "--require-eligible")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["qualified"] == 0 and summary["incomplete"] == 0
    row = Database(database).opportunities(summary["scan_id"])[0]
    assert row["profitability"] is not None
    assert {"Demand confidence is unknown", "Pricing stability is unknown", "Competition is unknown",
            "Listing eligibility is restricted or unverified"}.issubset(set(row["filter_reasons"]))


def test_cli_watch_reads_new_manual_evidence_without_renewing_supplier_time(tmp_path, monkeypatch, capsys):
    database = tmp_path / "watch research.sqlite3"
    feed = tmp_path / "supplier.csv"
    write_real_supplier_csv(feed)
    assert main(["--db", str(database), "import-csv", str(feed)]) == 0
    save_cli_manual_comparison(database)
    db = Database(database)
    original_observation = db.products(is_demo=False)[0].observed_at

    def change_evidence_between_scans(_delay):
        save_cli_manual_comparison(database, price="15.00")
        return False

    class FiniteScheduler(Scheduler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, wait=change_evidence_between_scans, **kwargs)

        def run(self):
            return super().run(max_iterations=2)

    monkeypatch.setattr(cli, "Scheduler", FiniteScheduler)
    capsys.readouterr()
    assert main(["--db", str(database), "watch", "--interval", "0.001"]) == 0
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["qualified"] for row in summaries] == [1, 0]
    assert all(row["total"] == 1 and "REAL SUPPLIERS" in row["data_mode"] for row in summaries)
    assert db.opportunities(summaries[0]["scan_id"])[0]["listing"]["price"] == "50.00"
    assert db.opportunities(summaries[1]["scan_id"])[0]["listing"]["price"] == "15.00"
    assert db.products(is_demo=False)[0].observed_at == original_observation


def test_cli_manual_research_alerts_keep_real_evidence_and_persistent_cooldown(tmp_path, monkeypatch, capsys):
    sent = []

    class LocalNotifier:
        configured = True

        def send(self, opportunity):
            sent.append(opportunity)

    database = tmp_path / "real alert history.sqlite3"
    feed = tmp_path / "supplier.csv"
    write_real_supplier_csv(feed)
    assert main(["--db", str(database), "import-csv", str(feed)]) == 0
    save_cli_manual_comparison(database)
    monkeypatch.setattr(cli, "TelegramNotifier", LocalNotifier)
    monkeypatch.setenv("SCOUT_ALERT_COOLDOWN_HOURS", "24")
    capsys.readouterr()
    arguments = ["--db", str(database), "scan", "--alerts"]
    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert first["alerts"]["sent"] == 1 and repeated["alerts"]["suppressed"] == 1
    assert len(sent) == 1
    assert sent[0]["data_mode"] == "real" and sent[0]["listing"]["provenance"] == "manual"
    assert sent[0]["supplier"]["is_demo"] is False and sent[0]["simulated"] is False
