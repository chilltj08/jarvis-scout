"""Local command-line entry point. Demo and ordinary scans never send alerts."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
import signal
import threading

from jarvis_scout.config import AppConfig
from jarvis_scout.connectors.amazon import MockAmazonAdapter
from jarvis_scout.database import Database
from jarvis_scout.filters import OpportunityFilters
from jarvis_scout.models import json_safe
from jarvis_scout.notifications import AlertService, TelegramNotifier
from jarvis_scout.pipeline import import_feed, run_scan
from jarvis_scout.profitability import ProfitAssumptions
from jarvis_scout.scheduler import Scheduler


def decimal_arg(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("Expected a decimal number") from None
    if not number.is_finite():
        raise argparse.ArgumentTypeError("Expected a finite decimal number")
    return number


def scan_options(parser):
    parser.add_argument("--min-profit", type=decimal_arg, default=Decimal("3"))
    parser.add_argument("--min-roi", type=decimal_arg, default=Decimal("30"), help="ROI percent using all modelled costs")
    parser.add_argument("--min-demand", type=float, default=0.5)
    parser.add_argument("--min-stability", type=float, default=0.5)
    parser.add_argument("--max-competition", type=int, default=10)
    parser.add_argument("--max-age-hours", type=float, default=48)
    parser.add_argument("--packaging", type=decimal_arg, default=Decimal("0.50"))
    parser.add_argument("--returns-rate", type=decimal_arg, default=Decimal("0.03"), help="Fraction, e.g. 0.03 means 3%%")
    parser.add_argument("--vat-registered", action="store_true")
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument("--reclaim-vat", action="store_true", dest="reclaim_vat", help="Explicitly assume eligible input VAT recovery with valid invoices")
    recovery.add_argument("--no-reclaim-vat", action="store_false", dest="reclaim_vat", help="Do not reclaim input VAT (the default)")
    parser.set_defaults(reclaim_vat=False)
    parser.add_argument("--vat-rate", type=decimal_arg, default=Decimal("0.20"))
    parser.add_argument("--purchase-vat-rate", type=decimal_arg, default=Decimal("0.20"))
    parser.add_argument("--delivery-vat-rate", type=decimal_arg, default=Decimal("0.20"))
    parser.add_argument("--fees-vat-rate", type=decimal_arg, default=Decimal("0.20"))
    parser.add_argument("--packaging-vat-rate", type=decimal_arg, default=Decimal("0.20"))
    parser.add_argument("--alerts", action="store_true", help="Explicitly send qualifying items through Telegram")
    parser.add_argument("--allow-simulated-alerts", action="store_true", help="Opt in to clearly SIMULATED demo alerts; requires --alerts")


def main(argv=None) -> int:
    config = AppConfig.from_env()
    parser = argparse.ArgumentParser(prog="jarvis_scout", description="JARVIS SCOUT — UK resale research; bundled Amazon data is SIMULATED")
    parser.add_argument("--db", default=str(config.db_path), help="Local SQLite path")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-csv", help="Import valid rows; report invalid rows")
    importer.add_argument("path")
    importer.add_argument("--source")
    commands.add_parser("demo", help="Import fictional products and run a simulated scan; no alerts")
    commands.add_parser("history", help="Show scan summaries")
    scan_parser = commands.add_parser("scan", help="Scan imported products against the simulated catalog")
    scan_options(scan_parser)
    watch_parser = commands.add_parser("watch", help="Repeat scans sequentially; Ctrl+C stops")
    scan_options(watch_parser)
    watch_parser.add_argument("--interval", type=float, default=None, help="Seconds between scan starts; default env or 300")
    args = parser.parse_args(argv)
    try:
        db = Database(args.db)
        if args.command == "import-csv":
            report = import_feed(args.path, db, args.source)
            print(json.dumps(json_safe(report), indent=2))
            return 2 if report.errors else 0
        if args.command == "history":
            print(json.dumps(db.list_scans(), indent=2))
            return 0
        if args.command == "demo":
            report = import_feed(config.feed_path, db)
            if report.errors:
                raise ValueError("Demo feed errors: " + "; ".join(report.errors))
            assumptions, filters = ProfitAssumptions(), OpportunityFilters()
        else:
            assumptions = ProfitAssumptions(
                packaging_cost=args.packaging, returns_rate=args.returns_rate,
                vat_registered=args.vat_registered, reclaim_input_vat=args.reclaim_vat,
                vat_rate=args.vat_rate, purchase_vat_rate=args.purchase_vat_rate,
                delivery_vat_rate=args.delivery_vat_rate, amazon_fees_vat_rate=args.fees_vat_rate,
                packaging_vat_rate=args.packaging_vat_rate,
            )
            filters = OpportunityFilters(args.min_profit, args.min_roi, args.min_demand, args.min_stability,
                                         args.max_competition, args.max_age_hours)
            if args.allow_simulated_alerts and not args.alerts:
                raise ValueError("--allow-simulated-alerts requires --alerts")
        if not db.products():
            raise ValueError("No supplier products imported. Run demo or import-csv first.")
        alerts = None
        if getattr(args, "alerts", False):
            notifier = TelegramNotifier()
            if not notifier.configured:
                raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID securely before enabling alerts")
            alerts = AlertService(args.db, notifier, cooldown_hours=float(os.getenv("SCOUT_ALERT_COOLDOWN_HOURS", "24")),
                                  allow_simulated=args.allow_simulated_alerts)

        def scan_once():
            try:
                result = run_scan(db, MockAmazonAdapter(config.amazon_catalog_path), assumptions, filters)
            except Exception as error:
                if args.command == "watch":
                    print(json.dumps({"scan_status": "failed", "error_type": type(error).__name__}), flush=True)
                raise
            summary = {"scan_id": result.scan_id, "total": result.total, "qualified": result.qualified,
                       "review": result.review, "unmatched": result.unmatched,
                       "data_mode": "SIMULATED — no live Amazon market checks"}
            if alerts:
                summary["alerts"] = alerts.dispatch(result.records)
            print(json.dumps(summary), flush=True)

        if args.command == "watch":
            interval = args.interval if args.interval is not None else float(os.getenv("SCOUT_SCAN_INTERVAL_SECONDS", "300"))
            stop = threading.Event()
            previous_handler = signal.signal(signal.SIGINT, lambda *_: stop.set())
            try:
                scheduler = Scheduler(scan_once, interval_seconds=interval, stop_event=stop)
                scheduler.run()
                if scheduler.last_error:
                    print("Scheduler observed an error: " + scheduler.last_error)
            finally:
                signal.signal(signal.SIGINT, previous_handler)
        else:
            scan_once()
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        # Only known local validation errors are printed; transport errors are sanitised in adapters.
        parser.exit(2, f"Setup/input error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
