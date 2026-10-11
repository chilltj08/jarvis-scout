"""JARVIS SCOUT's local dashboard. Scans never purchase, list or send alerts."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import streamlit as st

from jarvis_scout.config import AppConfig
from jarvis_scout.manual import ResearchAmazonAdapter, create_manual_listing
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


def has_simulated_data(record: dict) -> bool:
    return bool((record.get("supplier") or {}).get("is_demo") or record.get("simulated")
                or record.get("data_mode") == "mixed")


def is_candidate(record: dict) -> bool:
    return bool(record.get("qualified") and ((record.get("supplier") or {}).get("is_demo")
                or not has_simulated_data(record)))


def data_label(record: dict) -> str:
    mode = record.get("data_mode")
    if mode == "demo" or (record.get("supplier") or {}).get("is_demo"):
        return "DEMO · SIMULATED"
    if mode == "mixed" or record.get("simulated"):
        return "REAL SUPPLIER · SIMULATED AMAZON"
    return "REAL SUPPLIER · MANUAL OBSERVATION" if record.get("listing") else "REAL SUPPLIER · NO AMAZON OBSERVATION"


def table_rows(records: list[dict]) -> pd.DataFrame:
    """Export only research fields, with price provenance beside every result."""
    rows = []
    for record in records:
        product = record.get("supplier") or {}
        listing = record.get("listing") or {}
        profit = record.get("profitability") or {}
        rows.append({
            "Record": record.get("id"), "Product": product.get("title", "Untitled product"),
            "Supplier": product.get("source", ""), "Pack": product.get("pack_quantity"),
            "Purchase price / pack (£)": number(product.get("unit_cost")),
            "Observed Amazon price (£)": number(listing.get("price")),
            "Profit / pack (£)": number(profit.get("profit")),
            "Minimum-order cash cost (£)": number(record.get("minimum_order_cost")),
            "ROI (%)": number(profit.get("roi_percent")), "Margin (%)": number(profit.get("margin_percent")),
            "Score": round(number(record.get("score")) or 0, 1),
            "Match": (record.get("match") or {}).get("status", "unmatched"),
            "Estimate": record.get("estimate_status", "Legacy simulated estimate" if has_simulated_data(record) else "complete" if profit else "review"),
            "Qualified": is_candidate(record), "Data mode": data_label(record),
            "Supplier link": safe_link(product.get("supplier_url")),
        })
    return pd.DataFrame(rows)


def display_table(records: list[dict]) -> None:
    st.dataframe(table_rows(records), hide_index=True, width="stretch", column_config={
        "Record": None,
        "Supplier link": st.column_config.LinkColumn("Supplier link", display_text="Open supplier ↗"),
        "Purchase price / pack (£)": st.column_config.NumberColumn(format="£%.2f"),
        "Observed Amazon price (£)": st.column_config.NumberColumn(format="£%.2f"),
        "Profit / pack (£)": st.column_config.NumberColumn(format="£%.2f"),
        "Minimum-order cash cost (£)": st.column_config.NumberColumn(format="£%.2f"),
        "ROI (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "Margin (%)": st.column_config.NumberColumn(format="%.1f%%"),
    })


def export_csv(records: list[dict]) -> bytes:
    frame = table_rows(records)
    def safe_cell(value: object) -> object:
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    return frame.map(safe_cell).to_csv(index=False).encode("utf-8")


def freshness_label(value: object) -> str:
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.utcoffset() is None:
            return "Unknown timestamp"
        hours = (datetime.now(timezone.utc) - observed).total_seconds() / 3600
        if hours < 0:
            return "Future timestamp — check feed"
        return f"{hours:.1f} hours old"
    except (ValueError, TypeError):
        return "Unknown timestamp"


def optional_decimal(value: str, label: str) -> Decimal | None:
    if not value.strip():
        return None
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError(f"{label} must be a number, or blank if unknown.") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{label} must be a finite nonnegative number.")
    return amount


def show_details(record: dict, *, review: bool = False) -> None:
    product, listing = record.get("supplier") or {}, record.get("listing") or {}
    match, profit = record.get("match") or {}, record.get("profitability") or {}
    st.subheader(product.get("title", "Product details"))
    st.caption(f"{data_label(record)} · {product.get('source', 'Unknown supplier')} · "
               f"SKU {product.get('sku', '—')} · Match: {match.get('status', 'unmatched')}")
    supplier_link = safe_link(product.get("supplier_url"))
    if supplier_link:
        st.link_button("Open supplier product ↗", supplier_link)
    simulated = has_simulated_data(record)
    if simulated:
        st.warning("SIMULATED Amazon prices, fees and market signals. This is a demonstration, not a real opportunity.")
    else:
        st.info("Amazon prices and fees are your manually recorded observations. Recheck availability, fees and eligibility before acting.")
    for warning in record.get("warnings", []):
        st.warning(warning)
    if record.get("incomplete_reasons"):
        st.warning("Incomplete estimate: " + "; ".join(record["incomplete_reasons"]))
    identity_rows = [{"Attribute": label, "Supplier product": str(product.get(key) or "—"),
                      "Simulated Amazon listing" if simulated else "Manually observed Amazon listing": str(listing.get(key) or "—")}
                     for label, key in [("Brand", "brand"), ("Model", "model"), ("EAN / GTIN", "ean"),
                                        ("Variation", "variation"), ("Pack quantity", "pack_quantity")]]
    st.dataframe(pd.DataFrame(identity_rows), hide_index=True, width="stretch")
    if listing:
        st.caption(f"{'Simulated' if simulated else 'Observed'} ASIN: {listing.get('asin', '—')} · "
                   f"Amazon observation: {local_time(listing.get('observed_at'))}")
        if listing.get("evidence_source"):
            st.write("Evidence source: " + listing["evidence_source"])
    for reason in match.get("reasons", []):
        st.write(f"• {reason}")
    if profit:
        for cell, label, value in zip(st.columns(4),
                ["Estimated profit / pack", "ROI on total cost", "Profit margin", "Total cost / pack"],
                [money(profit.get("profit")), percentage(profit.get("roi_percent")),
                 percentage(profit.get("margin_percent")), money(profit.get("total_cost"))]):
            cell.metric(label, value)
        if record.get("minimum_order_cost") is not None:
            st.caption(f"Minimum-order supplier cash cost: {money(record['minimum_order_cost'])} · "
                       f"MOQ {product.get('minimum_order_quantity', 1)} sellable packs. "
                       "Order commitment differs from profit on one sold pack.")
        with st.expander("Cost breakdown and VAT treatment", expanded=review):
            labels = [("Simulated sale price" if simulated else "Observed sale price (VAT inclusive)", "sale_price"),
                      ("Output VAT on sale", "output_vat"), ("Net sale revenue", "net_revenue"),
                      ("Purchase cost", "purchase_cost"), ("Allocated supplier shipping", "supplier_delivery"),
                      ("Amazon referral fee", "referral_fee"), ("Estimated fulfilment fee", "fba_fee"),
                      ("Packaging", "packaging"), ("Additional fulfilment / outbound shipping", "inbound_shipping"),
                      ("Other costs", "other_costs"), ("Returns allowance", "returns_allowance"),
                      ("Unrecoverable VAT on Amazon fees", "vat_cost"), ("Total economic cost", "total_cost"),
                      ("Estimated profit", "profit")]
            st.dataframe(pd.DataFrame([{"Component": label, "Amount": money(profit.get(key))}
                                     for label, key in labels]), hide_index=True, width="stretch")
            st.caption("All costs are per matched sellable pack. Supplier order shipping is allocated across its MOQ. "
                       "Extra shipping, packaging and other costs are VAT inclusive; avoid counting costs twice. "
                       "For VAT registrants, eligible input VAT reduces costs and output VAT reduces revenue. "
                       "Fee estimates are VAT exclusive. ROI = profit ÷ total economic cost; "
                       "margin = profit ÷ net sale revenue. These are estimates, not tax advice.")
            st.caption(f"Assumed recoverable input VAT: {money(profit.get('recoverable_input_vat'))}")
    with st.expander("Demand, pricing and eligibility"):
        for key, label in [("demand_confidence", "Demand confidence"), ("pricing_stability", "Pricing stability")]:
            value = listing.get(key)
            st.write(f"{label}: " + (percentage(100 * float(value)) if value is not None else "Unknown — no market data connected"))
        st.write(f"Competing offers: {listing.get('competition') if listing.get('competition') is not None else 'Unknown'}")
        st.write(f"Listing eligibility: {listing.get('restriction_status', 'unknown')}")
        st.write(f"Supplier observation: {local_time(product.get('observed_at'))}")
    if record.get("filter_reasons"):
        st.warning("Excluded by the saved scan filters: " + "; ".join(record["filter_reasons"]))
    if review:
        st.caption("Review notes record your checks; they do not approve a match or enable alerts.")
        note = st.text_area("Review note", value=record.get("review_note") or "", max_chars=4000,
                            key=f"note_{record['id']}")
        if st.button("Save review note", key=f"save_note_{record['id']}"):
            try:
                db.set_review_note(record["id"], note)
                st.success("Review note saved.")
            except Exception as error:
                st.error(f"Could not save the review note: {error}")



def remove_manual_observation(comparison_id: int, choice_key: str) -> None:
    """Explicit user action removes current evidence, never saved scan snapshots."""
    try:
        db.delete_manual_comparison(comparison_id)
        st.session_state[choice_key] = None
        st.session_state["comparison_removed"] = True
    except Exception as error:
        st.session_state["comparison_remove_error"] = str(error)

def show_import_result(report: object) -> None:
    st.success(f"Imported or updated {report.imported} supplier products. Unchanged or older quotes are not duplicated.")
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
    st.caption("Real supplier research · optional simulation")
    with st.expander("1 · Import supplier products", expanded=True):
        st.caption("Use permitted supplier feeds. Purchase price is per sellable pack; shipping is the total MOQ-order charge.")
        template_path = Path(__file__).parent / "data" / "supplier_template.csv"
        if template_path.exists():
            st.download_button("Download supplier CSV template", data=template_path.read_bytes(),
                               file_name="supplier_template.csv", mime="text/csv", width="stretch")
        upload = st.file_uploader("Retailer / wholesaler CSV", type=["csv"])
        if st.button("Import uploaded CSV", disabled=upload is None, width="stretch"):
            try:
                show_import_result(import_feed(StringIO(upload.getvalue().decode("utf-8-sig")), db, is_demo=False))
                st.session_state["research_scope"] = "Real suppliers"
            except Exception as error:
                st.error(f"Could not import this CSV: {error}")
        if st.button("Import demonstration feed", width="stretch"):
            try:
                show_import_result(import_feed(cfg.feed_path, db, is_demo=True))
                st.session_state["research_scope"] = "Demo"
            except Exception as error:
                st.error(f"Could not import the demonstration feed: {error}")
    scope = st.selectbox("Research scope", ["Real suppliers", "Demo", "All"], key="research_scope")
    scope_filter = {"Real suppliers": False, "Demo": True, "All": None}[scope]
    with st.expander("2 · Opportunity thresholds", expanded=False):
        min_profit = st.number_input("Minimum profit per pack (£)", min_value=0.0, value=3.0, step=0.5)
        min_roi = st.number_input("Minimum ROI (%)", min_value=0.0, value=30.0, step=5.0)
        require_market = st.checkbox("Require demand, stability and competition data", value=False,
                                     help="Manual observations do not supply these market signals. Enable to exclude missing signals.")
        demand = st.slider("Minimum demand confidence", 0, 100, 50)
        stability = st.slider("Minimum pricing stability", 0, 100, 50)
        competition = st.number_input("Maximum competing offers", min_value=0, value=10, step=1)
        require_eligible = st.checkbox("Require confirmed listing eligibility", value=False,
                                       help="Unknown eligibility is a warning by default. Restricted listings are always excluded.")
        freshness = st.number_input("Maximum data age (hours)", min_value=0.1, value=48.0, step=1.0)
    with st.expander("3 · Cost and VAT assumptions", expanded=False):
        packaging = st.number_input("Packaging per pack, VAT inclusive (£)", min_value=0.0, value=0.5, step=0.1)
        extra_shipping = st.number_input("Additional fulfilment / outbound shipping per pack (£)", min_value=0.0, value=0.0, step=0.1,
                                        help="VAT inclusive. Only costs not already included in the fulfilment fee or supplier shipping.")
        other_costs = st.number_input("Other costs per pack, VAT inclusive (£)", min_value=0.0, value=0.0, step=0.1)
        returns = st.number_input("Returns allowance (% of net sale revenue)", min_value=0.0, max_value=100.0,
                                  value=3.0, step=0.5)
        vat_registered = st.checkbox("VAT registered", value=False)
        reclaim = st.checkbox("Reclaim eligible input VAT", value=False, disabled=not vat_registered,
                              help="Only with valid invoices and eligible VAT recovery.")
        custom_vat = st.checkbox("Set category-specific VAT rates", value=False)
        vat_rates = {key: Decimal("0.20") for key in ["vat_rate", "purchase_vat_rate", "delivery_vat_rate",
                    "packaging_vat_rate", "amazon_fees_vat_rate", "inbound_shipping_vat_rate", "other_costs_vat_rate"]}
        if custom_vat:
            for key, label in [("vat_rate", "Sales VAT (%)"), ("purchase_vat_rate", "Purchase VAT (%)"),
                               ("delivery_vat_rate", "Supplier shipping VAT (%)"), ("packaging_vat_rate", "Packaging VAT (%)"),
                               ("amazon_fees_vat_rate", "Amazon fee VAT (%)"),
                               ("inbound_shipping_vat_rate", "Additional shipping VAT (%)"), ("other_costs_vat_rate", "Other costs VAT (%)")]:
                vat_rates[key] = Decimal(str(st.number_input(label, min_value=0.0, max_value=100.0,
                                                           value=20.0, step=1.0))) / Decimal("100")
        st.caption("Default rates: 20%. CSV VAT treatment applies to supplier purchase and shipping. "
                   "Use a defensible effective rate for mixed other costs. Standard VAT accounting only.")
    filters = OpportunityFilters(min_profit=Decimal(str(min_profit)), min_roi_percent=Decimal(str(min_roi)),
        min_demand_confidence=demand / 100, min_pricing_stability=stability / 100,
        max_competition=int(competition), max_data_age_hours=freshness,
        require_market_signals=require_market or scope == "Demo", require_eligible=require_eligible or scope == "Demo")
    assumptions = ProfitAssumptions(packaging_cost=Decimal(str(packaging)),
        inbound_shipping_cost=Decimal(str(extra_shipping)), other_costs=Decimal(str(other_costs)),
        returns_rate=Decimal(str(returns)) / Decimal("100"), vat_registered=vat_registered,
        reclaim_input_vat=reclaim, **vat_rates)
    scan_label = "Run simulated scan" if scope == "Demo" else "Run research scan"
    if st.button(scan_label, type="primary", width="stretch"):
        if not db.products(is_demo=scope_filter):
            st.warning("Import a CSV for the selected scope first, or explicitly import the demonstration feed.")
        else:
            try:
                with st.spinner("Checking saved product identities and estimating profitability…"):
                    report = run_scan(db, ResearchAmazonAdapter(db, catalog_path=cfg.amazon_catalog_path),
                                      assumptions, filters, is_demo=scope_filter)
                qualifier = "simulated opportunities" if scope == "Demo" else "financial research candidates"
                st.success(f"Scan #{report.scan_id}: {report.qualified} {qualifier}. Inspect warnings and evidence.")
            except Exception as error:
                st.error(f"Scan could not complete: {error}")
    st.caption("Thresholds apply to the next scan. Saved scans retain their original inputs. Scanning does not send alerts.")
    st.divider()
    st.caption("Telegram: " + ("configured" if cfg.telegram_configured else "not configured"))

st.markdown('<div class="scout-eyebrow">UK AMAZON RESALE RESEARCH</div>', unsafe_allow_html=True)
st.markdown('<div class="scout-title">JARVIS SCOUT</div>', unsafe_allow_html=True)
st.markdown('<div class="scout-subtitle">Supplier research + manually recorded Amazon comparisons. No Seller Central account needed.</div>',
            unsafe_allow_html=True)
if scope == "Demo":
    st.markdown('<span class="scout-badge">DEMONSTRATION · SIMULATED AMAZON DATA</span>', unsafe_allow_html=True)
else:
    st.caption("Real supplier quotes and manual Amazon evidence stay separate. Missing prices are never invented. "
               "Financial candidates require checks of demand, stock, eligibility and current fees.")
    if scope == "All":
        st.warning("This view includes demo products. Every result is labelled with its data mode.")

scans = db.list_scans(limit=50)
latest_id = db.latest_scan_id()
selected_id = latest_id
if scans:
    scan_labels = {scan["scan_id"]: f"Scan #{scan['scan_id']} · {local_time(scan.get('created_at'))}" for scan in scans}
    selected_id = st.selectbox("Saved scan", list(scan_labels), format_func=scan_labels.get,
                               index=list(scan_labels).index(latest_id) if latest_id in scan_labels else 0)
all_records = db.opportunities(scan_id=selected_id) if selected_id is not None else []
records = [record for record in all_records if scope_filter is None
           or bool((record.get("supplier") or {}).get("is_demo")) is scope_filter]
qualified_records = [record for record in records if is_candidate(record)]
review_records = [record for record in records if (record.get("match") or {}).get("status") != "exact"
                  or record.get("estimate_status") == "incomplete"
                  or (not (record.get("supplier") or {}).get("is_demo") and has_simulated_data(record))]
metrics = st.columns(4)
metrics[0].metric("Supplier products", len(db.products(is_demo=scope_filter)))
metrics[1].metric("Qualified · simulated" if scope == "Demo" else "Qualified · mixed scope" if scope == "All" else "Financial candidates", len(qualified_records))
metrics[2].metric("Review / incomplete", len(review_records))
metrics[3].metric("Saved scans", len(scans))
st.write("")
opportunities_tab, suppliers_tab, comparison_tab, review_tab, history_tab, settings_tab = st.tabs(
    ["Opportunities", "Suppliers", "Amazon comparisons", "Review queue", "Scan history", "Settings & guide"])

with opportunities_tab:
    st.subheader("Your research shortlist")
    controls = st.columns([3, 2])
    search = controls[0].text_input("Search products or suppliers", placeholder="Brand, product, supplier…")
    include_excluded = controls[1].checkbox("Include filtered-out exact matches", value=False)
    visible = [record for record in records if (record.get("match") or {}).get("status") == "exact"
               and (include_excluded or is_candidate(record))]
    if search.strip():
        query = search.strip().casefold()
        visible = [record for record in visible if query in " ".join(
            str((record.get("supplier") or {}).get(key, "")) for key in ["title", "source", "brand", "model"]).casefold()]
    visible.sort(key=lambda record: float(record.get("score") or 0), reverse=True)
    if visible:
        display_table(visible)
        st.download_button("Export these results as CSV", data=export_csv(visible),
                           file_name=f"jarvis-scout-{scope.lower().replace(' ', '-')}-scan-{selected_id}.csv", mime="text/csv")
        st.divider()
        choice = st.selectbox("Inspect a product", list(range(len(visible))),
                              format_func=lambda i: f"{visible[i]['supplier']['title']} · {data_label(visible[i])}")
        show_details(visible[choice])
    elif not records:
        st.info("Import an authorised supplier CSV, add evidence in Amazon comparisons, then run a research scan. "
                "Use the explicit demonstration feed to explore simulated examples.")
    else:
        st.info("No qualified exact matches meet this view. Check the review queue or include filtered-out exact matches.")

with suppliers_tab:
    st.subheader("Supplier products and quote freshness")
    st.caption("This is the latest imported quote for each supplier SKU, separated by real/demo origin. "
               "Freshness measures quote age; it does not verify present stock or availability.")
    supplier_rows = db.product_rows(is_demo=scope_filter)
    supplier_sources = sorted({row["supplier"]["source"] for row in supplier_rows})
    supplier_controls = st.columns([2, 3])
    source = supplier_controls[0].selectbox("Supplier source", ["All sources", *supplier_sources])
    supplier_query = supplier_controls[1].text_input("Search imported supplier products", placeholder="Title, SKU, GTIN, brand…")
    selected_rows = [row for row in supplier_rows if source == "All sources" or row["supplier"]["source"] == source]
    if supplier_query.strip():
        query = supplier_query.strip().casefold()
        selected_rows = [row for row in selected_rows if query in " ".join(str(row["supplier"].get(key, ""))
                         for key in ["title", "sku", "ean", "brand"]).casefold()]
    if selected_rows:
        supplier_frame = pd.DataFrame([{
            "Product": row["supplier"]["title"], "Supplier": row["supplier"]["source"],
            "SKU": row["supplier"]["sku"], "GTIN": row["supplier"].get("ean"),
            "Purchase price / pack (£)": number(row["supplier"].get("unit_cost")),
            "MOQ (sellable packs)": row["supplier"].get("minimum_order_quantity", 1),
            "Items / sellable pack": row["supplier"].get("pack_quantity"),
            "Order shipping (£)": number(row["supplier"].get("shipping_cost")),
            "Stock status": row["supplier"].get("stock_status", "unknown"),
            "VAT treatment": row["supplier"].get("vat_treatment") or (("included" if row["supplier"].get("supplier_vat_included") else "excluded") if row["supplier"].get("is_demo") else "unknown"),
            "Observed": local_time(row["observed_at"]), "Last updated": local_time(row["updated_at"]),
            "Quote freshness": freshness_label(row["observed_at"]),
            "Data mode": "DEMO · SIMULATED" if row["supplier"].get("is_demo") else "REAL SUPPLIER QUOTE",
            "Supplier link": safe_link(row["supplier"].get("supplier_url")),
        } for row in selected_rows])
        st.dataframe(supplier_frame, hide_index=True, width="stretch", column_config={
            "Supplier link": st.column_config.LinkColumn(display_text="Open supplier ↗"),
            "Purchase price / pack (£)": st.column_config.NumberColumn(format="£%.2f"),
            "Order shipping (£)": st.column_config.NumberColumn(format="£%.2f"),
        })
        st.caption(f"{len(selected_rows)} product(s) shown. Re-import the same supplier/SKU to update its quote; older quotes do not replace newer ones.")
    else:
        st.info("No supplier products in this view. Download the template and import your permitted supplier CSV.")

with comparison_tab:
    st.subheader("Record an Amazon listing comparison")
    if st.session_state.pop("comparison_saved", False):
        st.success("Manual Amazon observation saved. Run a research scan to check the match and recalculate estimates.")
    if st.session_state.pop("comparison_removed", False):
        st.success("Manual observation removed. Saved scan history is unchanged; run a new scan to refresh results.")
    remove_error = st.session_state.pop("comparison_remove_error", None)
    if remove_error:
        st.error(f"Could not remove the observation: {remove_error}")
    st.caption("Only real supplier products can receive manual comparisons. Enter Amazon identity and prices from your own evidence. "
               "An ASIN alone does not verify the GTIN, brand or pack; no account or automated scraping is used.")
    real_products = db.products(is_demo=False)
    if not real_products:
        st.info("Import a real supplier CSV to add a manual Amazon comparison.")
    else:
        product_index = st.selectbox("Supplier product to compare", list(range(len(real_products))),
            format_func=lambda i: f"{real_products[i].source} · {real_products[i].title} · SKU {real_products[i].sku}")
        product = real_products[product_index]
        st.write(f"Supplier purchase quote: **{money(product.unit_cost)} / sellable pack** · "
                 f"brand: {product.brand or 'unknown'} · GTIN: {product.ean or 'missing'} · "
                 f"pack: {product.pack_quantity} · MOQ: {product.minimum_order_quantity}")
        existing = db.manual_comparisons(product.product_key)
        comparison_options = [None, *[row["id"] for row in existing]]
        comparison_id = st.selectbox("New or saved observation", comparison_options,
            format_func=lambda value: "New Amazon observation" if value is None else next(
                f"Edit {row['listing']['asin']} · {local_time(row['listing'].get('observed_at'))}" for row in existing if row["id"] == value),
            key=f"comparison_choice_{product.product_key}")
        if comparison_id is not None:
            st.caption("Saving a different ASIN creates an additional comparison. Remove an obsolete observation explicitly to resolve ambiguous matches. "
                       "Removing current evidence preserves every saved scan snapshot.")
            st.button("Remove selected observation", key=f"remove_comparison_{product.product_key}_{comparison_id}",
                      on_click=remove_manual_observation, args=(comparison_id, f"comparison_choice_{product.product_key}"))
        defaults = next((row["listing"] for row in existing if row["id"] == comparison_id), {})
        now_london = datetime.now(ZoneInfo("Europe/London"))
        observed = datetime.fromisoformat(defaults["observed_at"]).astimezone(ZoneInfo("Europe/London")) if defaults else now_london
        suffix = f"{product.product_key}_{comparison_id}"
        with st.form(f"manual_comparison_{suffix}"):
            identity = st.columns(2)
            asin = identity[0].text_input("Amazon ASIN", value=defaults.get("asin", ""), key=f"asin_{suffix}")
            amazon_title = identity[1].text_input("Amazon listing title", value=defaults.get("title", ""), key=f"title_{suffix}")
            brand = identity[0].text_input("Amazon listing brand", value=defaults.get("brand", ""), key=f"brand_{suffix}")
            ean = identity[1].text_input("Amazon listing EAN / GTIN", value=defaults.get("ean") or "", key=f"ean_{suffix}")
            model = identity[0].text_input("Amazon model (if applicable)", value=defaults.get("model", ""), key=f"model_{suffix}")
            variation = identity[1].text_input("Amazon variation (if applicable)", value=defaults.get("variation", ""), key=f"variation_{suffix}")
            pack = identity[0].number_input("Amazon items per sellable pack", min_value=1, value=defaults.get("pack_quantity", 1), step=1, key=f"pack_{suffix}")
            price = identity[1].text_input("Observed Amazon selling price (£)", value=str(defaults["price"]) if defaults.get("price") is not None else "",
                                          help="Price for the complete pack, VAT inclusive. Leave blank if unknown; no price will be invented.", key=f"price_{suffix}")
            observation_date = identity[0].date_input("Amazon observation date (Europe/London)", value=observed.date(), key=f"date_{suffix}")
            observation_time = identity[1].time_input("Amazon observation time (Europe/London)", value=observed.time().replace(tzinfo=None), key=f"time_{suffix}")
            referral_default = str(Decimal(str(defaults["referral_fee_rate"])) * 100) if defaults.get("referral_fee_rate") is not None else ""
            referral = identity[0].text_input("Estimated referral fee (%)", value=referral_default,
                help="Fraction of VAT-inclusive selling price. Leave blank if unknown.", key=f"referral_{suffix}")
            fulfilment = identity[1].text_input("Estimated fulfilment fee / pack (£)", value=str(defaults["fba_fee"]) if defaults.get("fba_fee") is not None else "",
                help="VAT exclusive. FBA fee or your equivalent fulfilment estimate; enter additional shipping separately, without double counting.", key=f"fulfilment_{suffix}")
            evidence = st.text_input("Evidence source (required)", value=defaults.get("evidence_source", ""),
                help="Listing URL and/or description of the evidence and fee calculation you checked.", key=f"evidence_{suffix}")
            verified = st.checkbox("I checked the listing GTIN, brand and pack against evidence", value=defaults.get("identifier_verified", False), key=f"verified_{suffix}")
            fees_confirmed = st.checkbox("Entered fee estimates cover this fulfilment method", value=defaults.get("fees_confirmed", False), key=f"fees_{suffix}")
            eligibility_options = ["unknown", "eligible", "restricted"]
            eligibility = st.selectbox("Listing eligibility", eligibility_options,
                index=eligibility_options.index(defaults.get("restriction_status", "unknown")),
                help="Leave unknown unless you have checked your own eligibility. Restricted listings never qualify.", key=f"eligibility_{suffix}")
            submitted = st.form_submit_button("Save Amazon comparison", type="primary")
        if submitted:
            try:
                referral_rate = optional_decimal(referral, "Referral fee")
                listing = create_manual_listing(asin=asin, title=amazon_title, brand=brand, ean=ean or None,
                    pack_quantity=int(pack), price=optional_decimal(price, "Observed selling price"),
                    observed_at=datetime.combine(observation_date, observation_time, tzinfo=ZoneInfo("Europe/London")).isoformat(),
                    evidence_source=evidence, model=model, variation=variation,
                    referral_fee_rate=referral_rate / Decimal("100") if referral_rate is not None else None,
                    fulfilment_fee=optional_decimal(fulfilment, "Fulfilment fee"), identifier_verified=verified,
                    fees_confirmed=fees_confirmed, restriction_status=eligibility)
                db.save_manual_comparison(product.product_key, listing)
                st.session_state["comparison_saved"] = True
                st.rerun()
            except Exception as error:
                st.error(f"Could not save the comparison: {error}")
        saved_comparisons = db.manual_comparisons(product.product_key)
        if saved_comparisons:
            st.markdown("**Saved manual observations for this supplier product**")
            st.dataframe(pd.DataFrame([{
                "ASIN": row["listing"]["asin"], "Observed Amazon price (£)": number(row["listing"].get("price")),
                "Observed": local_time(row["listing"].get("observed_at")),
                "Evidence": row["listing"].get("evidence_source"),
                "Identity checked": row["listing"].get("identifier_verified"),
                "Fees complete": row["listing"].get("fees_confirmed"),
                "Eligibility": row["listing"].get("restriction_status", "unknown"),
                "Last updated": local_time(row["updated_at"]), "Data mode": "MANUAL OBSERVATION",
            } for row in saved_comparisons]), hide_index=True, width="stretch")
            st.caption("Save the same supplier product and ASIN again to update that observation. Saved scans keep their old evidence snapshots.")

with review_tab:
    st.subheader("Verify identity and complete the estimate")
    st.caption("Verified GTIN, compatible brand, variation and pack size must agree. Uncertain matches and incomplete estimates cannot qualify.")
    if review_records:
        display_table(review_records)
        choice = st.selectbox("Review a supplier product", list(range(len(review_records))),
            format_func=lambda i: f"{review_records[i]['supplier']['title']} · {review_records[i]['match']['status']} · {data_label(review_records[i])}")
        show_details(review_records[choice], review=True)
    else:
        st.info("No unresolved matches or incomplete estimates in the selected scope and scan.")

with history_tab:
    st.subheader("Saved scan history")
    st.caption("SQLite keeps each scan's prices, evidence, assumptions and identity decisions. The research scope also filters the saved results above.")
    if scans:
        history_rows = []
        for scan in scans:
            scan_records = db.opportunities(scan["scan_id"])
            modes = sorted({data_label(record) for record in scan_records})
            history_rows.append({"Scan": scan["scan_id"], "Started": local_time(scan.get("created_at")),
                "Products": scan.get("total", 0),
                "Real financial candidates": sum(is_candidate(record) and not (record.get("supplier") or {}).get("is_demo") for record in scan_records),
                "Demo candidates": sum(is_candidate(record) and (record.get("supplier") or {}).get("is_demo", False) for record in scan_records),
                "Legacy real + simulated comparisons": sum(has_simulated_data(record) and not (record.get("supplier") or {}).get("is_demo") for record in scan_records),
                "Review": scan.get("review", 0), "Unmatched": scan.get("unmatched", 0), "Data mode": "; ".join(modes) or "Empty scan"})
        st.dataframe(pd.DataFrame(history_rows), hide_index=True, width="stretch")
        current_scan = next((scan for scan in scans if scan["scan_id"] == selected_id), {})
        if current_scan.get("settings"):
            with st.expander("Saved thresholds and assumptions for this scan"):
                st.json(current_scan["settings"])
    else:
        st.info("Your first scan will appear here.")

with settings_tab:
    st.subheader("A portable, low-cost research workspace")
    st.write("Run locally on Windows or macOS. SQLite stores imported supplier quotes, manual Amazon observations, scan history and review notes. "
             "No Amazon Seller Central account is required for manual product research.")
    st.markdown("**Get started**")
    st.write("1. Download the supplier CSV template, fill it with permitted real quotes, and import it.\n"
             "2. Check Suppliers for row validation, stock, VAT treatment, shipping and freshness.\n"
             "3. Add an Amazon comparison using the exact GTIN, brand and pack, observed price and fee evidence.\n"
             "4. Set cost/VAT assumptions and run a research scan. Complete missing inputs and inspect warnings.\n"
             "5. Recheck demand, listing eligibility, fees and availability before considering any purchase.")
    st.markdown("**Notifications on your iPhone**")
    st.write("Telegram delivers notifications on iPhone, macOS and Windows. Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your local .env file. "
             "Keep secrets out of Git. The dashboard never sends messages automatically or displays credentials.")
    st.caption("Telegram configuration detected: " + ("yes" if cfg.telegram_configured else "no"))
    st.code("python -m jarvis_scout scan --alerts\npython -m jarvis_scout watch --alerts", language="powershell")
    st.write("See the README for notification safeguards and scan scheduling. Real financial candidates use saved manual evidence, with unknown market signals labelled. "
             "Mock Amazon data never triggers real-opportunity alerts. Demo notifications require explicit opt-in and carry a SIMULATED label. "
             "Scheduling requires this computer or server to stay running.")
    st.markdown("**Limitations and next integrations**")
    st.write("Supplier CSVs and manual observations do not discover live deals automatically. Demand, pricing stability, competition and selling eligibility may remain unknown. "
             "Connect permitted supplier APIs and supported Amazon/demand integrations when needed. Prices and fee estimates are user observations, not verified market quotations.")
    st.caption("No scraping, purchases or Amazon listing creation. All data sources must respect permissions and site restrictions.")
