# JARVIS Scout

A **research-only** starter app for identifying *potential* resale opportunities on Amazon UK.

**Status:** This is a functioning offline demonstration. Supplier and Amazon catalogue prices are synthetic sample data, **not live market results**. The app does not make purchases, post listings, access Amazon accounts, or establish that any item is permitted to sell.

## Quick start

Requires Python 3.11 or newer.

```bash
git clone https://github.com/chilltj08/jarvis-scout.git
cd jarvis-scout
python -m venv .venv
```

Activate the virtual environment:

- **Windows PowerShell:** `.venv\Scripts\Activate.ps1`
- **macOS/Linux:** `source .venv/bin/activate`

Then run:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q
python -m jarvis_scout.pipeline
python -m streamlit run app.py
```

Your browser should open the Streamlit demo dashboard.

## What it does

1. Loads local CSV supplier offers and simulated Amazon prices.
2. Matches products **strictly** by EAN, normalised brand and pack quantity, avoiding fuzzy guesses.
3. Estimates profit after purchase price, referral fee, fulfilment, inbound shipping and miscellaneous allowances.
4. Filters and sorts opportunities by estimated net profit and return on inventory cost.
5. Displays demo results in a Streamlit dashboard.

CSV files in `data/` use **made-up products, identifiers and prices** for testing.

## Next integrations

- Replace the sample supplier CSV with a licensed or authorised supplier feed.
- Use a permitted Amazon data provider / Amazon Selling Partner API where you qualify for access.
- Add listing eligibility, pack size verification and supplier invoice checks.
- Implement historical pricing, competitive offer analysis, costs/taxes tailored to the seller.
- Add Telegram alerts (credentials must remain outside Git).

**Do not base purchase decisions on simulated prices or unverified selling permissions.** Fees and VAT treatment vary by seller and product. No estimates here represent guaranteed profit.

## GitHub and credentials

This repository may be public. Never commit tokens, API credentials, supplier account details, `.env` files, or private invoices. `.gitignore` excludes typical secret and local files.

## Project layout

```text
jarvis_scout/          Python calculations, CSV loading and matching
data/                  Simulated supplier/Amazon CSV input files
tests/                 Automated tests
app.py                 Streamlit dashboard
requirements.txt       Python dependencies
.github/workflows/     CI test workflow
```
