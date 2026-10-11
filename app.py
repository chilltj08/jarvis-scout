"""JARVIS SCOUT's local dashboard. Scans never purchase, list or send alerts."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import streamlit as st

from jarvis_scout.config import AppConfig
from jarvis_scout.connectors.amazon import MockAmazonAdapter
from jarvis_scout.database import Database
from jarvis_scout.filters import OpportunityFilters
from jarvis_scout.pipeline import import_feed, run_scan
from jarvis_scout.profitability import ProfitAssumptions


st.set_page_config(
    page_title="JARVIS SCOUT · UK resale research",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Constant styling only: supplier content is always rendered by Streamlit, never HTML.
st.markdown(
    """
    <style>
      .stApp { background: #f5f7fb; }
      [data-testid="stSidebar"] { background: #fff; border-right: 1px solid #e4e9f0; }
      .block-container { padding-top: 2.5rem; padding-bottom: 3rem; max-width: 1450px; }
      h1, h2, h3 { color: #14243b; letter-spacing: -.025em; }
      [data-testid="stMetric"] { background: #fff; border: 1px solid #e1e7ef;
        border-radius: 12px; padding: 18px 20px; }
      [data-testid="stMetricValue"] { color: #14243b; }
      [data-testid="stTabs"] button { font-weight: 600; }
      div[data-testid="stExpander"] { background: #fff; border-radius: 10px; }
      .scout-eyebrow { color: #516a86; font-size: .76rem; font-weight: 700;
        letter-spacing: .16em; margin-bottom: .5rem; }
      .scout-title { font-size: 2.65rem; font-weight: 750; color: #14243b;
        line-height: 1.1; margin-bottom: .55rem; letter-spacing: -.045em; }
      .scout-subtitle { color: #516176; font-size: 1rem; margin-bottom: 1.3rem; }
      .scout-badge { display: inline-block; color: #855511; background: #fff1d4;
        border: 1px solid #ebd29b; border-radius: 30px; font-size: .72rem;
        font-weight: 700; padding: 5px 12px; letter-spacing: .065em; }
      @media (max-width: 700px) { .scout-title { font-size: 2rem; }
        .block-container { padding-top: 1.6rem; } }
    </style>
    """,
    unsafe_allow_html=True,
)


def money(value: object) -> str:
    """Format persisted Decimal strings without introducing float arithmetic."""
    try:
        return f"£{Decimal(str(value)):,.2f}" if value is not None else "—"
    except (InvalidOperation, ValueError, TypeError):
        return "—"


def percentage(value: object) -> str:
    try:
        return f"{Decimal(str(value)):.1f}%" if value is not None else "—"
    except (InvalidOperation, ValueError, TypeError):
        return "—"


def number(value: object) -> float | None:
    try:
        result = float(value)  # Display and CSV export only; the calculator uses Decimal.
        return result if pd.notna(result) else None
    except (ValueError, TypeError):
        return None


def safe_link(value: object) -> str | None:
    """Only expose ordinary web links; CSV feeds are untrusted input."""
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlparse(value)
        if (parsed.scheme in {"http", "https"} and parsed.hostname
                and parsed.username is None and parsed.password is None):
            parsed.port  # Validate malformed port text before rendering a link.
            return value
    except ValueError:
        pass
    return None


def local_time(value: object) -> str:
    if not value:
        return "—"
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return str(value)
        try:
            timestamp = timestamp.astimezone(ZoneInfo("Europe/London"))
        except ZoneInfoNotFoundError:
            pass
        return timestamp.strftime("%d %b %Y, %H:%M %Z")
    except ValueError:
        return str(value)


def table_rows(records: list[dict]) -> pd.DataFrame:
    """Flatten explicitly selected fields so exports cannot include credentials."""
    rows = []
    for record in records:
        product = record.get("supplier") or {}
        listing = record.get("listing") or {}
        profit = record.get("profitability") or {}
        rows.append(
            {
                "Record": record.get("id"),
                "Product": product.get("title", "Untitled product"),
                "Supplier": product.get("source", ""),
                "Pack": product.get("pack_quantity"),
                "Buy price (£)": number(product.get("unit_cost")),
                "Simulated sale (£)": number(listing.get("price")),
                "Profit (£)": number(profit.get("profit")),
                "ROI (%)": number(profit.get("roi_percent")),
                "Margin (%)": number(profit.get("margin_percent")),
                "Score": round(number(record.get("score")) or 0, 1),
                "Match": (record.get("match") or {}).get("status", "unmatched"),
                "Qualified": bool(record.get("qualified")),
                "Data": "SIMULATED" if record.get("simulated", True) else "External source",
                "Supplier link": safe_link(product.get("supplier_url")),
            }
        )
    return pd.DataFrame(rows)


def display_table(records: list[dict]) -> None:
    frame = table_rows(records)
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Record": None,
            "Supplier link": st.column_config.LinkColumn("Supplier", display_text="Open supplier ↗"),
            "Buy price (£)": st.column_config.NumberColumn(format="£%.2f"),
            "Simulated sale (£)": st.column_config.NumberColumn(format="£%.2f"),
            "Profit (£)": st.column_config.NumberColumn(format="£%.2f"),
            "ROI (%)": st.column_config.NumberColumn(format="%.1f%%"),
            "Margin (%)": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


def export_csv(records: list[dict]) -> bytes:
    frame = table_rows(records)
    # An authorised feed can still contain spreadsheet formula-like text.
    # Prefix text cells on export; numeric cells remain usable for analysis.
    def safe_cell(value: object) -> object:
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    return frame.map(safe_cell).to_csv(index=False).encode("utf-8")


def show_details(record: dict, *, review: bool = False) -> None:
    product = record.get("supplier") or {}
    listing = record.get("listing") or {}
    match = record.get("match") or {}
    profit = record.get("profitability") or {}
    st.subheader(product.get("title", "Product details"))
    st.caption(
        f"{product.get('source', 'Unknown supplier')} · SKU {product.get('sku', '—')} · "
        f"Match: {match.get('status', 'unmatched')} · "
        f"Confidence {percentage(100 * float(match.get('confidence', 0)))}"
    )
    supplier_link = safe_link(product.get("supplier_url"))
    if supplier_link:
        st.link_button("Open supplier product ↗", supplier_link)
    st.info("Amazon prices, fees, demand and competition shown here are simulated for development.")

    identity_rows = []
    for label, key in [("Brand", "brand"), ("Model", "model"), ("EAN / GTIN", "ean"),
                       ("Variation", "variation"), ("Pack quantity", "pack_quantity")]:
        identity_rows.append(
            {"Attribute": label, "Supplier product": str(product.get(key) or "—"),
             "Simulated Amazon listing": str(listing.get(key) or "—")}
        )
    st.dataframe(pd.DataFrame(identity_rows), hide_index=True, width="stretch")
    if listing:
        st.caption(f"Simulated listing identifier: {listing.get('asin', '—')}")
    for reason in match.get("reasons", []):
        st.write(f"• {reason}")

    if profit:
        cells = st.columns(4)
        for cell, label, value in zip(
            cells,
            ["Estimated profit / pack", "ROI on total cost", "Profit margin", "Total cost / pack"],
            [money(profit.get("profit")), percentage(profit.get("roi_percent")),
             percentage(profit.get("margin_percent")), money(profit.get("total_cost"))],
        ):
            cell.metric(label, value)
        with st.expander("Cost breakdown and VAT treatment", expanded=review):
            labels = [("Simulated sale price (including sales VAT)", "sale_price"),
                      ("Output VAT on sale", "output_vat"), ("Net sale revenue", "net_revenue"),
                      ("Purchase cost", "purchase_cost"), ("Allocated supplier delivery", "supplier_delivery"),
                      ("Amazon referral fee", "referral_fee"), ("Amazon FBA fee", "fba_fee"),
                      ("Packaging", "packaging"), ("Returns allowance", "returns_allowance"),
                      ("Unrecoverable VAT on Amazon fees", "vat_cost"), ("Total economic cost", "total_cost"),
                      ("Estimated profit", "profit")]
            st.dataframe(
                pd.DataFrame([{"Component": label, "Amount": money(profit.get(key))}
                              for label, key in labels]),
                hide_index=True, width="stretch",
            )
            st.caption(
                "All costs are per matched pack. Delivery must be allocated per purchased pack in the feed. "
                "Purchase, delivery and packaging costs reflect the saved VAT assumptions. "
                "For VAT registrants, eligible input VAT reduces costs and output VAT reduces revenue. "
                "Amazon fees are entered excluding VAT. ROI = profit ÷ total economic cost; "
                "margin = profit ÷ net sale revenue. Estimates are not tax advice."
            )
            if profit.get("recoverable_input_vat") is not None:
                st.caption(f"Assumed recoverable input VAT: {money(profit['recoverable_input_vat'])}")
        with st.expander("Demand, pricing and eligibility"):
            st.write(f"Demand confidence: {percentage(100 * float(listing.get('demand_confidence', 0)))}")
            st.write(f"Pricing stability: {percentage(100 * float(listing.get('pricing_stability', 0)))}")
            st.write(f"Competing offers: {listing.get('competition', '—')}")
            st.write(f"Listing restriction status: {listing.get('restriction_status', 'unknown')}")
            st.write(f"Supplier observation: {local_time(product.get('observed_at'))}")
            st.write(f"Simulated Amazon observation: {local_time(listing.get('observed_at'))}")
    if record.get("filter_reasons"):
        st.warning("Excluded by the saved scan filters: " + "; ".join(record["filter_reasons"]))
    if review:
        st.caption("Review notes document your checks. A note does not approve a match or enable alerts.")
        note = st.text_area("Review note", value=record.get("review_note") or "", max_chars=4000,
                            key=f"note_{record['id']}")
        if st.button("Save review note", key=f"save_note_{record['id']}"):
            try:
                db.set_review_note(record["id"], note)
                st.success("Review note saved.")
            except Exception as error:
                st.error(f"Could not save the review note: {error}")


def show_import_result(report: object) -> None:
    st.success(f"Imported {report.imported} supplier products.")
    if report.errors:
        st.warning(f"{len(report.errors)} feed issue(s). Correct these rows and import again.")
        for error in report.errors[:8]:
            st.write(error)
        if len(report.errors) > 8:
            st.caption("Showing the first 8 feed issues.")


cfg = AppConfig.from_env()
try:
    db = Database(cfg.db_path)
except Exception as error:
    st.error(f"Could not open the local database: {error}")
    st.stop()

with st.sidebar:
    st.markdown("### SCOUT control room")
    st.caption("CSV suppliers · simulated Amazon UK")
    with st.expander("1 · Import supplier products", expanded=True):
        st.caption("Use authorised retailer or wholesaler feeds. Re-imports update the supplier SKU.")
        if st.button("Import demonstration feed", width="stretch"):
            try:
                show_import_result(import_feed(cfg.feed_path, db))
            except Exception as error:
                st.error(f"Could not import the demonstration feed: {error}")
        upload = st.file_uploader("Retailer / wholesaler CSV", type=["csv"])
        if st.button("Import uploaded CSV", disabled=upload is None, width="stretch"):
            try:
                text_feed = StringIO(upload.getvalue().decode("utf-8-sig"))
                show_import_result(import_feed(text_feed, db))
            except Exception as error:
                st.error(f"Could not import this CSV: {error}")
    with st.expander("2 · Opportunity thresholds", expanded=False):
        min_profit = st.number_input("Minimum profit per pack (£)", min_value=0.0, value=3.0, step=0.5)
        min_roi = st.number_input("Minimum ROI (%)", min_value=0.0, value=30.0, step=5.0)
        demand = st.slider("Minimum demand confidence", 0, 100, 50, help="A simulated input in this MVP.")
        stability = st.slider("Minimum pricing stability", 0, 100, 50, help="A simulated input in this MVP.")
        competition = st.number_input("Maximum competing offers", min_value=0, value=10, step=1)
        freshness = st.number_input("Maximum data age (hours)", min_value=0.1, value=48.0, step=1.0)
    with st.expander("3 · Cost and VAT assumptions", expanded=False):
        packaging = st.number_input("Packaging per pack, VAT inclusive (£)", min_value=0.0, value=0.5, step=0.1)
        returns = st.number_input("Returns allowance (% of net sale revenue)", min_value=0.0, max_value=100.0,
                                  value=3.0, step=0.5)
        vat_registered = st.checkbox("VAT registered", value=False)
        reclaim = st.checkbox("Reclaim eligible input VAT", value=False, disabled=not vat_registered,
                              help="Enable only if your VAT registration and valid supplier invoices allow recovery.")
        custom_vat = st.checkbox("Set category-specific VAT rates", value=False)
        vat_rates = {key: Decimal("0.20") for key in ["vat_rate", "purchase_vat_rate", "delivery_vat_rate",
                                                     "packaging_vat_rate", "amazon_fees_vat_rate"]}
        if custom_vat:
            for key, label in [("vat_rate", "Sales VAT (%)"), ("purchase_vat_rate", "Purchase VAT (%)"),
                               ("delivery_vat_rate", "Delivery VAT (%)"), ("packaging_vat_rate", "Packaging VAT (%)"),
                               ("amazon_fees_vat_rate", "Amazon fee VAT (%)")]:
                vat_rates[key] = Decimal(str(st.number_input(label, min_value=0.0, max_value=100.0,
                                                           value=20.0, step=1.0))) / Decimal("100")
        st.caption("Standard UK VAT assumptions: 20% sales, purchases, delivery, packaging and Amazon fees. "
                   "Supplier CSV indicates whether quoted purchase and delivery prices include VAT.")
    filters = OpportunityFilters(
        min_profit=Decimal(str(min_profit)), min_roi_percent=Decimal(str(min_roi)),
        min_demand_confidence=demand / 100, min_pricing_stability=stability / 100,
        max_competition=int(competition), max_data_age_hours=freshness,
    )
    assumptions = ProfitAssumptions(
        packaging_cost=Decimal(str(packaging)), returns_rate=Decimal(str(returns)) / Decimal("100"),
        vat_registered=vat_registered, reclaim_input_vat=reclaim, **vat_rates,
    )
    if st.button("Run simulated scan", type="primary", width="stretch"):
        if not db.products():
            st.warning("Import a CSV or the demonstration feed first.")
        else:
            try:
                with st.spinner("Matching supplier packs and calculating estimated profitability…"):
                    report = run_scan(db, MockAmazonAdapter(cfg.amazon_catalog_path), assumptions, filters)
                st.success(f"Scan #{report.scan_id}: {report.qualified} simulated opportunities.")
            except Exception as error:
                st.error(f"Scan could not complete: {error}")
    st.caption("These thresholds apply to your next scan. Saved scan results keep their original assumptions.")
    st.divider()
    st.caption("Telegram: " + ("configured" if cfg.telegram_configured else "not configured"))
    st.caption("Scans in this dashboard do not send notifications. See Settings for the explicit alert command.")

st.markdown('<div class="scout-eyebrow">UK AMAZON RESALE RESEARCH</div>', unsafe_allow_html=True)
st.markdown('<div class="scout-title">JARVIS SCOUT</div>', unsafe_allow_html=True)
st.markdown('<div class="scout-subtitle">Research the product. Check the pack. Understand the margin.</div>',
            unsafe_allow_html=True)
st.markdown('<span class="scout-badge">SIMULATED AMAZON DATA · DEVELOPMENT MODE</span>', unsafe_allow_html=True)
st.caption("Supplier feeds may contain real quotes. Amazon prices, demand, fees and restrictions come from a mock catalogue. "
           "Results are research estimates and require manual checks before any purchase.")

scans = db.list_scans(limit=50)
latest_id = db.latest_scan_id()
selected_id = latest_id
if scans:
    scan_labels = {scan["scan_id"]: f"Scan #{scan['scan_id']} · {local_time(scan.get('created_at'))}" for scan in scans}
    selected_id = st.selectbox("Saved scan", list(scan_labels), format_func=scan_labels.get,
                               index=list(scan_labels).index(latest_id) if latest_id in scan_labels else 0)
records = db.opportunities(scan_id=selected_id) if selected_id is not None else []
qualified_records = [record for record in records if record.get("qualified")]
review_records = [record for record in records if (record.get("match") or {}).get("status") != "exact"]

metrics = st.columns(4)
metrics[0].metric("Supplier products", len(db.products()))
metrics[1].metric("Qualified · simulated", len(qualified_records))
metrics[2].metric("Review / unmatched", len(review_records))
metrics[3].metric("Saved scans", len(scans))
st.write("")

opportunities_tab, review_tab, history_tab, settings_tab = st.tabs(
    ["Opportunities", "Review queue", "Scan history", "Settings & guide"]
)
with opportunities_tab:
    st.subheader("Your research shortlist")
    controls = st.columns([3, 2])
    search = controls[0].text_input("Search products or suppliers", placeholder="Brand, product, supplier…")
    include_excluded = controls[1].checkbox("Include filtered-out exact matches", value=False)
    visible = [record for record in records if (record.get("match") or {}).get("status") == "exact"
               and (include_excluded or record.get("qualified"))]
    if search.strip():
        query = search.strip().casefold()
        visible = [record for record in visible if query in " ".join(
            str((record.get("supplier") or {}).get(key, "")) for key in ["title", "source", "brand", "model"]
        ).casefold()]
    visible.sort(key=lambda record: float(record.get("score") or 0), reverse=True)
    if visible:
        display_table(visible)
        st.download_button("Export these results as CSV", data=export_csv(visible),
                           file_name=f"jarvis-scout-simulated-scan-{selected_id}.csv", mime="text/csv")
        st.divider()
        choice = st.selectbox("Inspect a product", list(range(len(visible))),
                              format_func=lambda i: f"{visible[i]['supplier']['title']} · {visible[i]['supplier']['source']}")
        show_details(visible[choice])
    elif not records:
        st.info("Start with the demonstration feed in the sidebar, then run a simulated scan. "
                "You can also import an authorised supplier CSV.")
    else:
        st.info("No qualified exact matches meet this view. Inspect the review queue or include filtered-out exact matches.")

with review_tab:
    st.subheader("Verify identity before trusting the numbers")
    st.caption("Variation, model and pack size must agree. Uncertain and unmatched products cannot qualify or trigger deal alerts.")
    if review_records:
        display_table(review_records)
        choice = st.selectbox("Review a supplier product", list(range(len(review_records))),
                              format_func=lambda i: f"{review_records[i]['supplier']['title']} · {review_records[i]['match']['status']}")
        show_details(review_records[choice], review=True)
    else:
        st.info("No unresolved matches in the selected scan. Import products and run a scan to populate the queue.")

with history_tab:
    st.subheader("Saved scan history")
    st.caption("SQLite keeps each scan's estimates and match decisions. Use the Saved scan selector above to inspect a snapshot.")
    if scans:
        st.dataframe(pd.DataFrame(
            [{"Scan": scan["scan_id"], "Started": local_time(scan.get("created_at")), "Products": scan.get("total", 0),
              "Qualified · simulated": scan.get("qualified", 0), "Review": scan.get("review", 0),
              "Unmatched": scan.get("unmatched", 0)} for scan in scans]), hide_index=True, width="stretch")
        current_scan = next((scan for scan in scans if scan["scan_id"] == selected_id), {})
        if current_scan.get("settings"):
            with st.expander("Saved thresholds and assumptions for this scan"):
                st.json(current_scan["settings"])
    else:
        st.info("Your first scan will appear here.")

with settings_tab:
    st.subheader("A portable, low-cost research workspace")
    st.write("This MVP runs locally on Windows or macOS. SQLite stores supplier products, scan history and review notes. "
             "The Streamlit dashboard works in your browser. No marketplace account is needed for the simulated workflow.")
    st.markdown("**Get started**")
    st.write("1. Import the included demonstration feed or your authorised supplier CSV.\n"
             "2. Set your costs, VAT treatment and opportunity thresholds.\n"
             "3. Run a simulated scan, then inspect the shortlist and identity review queue.\n"
             "4. Check supplier availability, exact Amazon variation, actual fees and selling eligibility yourself.")
    st.markdown("**Notifications on your iPhone**")
    st.write("Telegram supports iPhone, macOS and Windows and can deliver deal notifications without keeping this browser open. "
             "Configure your bot token and chat ID in your local .env file using .env.example. "
             "Keep secrets out of source control. The dashboard only shows configuration status, never secret values.")
    st.caption("Telegram configuration detected: " + ("yes" if cfg.telegram_configured else "no"))
    st.write("Use the explicit notification and scheduler commands in the README. "
             "Any demonstration notifications must be opted into and are labelled SIMULATED. "
             "A scheduler needs the computer or server running; an iPhone does not run the Python scanner itself.")
    st.markdown("**Before live deal discovery**")
    st.write("Connect permitted retailer or wholesaler data and an authorised Amazon integration that supplies current prices, "
             "fees, demand evidence, competing offers and seller-specific listing restrictions. "
             "Replace the mock adapter only when those data and permissions are available.")
    st.caption("No purchases or Amazon listings are created. Connectors must respect site terms, API permissions and access restrictions.")
