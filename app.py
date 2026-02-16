import streamlit as st
import io
from fpdf import FPDF

st.set_page_config(page_title="FlocBot ROI Estimator", page_icon="💧", layout="centered")

st.title("FlocBot ROI Estimator")
st.markdown("Estimate the return on investment from optimizing coagulant dosing with FlocBot.")

# --- Scenario buttons ---
st.subheader("Quick Scenarios")
scenario_cols = st.columns(3)
with scenario_cols[0]:
    if st.button("Conservative (3%)", use_container_width=True):
        st.session_state["overfeed_pct"] = 3.0
with scenario_cols[1]:
    if st.button("Moderate (7%)", use_container_width=True):
        st.session_state["overfeed_pct"] = 7.0
with scenario_cols[2]:
    if st.button("Aggressive (12%)", use_container_width=True):
        st.session_state["overfeed_pct"] = 12.0

# --- Inputs ---
st.subheader("Inputs")

annual_spend = st.number_input(
    "Annual coagulant spend ($/year) — recommended",
    min_value=0.0,
    value=0.0,
    step=10000.0,
    format="%.2f",
    help="If you know your total annual coagulant spend, enter it here. This is the most accurate baseline. Leave at 0 to calculate from dose instead.",
)

with st.expander("Optional: calculate from dose instead", expanded=annual_spend == 0.0):
    flow_mgd = st.number_input(
        "Flow (MGD)",
        min_value=0.0,
        value=1.0,
        step=0.1,
        format="%.2f",
        help="Average daily flow in million gallons per day.",
    )

    coagulant_type = st.selectbox(
        "Coagulant type",
        ["Alum", "Ferric Chloride", "PAC (Polyaluminum Chloride)", "ACH", "Other"],
        help="Select the coagulant used at your plant.",
    )

    unit_cost = st.number_input(
        "Coagulant unit cost ($/lb)",
        min_value=0.0,
        value=0.15,
        step=0.01,
        format="%.4f",
        help="Cost per pound of coagulant as delivered.",
    )

    current_dose = st.number_input(
        "Current dose (mg/L)",
        min_value=0.0,
        value=30.0,
        step=1.0,
        format="%.1f",
        help="Current average coagulant dose in mg/L.",
    )

st.markdown("---")

overfeed_pct = st.number_input(
    "Avoidable overfeed (%)",
    min_value=0.0,
    max_value=100.0,
    value=st.session_state.get("overfeed_pct", 7.0),
    step=0.5,
    format="%.1f",
    help="Estimated percentage of current coagulant use that is avoidable overfeed. Use the scenario buttons above for common estimates.",
    key="overfeed_input",
)

st.markdown("---")
st.markdown("**FlocBot Cost**")
cost_mode = st.radio(
    "Cost basis",
    ["Annual subscription", "Upfront purchase"],
    horizontal=True,
    help="Choose how you'll pay for FlocBot.",
)

if cost_mode == "Annual subscription":
    flocbot_annual = st.number_input(
        "FlocBot annual cost ($/year)",
        min_value=0.0,
        value=15000.0,
        step=1000.0,
        format="%.2f",
    )
    flocbot_upfront = 0.0
else:
    flocbot_upfront = st.number_input(
        "FlocBot upfront cost ($)",
        min_value=0.0,
        value=75000.0,
        step=1000.0,
        format="%.2f",
    )
    flocbot_annual = 0.0

# --- Calculations ---
# Baseline annual chemical cost
if annual_spend > 0:
    baseline_cost = annual_spend
    baseline_source = "provided"
else:
    # mg/L × MGD × 8.34 lb·L/(mg·MG) = lb/day
    lbs_per_day = current_dose * flow_mgd * 8.34
    baseline_cost = lbs_per_day * unit_cost * 365.0
    baseline_source = "calculated"

annual_savings = baseline_cost * (overfeed_pct / 100.0)

if cost_mode == "Annual subscription":
    net_annual_savings = annual_savings - flocbot_annual
    if net_annual_savings > 0:
        payback_years = flocbot_annual / net_annual_savings  # fraction of first year
        payback_months = (flocbot_annual / annual_savings) * 12 if annual_savings > 0 else float("inf")
    else:
        payback_months = float("inf")
    five_year_net = (annual_savings * 5) - (flocbot_annual * 5)
