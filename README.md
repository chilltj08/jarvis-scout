# JARVIS SCOUT

A portable research workspace for UK Amazon resale opportunities. Import retailer or wholesaler CSV quotes, compare the exact sellable product and pack, estimate profit, and review a ranked shortlist in your browser. Python, SQLite and Streamlit keep the MVP inexpensive and straightforward to maintain.

**This MVP uses simulated Amazon data. It does not discover live deals.** The included suppliers, products, prices, demand scores and eligibility decisions are fictional demonstrations. Importing a real supplier CSV does not make the Amazon comparison live. Simulations are labelled in the dashboard, exports and optional test alerts.

## Start on Windows

Install [Python 3.12](https://www.python.org/downloads/), then open PowerShell in the `jarvis-scout` folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m jarvis_scout demo
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Using the interpreter directly means you do not need to activate the environment or change PowerShell's execution policy. Open the local URL printed by Streamlit, usually `http://localhost:8501`. Stop the app with `Ctrl+C`.

## Start on macOS

Install Python 3.12, then open Terminal in the checkout:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m jarvis_scout demo
.venv/bin/python -m streamlit run app.py
```

The same dashboard works in your Mac's browser. Run commands from the project folder. Linux uses the same interpreter paths as macOS. Installation and validation in this repository's cloud environment use Linux; Windows and macOS instructions have not been exercised on those machines.

`requirements.lock` pins the application and development dependencies installed in the tested Linux Python 3.12 environment, including transitive dependencies. Pip selects compatible Windows/macOS wheels and may add platform-specific dependencies. `requirements.txt` pins direct application dependencies and pytest; `requirements-dev.txt` includes those same requirements. The demo needs no Amazon account, supplier credentials or Telegram token. Configuration is optional until you want to change paths or test notifications.

## Upgrade an existing Windows checkout

Stop your dashboard and scanner, open PowerShell in the existing project folder, and update from GitHub:

```powershell
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m jarvis_scout --help
.\.venv\Scripts\python.exe -m jarvis_scout demo
.\.venv\Scripts\python.exe -m jarvis_scout scan
.\.venv\Scripts\python.exe -m jarvis_scout history
```

The package now includes `jarvis_scout/__main__.py`, which provides `scan`, `watch`, `history`, `import-csv` and `demo`. If Python still reports a missing entry point, check which copy is being imported with `.\.venv\Scripts\python.exe -c "import jarvis_scout; print(jarvis_scout.__file__)"`; it should point into your updated checkout. Keep your private `.env` and existing database in place. The original `calculator.calculate` and `pipeline.discover` APIs remain available for the older fictional CSV demo; their simplified ROI and identity rules are separate from the upgraded scanner.

## Use the dashboard

1. Load the demonstration feed from the sidebar, or upload an authorised supplier CSV.
2. Set minimum profit, ROI, demand confidence, pricing stability, competition and freshness limits. Adjust packaging, returns and VAT assumptions.
3. Select **Run simulated scan**. The app saves the thresholds, assumptions and results as a SQLite snapshot.
4. Inspect product details, the complete cost breakdown and exclusion reasons. Open supplier links or export your shortlist as CSV.
5. Use **Review queue** for uncertain identities and **Scan history** to revisit earlier results.

Review notes record your investigation; they do not approve a match, bypass identity checks or enable alerts. Historical results keep their original costs and filters. Dashboard scans never send Telegram messages.

SQLite data defaults to `var/scout.sqlite3`. The app is a local, single-user tool; authentication must be added before exposing it as a public website.

## Import supplier quotes

Use a supplier-provided feed you have permission to process. CSV files must be UTF-8 (a UTF-8 BOM is supported), comma-separated, with a unique header. See [`data/supplier_demo.csv`](data/supplier_demo.csv) for a complete example.

| Column | Meaning |
| --- | --- |
| `source` | Supplier/source name; required unless `--source` is supplied. |
| `sku` | Supplier-local identifier; required, preserved as text. |
| `title` | Product title; required. |
| `unit_cost` | GBP cost of **one complete sellable pack**; required. No currency symbol. |
| `pack_quantity` | Units inside that pack; required positive integer. |
| `delivery_cost` | Supplier delivery cost **allocated to that same pack**; required. Use `0` when applicable. |
| `supplier_url` | HTTP(S) product URL without embedded credentials; required. |
| `brand`, `model`, `variation` | Identity fields; blank or conflicting fields require review. Include size, colour or flavour in `variation`. |
| `ean` | GTIN-8/12/13/14 as text; blank or invalid check digits require review. Preserve leading zeroes. |
| `currency` | Optional; defaults to `GBP`. Other currencies are rejected. |
| `supplier_vat_included` | Optional `true`/`false`; defaults to `true`. Applies to both `unit_cost` and `delivery_cost`. |
| `available_quantity` | Optional nonnegative number of available packs; `0` prevents qualification. Blank means unknown. |
| `observed_at` | Optional ISO timestamp with timezone, such as `2026-10-11T09:00:00Z`; future timestamps are rejected. |

For example, a pack of two filters costing £9 has `pack_quantity=2,unit_cost=9.00`. An order's £12 delivery split across 24 identical packs gives `delivery_cost=0.50`. Do not put the whole order's delivery against each pack or enter the per-item cost for a multi-item pack. Minimum order quantities and quantity-discount tiers are not modelled.

When `observed_at` is blank, the importer records the receipt time. This is an ingestion timestamp, not confirmation that a supplier or Amazon price was checked. Use the source's actual observation time whenever available. Spreadsheet software can remove GTIN leading zeroes: set the EAN column to text before exporting.

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m jarvis_scout import-csv .\feeds\supplier.csv --source "My Supplier"
.\.venv\Scripts\python.exe -m jarvis_scout scan
```

```bash
# macOS / Linux
.venv/bin/python -m jarvis_scout import-csv feeds/supplier.csv --source "My Supplier"
.venv/bin/python -m jarvis_scout scan
```

Valid rows are imported even when other rows fail; errors include row numbers, and the CLI exits with status `2` to signal an incomplete import. Re-importing updates the existing `(source, sku)` product. Products absent from the new file are retained; mark unavailable products with quantity `0` or use current observation timestamps rather than assuming a new feed removes them. Duplicate source/SKU rows inside one feed are rejected after the first occurrence.

Scanning checks all stored products against the bundled mock catalogue. A new real supplier product will usually be unmatched until a corresponding integration exists. The scheduler does not download feeds or re-import CSV files automatically.

## Matching, filters and scoring

An exact match requires one unambiguous candidate with the same checksum-valid GTIN, brand, model, variation and pack quantity. Brand/model/variation comparisons ignore case and repeated whitespace while retaining meaningful punctuation. A checksum proves identifier structure, not GS1 ownership or authenticity. Missing identifiers, missing attributes, conflicting identities and multiple matching ASINs go to review. Text similarity retrieves candidates but cannot prove identity.

The calculator will not split a bulk pack into single units or combine packs. A wholesaler case and an Amazon single-item listing require a separately verified workflow before they can be evaluated together.

Only exact matches can qualify. Default filters require £3 estimated profit, 30% ROI, demand confidence and stability of at least `0.5`, at most 10 competing offers, observations no older than 48 hours, and an eligible listing. Reported zero stock is excluded. Unknown or restricted eligibility is excluded. Real eligibility must be checked for your seller account; a catalogue listing alone does not grant permission to sell it.

The 0–100 ranking combines ROI (25%), profit (25%), demand confidence (20%), pricing stability (20%) and competition (10%). **The demo's demand and stability numbers are synthetic scores, not sales estimates or researched demand.** No historical price data or live competition data is retrieved.

## Profit calculation

Amounts use `Decimal` and round each cost/tax category to pennies before summing. The displayed breakdown reconciles to the profit estimate:

```text
Net revenue = VAT-inclusive sale price − output VAT (if VAT registered)
Total costs = purchase + allocated supplier delivery + referral fee + FBA fee
              + packaging + returns reserve + unrecoverable Amazon-fee VAT
Profit      = net revenue − total costs
ROI (%)     = profit / all modelled costs × 100
Margin (%)  = profit / net revenue × 100
```

ROI deliberately uses **all modelled costs**, including selling fees and the returns reserve. A zero denominator displays an unavailable ratio rather than a misleading `0%`. Negative profit remains visible.

- Amazon sale prices are VAT-inclusive. Referral and FBA fees are entered VAT-exclusive; the referral percentage applies to the full sale price. Fees and sale prices in the bundled catalogue are simulated.
- `supplier_vat_included` determines whether purchase and allocated delivery quotes already include their input VAT. Packaging is entered VAT-inclusive. Set applicable VAT rates to zero for inputs with no VAT charged.
- Defaults use 20% VAT, £0.50 packaging and a returns reserve of 3% of net revenue. All rates, packaging, registration and input-recovery assumptions can be changed in the dashboard or CLI.
- A nonregistered business retains input VAT as a cost and does not subtract output VAT. A registered business subtracts output VAT; input VAT is removed from costs only when input recovery is enabled and eligible valid VAT invoices exist. Registration alone does not establish eligibility. Disable recovery when it is unavailable.
- In the dashboard, explicitly select **Reclaim eligible input VAT** to enable recovery. In the CLI, pass `--vat-registered --reclaim-vat`; recovery is off by default.
- Output VAT and recoverable input VAT are informational reconciliation fields: do not add them a second time to the displayed cost total. The returns allowance is a reserve, not a detailed refund model.

For an illustrative nonregistered sale of £30, a £10 VAT-inclusive pack, £1 allocated VAT-inclusive delivery, 15% referral, £3 VAT-exclusive FBA, £0.50 packaging and 3% returns: costs total £21.40, estimated profit is £8.60, ROI is 40.19% and margin is 28.67%. This hand-calculated example is covered by tests.

This models standard UK VAT accounting. Flat Rate Scheme, margin schemes, import duties, cross-border VAT and profit/income/corporation taxes are not calculated. Storage, inbound placement/shipping to Amazon, advertising, subscriptions and other overheads are also not included. An FBA estimate is not a guaranteed fee or profit; add unmodelled costs to your assessment before acting. VAT rates are scan-wide; use separate scans or an extended model when different products require different tax treatment.

```bash
# macOS / Linux example; use .\.venv\Scripts\python.exe on Windows
.venv/bin/python -m jarvis_scout scan --min-profit 5 --min-roi 40 --packaging 0.75 --returns-rate 0.05
.venv/bin/python -m jarvis_scout scan --vat-registered --no-reclaim-vat
.venv/bin/python -m jarvis_scout scan --vat-registered --reclaim-vat
.venv/bin/python -m jarvis_scout scan --help
```

## Telegram notifications on iPhone, Mac and Windows

Telegram is the implemented notification channel. Install Telegram on your iPhone and, optionally, your Mac or Windows computer, sign into the same account, and enable Telegram notifications in the device's settings. The scanner sends to your bot conversation; your phone does not need to run Python. Native iMessage/SMS integration is not implemented.

1. In Telegram, use the official [@BotFather](https://t.me/BotFather) to create a bot. Keep its token private.
2. Open your new bot's conversation and send `/start`.
3. Copy `.env.example` to `.env` and privately enter the bot token. Obtain your conversation's numeric chat ID with Telegram's official `getUpdates` API locally, then set `TELEGRAM_CHAT_ID`. Do not paste token-containing URLs into chat, shared terminals or browser address bars.

```powershell
# Windows; then edit .env locally
Copy-Item .env.example .env
```

```bash
# macOS / Linux; then edit .env locally
cp .env.example .env
```

To look up the chat ID without printing the token, save the following as `var/get_chat_id.py` (create the ignored `var` directory if needed). Run it from the checkout with `.\.venv\Scripts\python.exe var/get_chat_id.py` on Windows or `.venv/bin/python var/get_chat_id.py` on macOS:

```python
import os
import httpx
from dotenv import load_dotenv

load_dotenv(".env", override=False)
token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN privately in .env first.")
try:
    with httpx.Client(timeout=10) as client:
        response = client.get(f"https://api.telegram.org/bot{token}/getUpdates")
    response.raise_for_status()
    result = response.json()
except (httpx.HTTPError, ValueError):
    raise SystemExit("Lookup failed. Check your bot token and connection locally.") from None
if not isinstance(result, dict) or result.get("ok") is not True:
    raise SystemExit("Telegram rejected the lookup.")
chat_ids = {
    item["message"]["chat"]["id"]
    for item in result.get("result", [])
    if isinstance(item, dict) and "message" in item
}
print("Chat IDs:", ", ".join(map(str, sorted(chat_ids))) or "None: send /start to your bot and retry.")
```

Do not commit `.env`, tokens or supplier credentials. The app reads environment variables and a local `.env`; existing environment variables take precedence. Credentials are never stored in scan records. Revoke and replace any token you accidentally disclose.

Sending requires an explicit CLI option. Since every current scan is simulated, testing also requires the simulated-alert opt-in:

```powershell
.\.venv\Scripts\python.exe -m jarvis_scout scan --alerts --allow-simulated-alerts
.\.venv\Scripts\python.exe -m jarvis_scout watch --interval 300 --alerts --allow-simulated-alerts
```

```bash
.venv/bin/python -m jarvis_scout scan --alerts --allow-simulated-alerts
.venv/bin/python -m jarvis_scout watch --interval 300 --alerts --allow-simulated-alerts
```

These send **SIMULATED DEMO** messages for qualifying examples, never genuine market recommendations. `demo`, ordinary `scan` and dashboard scans send no messages. A scan with `--alerts` alone suppresses simulated results. No real Telegram messages are sent by the automated tests.

## Scheduler and configuration

`watch` runs immediately, then repeats scans sequentially. Slow scans never overlap or build up a backlog. `Ctrl+C` stops the scheduler. Without `--alerts`, it only saves scan results:

```bash
.venv/bin/python -m jarvis_scout watch --interval 300
.venv/bin/python -m jarvis_scout history
```

Use the Windows interpreter path on Windows. Run one scheduler per database. It repeats the same local mock catalogue and stored supplier products; it does not refresh feeds, obtain live prices or create a background operating-system service. The scanner's computer must remain running and awake; closing the process or putting a laptop to sleep stops scans. An always-on host can be added later for uninterrupted notifications.

Import updated supplier feeds separately as quotes change. Products older than the configured freshness window are excluded; repeated scheduled scans do not renew their supplier observation time. Scheduler failures print a sanitised error type immediately so failed scans are visible.

| Environment variable | Default / purpose |
| --- | --- |
| `SCOUT_DB_PATH` | `<checkout>/var/scout.sqlite3`; SQLite products, scans and alert cooldowns. |
| `SCOUT_FEED_PATH` | `<checkout>/data/supplier_demo.csv`; used by `demo`. |
| `SCOUT_AMAZON_CATALOG` | `<checkout>/data/amazon_demo.json`; still a simulated catalogue. |
| `SCOUT_SCAN_INTERVAL_SECONDS` | `300`; overridden by `watch --interval`. |
| `SCOUT_ALERT_COOLDOWN_HOURS` | `24`; product/ASIN notification cooldown. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional; both required to send alerts. |

`--db` can override the database before the subcommand, for example `python -m jarvis_scout --db var/test.sqlite3 demo`. Custom relative paths resolve from the command's working directory.

SQLite persists alert reservations and cooldowns across restarts. Successful, pending and uncertain-delivery attempts suppress repeats within the cooldown even if prices change. Definitive failures can be retried. Network ambiguity can hold an undelivered alert until the cooldown expires; Telegram does not provide exactly-once delivery. An interrupted scan does not save partial results as a completed snapshot.

## Move from Windows to macOS

Copy the project source and your database backup to the Mac, transfer credentials privately, and create a **new** macOS virtual environment using the steps above. Do not copy Windows's `.venv`: virtual environments are specific to the operating system and interpreter. Update any Windows-only absolute paths in your `.env`.

Stop the app and scheduler before moving database files, or use SQLite's backup API for a consistent backup while the app is running. With SQLite WAL enabled, copying only an active `.sqlite3` file can omit recent transactions. This example creates a consistent portable backup without printing secrets:

```python
import sqlite3
from pathlib import Path
from jarvis_scout.config import AppConfig

source_path = AppConfig.from_env().db_path
backup_path = Path("var/scout-backup.sqlite3")
backup_path.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as backup:
    source.backup(backup)
```

Move that backup to the Mac, set `SCOUT_DB_PATH` to it (or place it at `var/scout.sqlite3`), then launch the dashboard. It preserves imported products, scan history, review notes and alert cooldowns.

## Tests and project layout

```powershell
.\.venv\Scripts\python.exe -m pytest
```

```bash
.venv/bin/python -m pytest
```

Tests cover financial calculations, conservative matching, imports/database snapshots, filters, Telegram transport/deduplication with mocked HTTP, scheduler behaviour and dashboard workflows. They use temporary databases and do not need credentials.

| Location | Responsibility |
| --- | --- |
| `app.py` | Streamlit dashboard, product inspection, exports and review notes. |
| `jarvis_scout/models.py`, `database.py` | Typed records and SQLite persistence. |
| `jarvis_scout/connectors/` | Separate CSV, supplier API and Amazon adapter boundaries. |
| `jarvis_scout/matching.py`, `profitability.py`, `filters.py` | Identity, cost estimates, qualification and ranking. |
| `jarvis_scout/pipeline.py`, `__main__.py` | Import/scan orchestration and CLI. |
| `jarvis_scout/notifications.py`, `scheduler.py` | Opt-in Telegram alerts and repeated scans. |
| `data/`, `tests/` | Fictional demo fixtures and regression checks. |

## Connect real sources next

The interfaces are ready for additional connectors; the live adapters deliberately raise `NotImplementedError` until implemented. An environment variable or token alone does not enable live discovery, and the CLI/dashboard currently select `MockAmazonAdapter` explicitly.

1. Connect authorised UK retailer/wholesaler feeds or documented supplier APIs with accurate pack pricing, allocated delivery, current availability and source timestamps. Supply credentials through environment variables only when a particular API requires them.
2. Implement the official Amazon Selling Partner API adapter with the required application permissions, seller authorisation and Amazon UK marketplace configuration. Connect Catalog Items for identity, Product Pricing for current offers, Product Fees for actual estimates and Listings Restrictions for seller-specific eligibility. Store authentication securely and respect documented quotas.
3. Add a permitted historical data source for demand evidence and pricing stability. Catalogue and offer APIs alone do not establish sales volume. Record provenance, source timestamps and confidence before enabling live alerts.

The application never purchases products or creates Amazon listings. Connectors must use permitted data access; there is no scraping fallback, anti-bot bypass or restriction-evasion workflow.
