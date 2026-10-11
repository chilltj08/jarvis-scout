"""Streamlit demo dashboard for offline JARVIS Scout data."""
import streamlit as st

from jarvis_scout.pipeline import discover

st.set_page_config(page_title="JARVIS Scout", page_icon="🔎", layout="wide")
st.title("🔎 JARVIS Scout")
st.warning(
    "DEMO ONLY: All supplier prices, Amazon prices and product identifiers below "
    "are synthetic. No live Amazon connection or selling eligibility checks."
)
st.caption("Research tool only. No buying, Amazon listings or live alerts.")

left, right = st.columns(2)
min_profit = left.number_input(
    "Minimum estimated profit (£)", min_value=0.0, value=5.0, step=1.0
)
min_roi = right.number_input(
    "Minimum estimated ROI (%)", min_value=0.0, value=25.0, step=5.0
)

items = discover(minimum_profit=min_profit, minimum_roi=min_roi)
rows = [
    {
        "Product (SIMULATED)": item.supplier_offer.name,
        "Supplier (SIMULATED)": item.supplier_offer.supplier,
        "Buy (£)": float(item.estimate.purchase_cost),
        "Amazon price (£, SIMULATED)": float(item.estimate.selling_price),
        "Referral fee (£)": float(item.estimate.referral_fee),
        "Fulfilment (£)": float(item.estimate.fulfilment_cost),
        "Inbound (£)": float(item.estimate.inbound_cost),
        "Net estimate (£)": float(item.estimate.estimated_profit),
        "ROI (%)": float(item.estimate.roi_percent),
        "Eligibility": "NOT VERIFIED",
    }
    for item in items
]

st.metric("Simulated matches passing filters", len(rows))
if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("No simulated matches pass the selected filters.")

st.info(
    "Profit estimates exclude VAT adjustments, storage fees, advertising, "
    "returns beyond any included allowance and seller-specific costs. "
    "Investigate real supplier authenticity and Amazon selling permission first."
)