else:
    net_annual_savings = annual_savings
    if annual_savings > 0:
        payback_months = (flocbot_upfront / annual_savings) * 12
    else:
        payback_months = float("inf")
    five_year_net = (annual_savings * 5) - flocbot_upfront

# --- Input validation ---
errors = []
if annual_spend == 0:
    if flow_mgd <= 0:
        errors.append("Flow must be greater than 0.")
    if unit_cost <= 0:
        errors.append("Coagulant unit cost must be greater than 0.")
    if current_dose <= 0:
        errors.append("Current dose must be greater than 0.")
if annual_spend == 0 and flow_mgd == 0 and current_dose == 0:
    errors.append("Provide either annual coagulant spend or flow/dose/unit cost to calculate.")

# --- Outputs ---
st.subheader("Results")

if errors:
    for e in errors:
        st.error(e)
else:
    col1, col2 = st.columns(2)
    with col1:
        src_label = " (provided)" if baseline_source == "provided" else " (calculated)"
        st.metric("Annual Chemical Cost" + src_label, f"${baseline_cost:,.0f}")
        st.metric("Estimated Annual Savings", f"${annual_savings:,.0f}")
    with col2:
        if payback_months == float("inf"):
            st.metric("Payback Period", "N/A")
        elif payback_months < 1:
            st.metric("Payback Period", "< 1 month")
        else:
            years = int(payback_months // 12)
            months = int(payback_months % 12)
            if years > 0 and months > 0:
                st.metric("Payback Period", f"{years}y {months}m")
            elif years > 0:
                st.metric("Payback Period", f"{years}y")
            else:
                st.metric("Payback Period", f"{months} months")
        st.metric("5-Year Net Savings", f"${five_year_net:,.0f}")

    if five_year_net < 0:
        st.warning("FlocBot cost exceeds projected savings over 5 years at this overfeed estimate.")

    # --- PDF Download ---
    def generate_pdf():
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 12, "FlocBot ROI Estimate", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(6)

        pdf.set_font("Helvetica", "", 11)

        def add_row(label, value):
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(90, 8, label, new_x="RIGHT")
            pdf.set_font("Helvetica", "", 11)
            pdf.cell(0, 8, value, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Inputs", new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)

        if annual_spend > 0:
            add_row("Annual Coagulant Spend:", f"${annual_spend:,.2f}")
        else:
            add_row("Flow:", f"{flow_mgd:.2f} MGD")
            add_row("Coagulant Type:", coagulant_type)
            add_row("Unit Cost:", f"${unit_cost:.4f}/lb")
            add_row("Current Dose:", f"{current_dose:.1f} mg/L")

        add_row("Avoidable Overfeed:", f"{overfeed_pct:.1f}%")
        if cost_mode == "Annual subscription":
            add_row("FlocBot Annual Cost:", f"${flocbot_annual:,.2f}")
        else:
            add_row("FlocBot Upfront Cost:", f"${flocbot_upfront:,.2f}")

        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Results", new_x="LMARGIN", new_y="NEXT")
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)

        add_row("Annual Chemical Cost:", f"${baseline_cost:,.0f}")
        add_row("Estimated Annual Savings:", f"${annual_savings:,.0f}")
        if payback_months == float("inf"):
            add_row("Payback Period:", "N/A")
        elif payback_months < 1:
            add_row("Payback Period:", "< 1 month")
        else:
            years = int(payback_months // 12)
            months = int(payback_months % 12)
            if years > 0 and months > 0:
                add_row("Payback Period:", f"{years} year(s) {months} month(s)")
            elif years > 0:
                add_row("Payback Period:", f"{years} year(s)")
            else:
                add_row("Payback Period:", f"{months} month(s)")
        add_row("5-Year Net Savings:", f"${five_year_net:,.0f}")

        pdf.ln(10)
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, "Generated by FlocBot ROI Estimator", align="C")

        return bytes(pdf.output())

    pdf_bytes = generate_pdf()
    st.download_button(
        label="Download PDF Summary",
        data=pdf_bytes,
        file_name="flocbot_roi_estimate.pdf",
        mime="application/pdf",
    )
