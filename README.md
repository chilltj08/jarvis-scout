# JARVIS SCOUT

A local research workspace for UK Amazon resale opportunities. Import supplier quotes, record your own Amazon listing observations, compare the exact product and pack, estimate profit, and review the results in a Streamlit dashboard. SQLite keeps products, manual comparisons, scan history and Telegram alert cooldowns on your computer.

**You can research real supplier products without an Amazon Seller Central account.** Amazon prices are entered manually with their observation date and evidence source. They are user observations, not verified live market data. No live supplier or Amazon price collection is included. Seller eligibility, demand and future sale prices remain unverified unless supported by your own investigation.

The separate demonstration catalogue is fictional and labelled **SIMULATED DEMO**. Real supplier products never obtain Amazon resale prices from that catalogue. Missing prices or fees remain missing, and incomplete estimates cannot qualify for alerts.

## Start or upgrade on Windows

Install [Python 3.12](https://www.python.org/downloads/), then open PowerShell in the `jarvis-scout` folder. For a new checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

For an existing checkout, stop the dashboard and scanner first:

```powershell
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m jarvis_scout --help
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Using the interpreter directly avoids virtual-environment activation and PowerShell execution-policy changes. Open the local URL printed by Streamlit, usually `http://localhost:8501`. Stop it with `Ctrl+C`. Keep your private `.env` and database in place; the schema migration preserves existing products, scan history, review notes and alert state. Existing historical mock results remain simulated.

`requirements.txt` pins direct application and test dependencies. `requirements.lock` also pins transitive dependencies from the tested Linux Python 3.12 environment; compatible platform-specific dependencies may differ. The automated suite runs in Linux; Windows and macOS instructions have not been exercised on those machines.

## Research a real product in the dashboard

1. Open **Suppliers**, download the empty CSV template, and fill it with supplier data you have permission to use. Upload the file as real supplier data. The importer reports rejected rows and saves valid rows.
2. Check the supplier inventory for source, price, pack size, minimum order, stock, actual observation time, last database update and freshness. Real inventory and demo inventory are displayed separately.
3. Open **Amazon comparisons**, select a real supplier product, and enter its Amazon ASIN, observed selling price, dated evidence, listing brand, EAN/GTIN and pack size. Record model/variation details when available. Confirm the identifier only after checking reliable evidence that it belongs to that Amazon product.
4. Enter the estimated referral percentage and fulfilment fee. Blank means unknown; enter an explicit zero only when supported. Confirm that you checked the fees. Saving a comparison does not fetch an Amazon page, approve identity or verify seller eligibility.
5. Select **Run research scan** in the sidebar. Review the full cost breakdown, incomplete-estimate reasons, identity review queue and market/eligibility warnings. Use supplier and Amazon links to continue your investigation.
6. Revisit **Scan history** or export results. Import updated supplier quotes and update manual observations when they change; running a scan does not refresh their timestamps.

Default research filters assess financial candidates even when demand, pricing stability, competition or account eligibility are unknown; these gaps are shown as warnings. Enable the stricter market-evidence and eligibility options to require that evidence. A financial candidate is a research lead, not confirmation that you may sell it or that buyers will purchase at the observed price.

Review notes document investigation and cannot approve an uncertain match. Scan snapshots preserve their original prices, evidence, filters and costs. Dashboard scans do not send Telegram messages. The dashboard is a local, single-user app; add authentication before making it publicly accessible.

## Supplier CSV format

Download [`data/supplier_template.csv`](data/supplier_template.csv) from the repository or dashboard. It contains only headers so example values cannot accidentally become real quotes. Use UTF-8 (a BOM is supported), comma-separated fields, unique headers, and the following columns. Imports are limited to 10 MiB, 50,000 rows and 2,000 characters per field:

| Column | Meaning |
| --- | --- |
| `supplier_name` | Supplier/source name, required. `--source` can provide an override. |
| `product_title` | Product description, required. |
| `brand` | Brand text; blank prevents a verified match. |
| `ean` | GTIN-8/12/13/14 as text. Leading zeroes are preserved. Blank requires manual review; malformed or checksum-invalid identifiers are rejected. |
| `sku` | Supplier-local product identifier, required and preserved as text. |
| `unit_cost` | Cost of **one complete sellable pack**, required, numeric and nonnegative. Do not include a currency symbol. |
| `currency` | `GBP`; currency conversion is not implemented. |
| `vat_treatment` | `included`, `excluded`, `not_applicable` or `unknown`. Blank/unknown prevents a complete estimate. Applies to supplier product cost and supplier shipping. |
| `minimum_order_quantity` | Positive integer number of sellable packs you must buy. |
| `pack_quantity` | Positive integer number of individual items inside each sellable pack. |
| `shipping_cost` | Total supplier shipping charge for the minimum order, in the same VAT treatment as the product quote. Blank means unknown; use explicit `0` for confirmed free shipping. |
| `stock_status` | `in_stock`, `out_of_stock`, `preorder` or `unknown`. Out-of-stock and preorder products cannot qualify. |
| `product_url` | Supplier HTTP(S) product link without embedded credentials, required. |
| `observed_at` | Actual source observation timestamp with timezone, required, for example `2026-10-11T09:00:00Z`. Future timestamps are rejected. |
| `model`, `variation` | Optional matching detail; include colour, size, flavour or other distinguishing variation. Conflicting supplied details require review. |
| `available_quantity` | Optional nonnegative count of available sellable packs; `0` prevents qualification. |

For a two-item pack costing £9, use `pack_quantity=2,unit_cost=9.00`. If its minimum order is 24 packs and that order has £12 shipping, use `minimum_order_quantity=24,shipping_cost=12.00`. The app allocates £0.50 supplier shipping to each pack and displays the minimum-order purchase commitment separately. It does not split packs or combine several products into an order. Delivery allocation can change at other order quantities; enter the actual minimum-order quote you are evaluating.

Do not use Excel's scientific notation for EANs or let it strip leading zeroes: format the column as text. An EAN checksum validates the number's structure, not authenticity or GS1 ownership. Verify the identifier against the actual product and listing before confirming a comparison.

```powershell
.\.venv\Scripts\python.exe -m jarvis_scout import-csv .\feeds\supplier.csv
.\.venv\Scripts\python.exe -m jarvis_scout scan
.\.venv\Scripts\python.exe -m jarvis_scout history
```

Valid rows are saved even if other rows fail; row-specific errors are reported and CLI exit code `2` signals an incomplete import. Re-importing the same real `(supplier_name, sku)` updates a single product when its observation is at least as recent; unchanged data and older observations do not replace a newer quote. Duplicate source/SKU rows within a file are rejected after the first valid occurrence. Missing rows do not delete existing products: explicitly mark products out of stock or keep observations current.

Legacy `source`, `title`, `delivery_cost`, `supplier_url` and `supplier_vat_included` fields remain supported for the fictional demo feed. In that legacy format, `delivery_cost` is already allocated per pack. Use the new template for real feeds. Importing a file does not infer demo status from its title: choose demo explicitly when importing fictional data.

## Manual Amazon evidence and matching

A manual comparison stores a 10-character ASIN, title, brand, EAN/GTIN, pack quantity, optional model and variation, selling price, observed-at timestamp (dashboard date/time use Europe/London), evidence source, estimated fees, and your verification confirmations. A URL, dated screenshot reference or documented research note can be evidence; do not put tokens or credentials in links. The app does not scrape evidence links. Observed selling prices are VAT-inclusive GBP; referral fee rates are fractions internally and displayed as percentages, and fulfilment fees are entered VAT-exclusive.

An exact real match requires one unambiguous, explicitly verified, checksum-valid identifier shared by both records, compatible brand and identical pack quantity. Supplied model or variation conflicts require review. A title or ASIN alone cannot establish product identity, and two possible matching ASINs require review. Missing identifiers or unconfirmed identity keep the estimate in review even if a price has been entered. The calculator never assumes that a wholesale case matches an Amazon single item.

Saving the same product/ASIN replaces its comparison instead of duplicating it. Changing an ASIN creates another observation; select the obsolete saved observation and use **Remove selected observation** to resolve an incorrect or ambiguous comparison. Removal affects current comparison evidence only. A scan records the evidence used at that moment; later edits or removals cannot rewrite prior scan estimates. Amazon comparisons can be recorded without Seller Central, but seller-account restrictions cannot be verified through this workflow. Official API adapters remain integration boundaries for future permitted access.

## Profit calculations and filters

Money uses `Decimal` and each cost/tax category is rounded to pennies before summing:

```text
Net revenue = VAT-inclusive selling price − output VAT (if VAT registered)
Total costs = purchase + allocated supplier delivery + referral fee + fulfilment fee
              + packaging + inbound shipping + other costs + returns reserve
              + unrecoverable Amazon-fee VAT
Profit      = net revenue − total costs
ROI (%)     = profit / all modelled costs × 100
Margin (%)  = profit / net revenue × 100
```

ROI includes every modelled cost, including selling fees and returns. Zero denominators display an unavailable ratio; negative profit stays visible. Minimum-order cost is a separate purchase commitment, not a second cost added to one sale. Default financial thresholds are £3 profit, 30% ROI and observations no older than 48 hours. Out-of-stock products, stale observations, incomplete costs and uncertain matches cannot qualify.

- Supplier VAT treatment governs both purchase and supplier delivery. `not_applicable` means no VAT charged on those inputs. Unknown treatment or shipping produces an incomplete estimate, not an assumed zero or default tax treatment.
- Amazon selling prices include sales VAT. Referral and fulfilment fees are estimated VAT-exclusive. The referral rate applies to the full selling price. Missing or unconfirmed fees prevent qualification.
- Defaults use 20% VAT, £0.50 packaging, a returns reserve of 3% of net revenue, and £0 inbound/other costs. Set inbound transport or postage not already in fulfilment, storage, preparation and other per-pack expenses explicitly. Avoid counting one expense twice.
- Packaging, inbound shipping and other costs are VAT-inclusive inputs. Their VAT rates can be configured. Applicable rates and registration assumptions are scan-wide; use separate scans when different products require different assumptions.
- A nonregistered business retains input VAT as a cost and subtracts no output VAT. A registered business subtracts output VAT. Input recovery additionally needs explicit opt-in, valid invoices and eligibility; registration alone does not establish recovery rights.
- Dashboard input recovery starts off. In the CLI use `--vat-registered --reclaim-vat` only when eligible. Output VAT and recoverable input VAT are informational reconciliation fields and must not be added again to the displayed total.

```powershell
.\.venv\Scripts\python.exe -m jarvis_scout scan --min-profit 5 --min-roi 40 --packaging 0.75 --returns-rate 0.05 --inbound-shipping 1.00 --other-costs 0.25
.\.venv\Scripts\python.exe -m jarvis_scout scan --vat-registered --reclaim-vat
.\.venv\Scripts\python.exe -m jarvis_scout scan --require-market-signals --require-eligible
.\.venv\Scripts\python.exe -m jarvis_scout scan --help
```

The ranking score combines ROI, profit and available demand, stability and competition inputs. Unknown market inputs stay unknown. Manual evidence currently provides no live sales-volume, competition or historical-price feed; stricter evidence filters will therefore exclude those candidates. Demo scores are synthetic and not forecasts.

This models standard UK VAT accounting, not Flat Rate Scheme, margin schemes, import duties, cross-border VAT or business income taxes. Returns are an allowance, not a detailed refund model. Estimates rely on the costs and fees you enter and cannot guarantee profit or eligibility.

## Demo mode

Demo inventory has a separate identity from real inventory, even when supplier name and SKU coincide. Select demo data in the dashboard or run:

```powershell
.\.venv\Scripts\python.exe -m jarvis_scout demo
.\.venv\Scripts\python.exe -m jarvis_scout scan --scope demo
```

`scan` and `watch` default to real supplier products. `--scope demo` explicitly selects fictional products; `--scope all` includes both while preserving each result's provenance. To import an extra fictional feed, use `import-csv <path> --demo`. Demo prices cannot be assigned to real records, and historical mixed real-supplier/mock-Amazon results cannot trigger real alerts.

The original `calculator.calculate` and `pipeline.discover` APIs remain available for the earlier fictional CSV demo. They are separate from the current scanner and cannot produce alerts.

## Telegram notifications on iPhone, Mac and Windows

Install Telegram on your iPhone and optionally Mac/Windows, sign into the same account, and allow notifications. Your phone does not need to run Python. Native iMessage/SMS integration is not implemented.

1. Create a bot with Telegram's official [@BotFather](https://t.me/BotFather) and keep its token private.
2. Open the bot conversation and send `/start`.
3. Copy `.env.example` to `.env`, then edit the local file privately with your bot token and numeric chat ID. Obtain the chat ID using the official Telegram `getUpdates` API locally; do not share token-bearing URLs.

```powershell
Copy-Item .env.example .env
```

After setting the token privately and sending `/start`, save this as `var/get_chat_id.py` (create the ignored `var` folder if needed) and run `.\.venv\Scripts\python.exe var/get_chat_id.py`. It prints chat IDs without printing your bot token:

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
    raise SystemExit("Lookup failed; check token and connection locally.") from None
if not isinstance(result, dict) or result.get("ok") is not True:
    raise SystemExit("Telegram rejected the lookup.")
chat_ids = {
    item["message"]["chat"]["id"]
    for item in result.get("result", [])
    if isinstance(item, dict) and "message" in item
}
print("Chat IDs:", ", ".join(map(str, sorted(chat_ids))) or "None: send /start and retry.")
```

Put your chat ID into `.env`, then explicitly enable alerts:

```powershell
.\.venv\Scripts\python.exe -m jarvis_scout scan --alerts
.\.venv\Scripts\python.exe -m jarvis_scout watch --interval 300 --alerts
```

Alerts describe **REAL SUPPLIER / MANUAL AMAZON RESEARCH** and show evidence/warnings. They require a real supplier record and a nonsimulated, verified comparison with a complete qualifying estimate. Mock or mixed evidence can never trigger real-opportunity alerts. Sending is an explicit CLI action; ordinary scans, demos and dashboard scans send no messages.

A clearly labelled demo notification requires both opt-ins:

```powershell
.\.venv\Scripts\python.exe -m jarvis_scout scan --scope demo --alerts --allow-simulated-alerts
```

This sends only simulated examples. Automated tests use mocked Telegram transport and send no real messages. SQLite preserves reservations and cooldowns across restarts. Successful, pending or uncertain-delivery attempts suppress duplicates inside the cooldown; definitive failures can retry. Telegram cannot guarantee exactly-once delivery, so network ambiguity can suppress an undelivered alert until cooldown expiry.

Never commit `.env`, tokens, supplier credentials or private databases. Existing environment variables take precedence over `.env`. Credentials are not stored in scan snapshots. Revoke any token that is disclosed accidentally.

## Scheduler and configuration

`watch` runs immediately and repeats scans sequentially. Slow scans do not overlap. `Ctrl+C` stops the process. Run one scheduler per database and keep its computer awake:

```powershell
.\.venv\Scripts\python.exe -m jarvis_scout watch --interval 300
.\.venv\Scripts\python.exe -m jarvis_scout history
```

It reads current stored supplier products and saved manual observations on each scan; imports or comparison updates made in the dashboard can be picked up by subsequent scans. It does not download supplier feeds, fetch Amazon prices, renew observation timestamps or install an operating-system service. Stale observations remain stale until supported by updated evidence. An always-on host can be added later for uninterrupted alerts.

| Environment variable | Purpose/default |
| --- | --- |
| `SCOUT_DB_PATH` | `<checkout>/var/scout.sqlite3`; products, comparisons, scans and cooldowns. |
| `SCOUT_FEED_PATH` | `<checkout>/data/supplier_demo.csv`; used only by `demo`. |
| `SCOUT_AMAZON_CATALOG` | `<checkout>/data/amazon_demo.json`; fictional demo catalogue only. |
| `SCOUT_SCAN_INTERVAL_SECONDS` | `300`; overridden by `watch --interval`. |
| `SCOUT_ALERT_COOLDOWN_HOURS` | `24`; notification cooldown. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Both required only when sending alerts. |

Use `--db` before the command to override the database, for example `python -m jarvis_scout --db var/research.sqlite3 scan`. Relative paths resolve from the working directory. An interrupted or failed scan does not save partial results as a complete snapshot.

## Move to macOS

Install Python 3.12 and create a new virtual environment on the Mac:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
.venv/bin/python -m jarvis_scout scan
.venv/bin/python -m jarvis_scout watch --interval 300 --alerts
```

Do not copy the Windows `.venv`; virtual environments depend on their operating system. Transfer `.env` privately and update any Windows absolute paths. Move a consistent database backup to preserve products, manual evidence, scan history and cooldowns. Stop the app and scheduler before copying database files; copying just an active WAL-mode `.sqlite3` can miss recent transactions. For an online backup, run this locally without printing secrets:

```python
import sqlite3
from pathlib import Path
from jarvis_scout.config import AppConfig

backup_path = Path("var/scout-backup.sqlite3")
backup_path.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(AppConfig.from_env().db_path) as source:
    with sqlite3.connect(backup_path) as backup:
        source.backup(backup)
```

Set `SCOUT_DB_PATH` to the transferred backup or place it at `var/scout.sqlite3`.

## Tests and future integrations

```powershell
.\.venv\Scripts\python.exe -m pytest
```

On macOS/Linux use `.venv/bin/python -m pytest`. Tests cover import validation, timestamps and duplicate handling, database migration, conservative identity checks, manual evidence, incomplete estimates, VAT/cost arithmetic, mock/real separation, dashboard flows, CLI/scheduler behaviour, Telegram transport and persistent deduplication. They use temporary databases and need no real credentials.

`jarvis_scout/connectors/` separates CSV, supplier API and Amazon adapter boundaries. `manual.py` provides saved manual comparisons; `matching.py`, `profitability.py` and `filters.py` handle identity, estimates and qualification; `pipeline.py` and `__main__.py` orchestrate scanning; `notifications.py` and `scheduler.py` retain optional alerts and scheduled scans.

No Amazon Seller Central credentials are needed for this manual workflow. Future automated research needs permitted supplier APIs/feeds and a licensed or otherwise authorised market-data source. Official Selling Partner API integrations generally require seller authorisation and appropriate application permissions; setting a token alone does not enable them. Live adapter stubs are deliberately unimplemented. Historical price/demand evidence and seller-specific restrictions remain future integrations.

JARVIS SCOUT never purchases products or creates Amazon listings. It does not scrape websites, bypass restrictions or evade anti-bot controls.
