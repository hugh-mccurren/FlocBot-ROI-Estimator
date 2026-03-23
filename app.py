import streamlit as st
import keep_alive
keep_alive.start()

from fpdf import FPDF
import pandas as pd
import altair as alt
from datetime import datetime

APP_VERSION = "v0.3"
SENSITIVITY_PCTS = [3, 5, 7, 10, 12]

# ---------------------------------------------------------------------------
# Pure calculation functions
# ---------------------------------------------------------------------------

def compute_baseline_from_dose(dose_mg_l, flow_mgd, unit_cost_per_lb, operating_days=365):
    lbs_per_day = dose_mg_l * flow_mgd * 8.34
    lbs_per_year = lbs_per_day * operating_days
    return lbs_per_year * unit_cost_per_lb, lbs_per_day, lbs_per_year


def compute_annual_savings(baseline_cost, overfeed_pct):
    return baseline_cost * (overfeed_pct / 100.0)


def compute_flocbot_total_cost(cost_mode, flocbot_annual, flocbot_upfront, years=5):
    if cost_mode == "Annual subscription":
        return flocbot_annual * years
    return flocbot_upfront


def compute_5yr_net(baseline_cost, overfeed_pct, cost_mode,
                    flocbot_annual, flocbot_upfront, escalation_pct,
                    discount_rate_pct, years=5):
    r = escalation_pct / 100.0
    d = discount_rate_pct / 100.0
    total_savings = 0.0
    for yr in range(years):
        yr_savings = baseline_cost * (overfeed_pct / 100.0) * ((1 + r) ** yr)
        if d > 0:
            yr_savings /= (1 + d) ** yr
        total_savings += yr_savings
    total_flocbot = compute_flocbot_total_cost(cost_mode, flocbot_annual, flocbot_upfront, years)
    return total_savings, total_flocbot, total_savings - total_flocbot


def compute_payback_years(annual_savings, cost_mode, flocbot_annual, flocbot_upfront):
    """Simple payback using Year 1 savings only (no escalation).
    Always returns a number when annual_savings > 0."""
    if annual_savings <= 0:
        return None
    if cost_mode == "Annual subscription":
        net = annual_savings - flocbot_annual
        if net <= 0:
            return None  # subscription costs more than savings each year
        return flocbot_annual / annual_savings
    return flocbot_upfront / annual_savings  # always valid for upfront


def compute_payback_cashflow(baseline_cost, overfeed_pct, cost_mode,
                             flocbot_annual, flocbot_upfront,
                             escalation_pct, discount_rate_pct, years=100):
    """Cashflow-based payback with escalation and optional discounting.

    Returns payback in years (with fractional interpolation), or None if
    cumulative net never reaches zero within *years*.
    """
    r = escalation_pct / 100.0
    d = discount_rate_pct / 100.0
    cumulative = 0.0
    prev_cumulative = 0.0
    for yr in range(years):
        savings = baseline_cost * (overfeed_pct / 100.0) * ((1 + r) ** yr)
        if d > 0:
            savings /= (1 + d) ** yr
        if cost_mode == "Annual subscription":
            fb_cost = flocbot_annual
        else:
            fb_cost = flocbot_upfront if yr == 0 else 0.0
        net = savings - fb_cost
        prev_cumulative = cumulative
        cumulative += net
        if cumulative >= 0 and prev_cumulative < 0:
            # Interpolate within this year
            fraction = (0 - prev_cumulative) / net if net != 0 else 0
            return yr + fraction
        if cumulative >= 0 and yr == 0:
            # Paid back within Year 1
            if net >= 0 and savings > 0:
                # Fraction of the year needed: cost portion / savings
                if cost_mode == "Annual subscription":
                    return flocbot_annual / savings
                return flocbot_upfront / savings
            return None
    return None


def compute_break_even_pct(baseline_cost, cost_mode, flocbot_annual, flocbot_upfront):
    if baseline_cost <= 0:
        return None
    if cost_mode == "Annual subscription":
        return (flocbot_annual / baseline_cost) * 100.0
    return (flocbot_upfront / baseline_cost) * 100.0


def format_payback(payback_years):
    if payback_years is None:
        return "See details below"
    months_total = payback_years * 12
    if months_total < 1:
        return "< 1 month"
    years = int(months_total // 12)
    months = int(months_total % 12)
    if years > 0 and months > 0:
        return f"{years}y {months}m"
    if years > 0:
        return f"{years}y"
    return f"{months} months"


def compute_yearly_cashflows(baseline_cost, overfeed_pct, cost_mode,
                             flocbot_annual, flocbot_upfront, escalation_pct,
                             discount_rate_pct, years=5):
    r = escalation_pct / 100.0
    d = discount_rate_pct / 100.0
    rows = []
    cumulative = 0.0
    for yr in range(years):
        savings = baseline_cost * (overfeed_pct / 100.0) * ((1 + r) ** yr)
        if d > 0:
            savings /= (1 + d) ** yr
        if cost_mode == "Annual subscription":
            fb_cost = flocbot_annual
        else:
            fb_cost = flocbot_upfront if yr == 0 else 0.0
        net = savings - fb_cost
        cumulative += net
        rows.append({
            "Year": yr + 1,
            "Savings": savings,
            "FlocBot Cost": fb_cost,
            "Net": net,
            "Cumulative": cumulative,
        })
    return pd.DataFrame(rows)


def build_sensitivity_data(baseline_cost, cost_mode, flocbot_annual,
                           flocbot_upfront, escalation_pct, discount_rate_pct):
    rows = []
    for pct in SENSITIVITY_PCTS:
        annual_sav = compute_annual_savings(baseline_cost, pct)
        _, _, net_5yr = compute_5yr_net(
            baseline_cost, pct, cost_mode,
            flocbot_annual, flocbot_upfront,
            escalation_pct, discount_rate_pct,
        )
        rows.append({
            "Overdosing Reduction (%)": pct,
            "Annual Savings": annual_sav,
            "5-Year Net Savings": max(net_5yr, 0.0),
        })
    return pd.DataFrame(rows)


def build_sensitivity_table(sens_df):
    display = sens_df.copy()
    display["Overdosing Reduction (%)"] = display["Overdosing Reduction (%)"].apply(lambda x: f"{x}%")
    display["Annual Savings"] = display["Annual Savings"].apply(lambda x: f"${x:,.0f}")
    display["5-Year Net Savings"] = display["5-Year Net Savings"].apply(lambda x: f"${x:,.0f}")
    return display


# ---------------------------------------------------------------------------
# Page config & CSS
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="FlocBot ROI Estimator",
    page_icon="\U0001f9ea",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── UptimeRobot keyword marker (hidden HTML, renders on every load) ─────
st.markdown("<!-- APP_READY_FLOCBOT_ROI -->", unsafe_allow_html=True)

st.markdown("""
<style>
/* ---- Global ---- */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f1b2d 0%, #1a2940 100%);
}
section[data-testid="stSidebar"] * {
    color: #e2e8f0 !important;
}
section[data-testid="stSidebar"] label {
    color: #94a3b8 !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
section[data-testid="stSidebar"] .stNumberInput input,
section[data-testid="stSidebar"] .stSelectbox > div > div,
section[data-testid="stSidebar"] .stTextInput input {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    color: #f3f4f6 !important;
    border-radius: 6px !important;
}
section[data-testid="stSidebar"] .stTextInput input::placeholder {
    color: #9ca3af !important;
}
section[data-testid="stSidebar"] hr {
    border-color: #334155 !important;
}

/* ---- KPI cards ---- */
div[data-testid="stHorizontalBlock"] div.kpi-card {
    border-radius: 10px;
    padding: 0;
}
.kpi-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 1.1rem 1.2rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    text-align: center;
}
.kpi-card .kpi-label {
    font-size: 0.78rem;
    font-weight: 600;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.3rem;
}
.kpi-card .kpi-value {
    font-size: 1.65rem;
    font-weight: 700;
    color: #0f172a;
    line-height: 1.2;
}
.kpi-card .kpi-sub {
    font-size: 0.72rem;
    color: #94a3b8;
    margin-top: 0.15rem;
}

/* ---- ROI banner ---- */
.roi-banner {
    border-radius: 8px;
    padding: 0.85rem 1.2rem;
    font-size: 0.92rem;
    font-weight: 500;
    margin: 0.6rem 0 1rem 0;
    line-height: 1.5;
}
.roi-strong  { background: #ecfdf5; border-left: 4px solid #10b981; color: #065f46; }
.roi-ok      { background: #eff6ff; border-left: 4px solid #3b82f6; color: #1e3a5f; }
.roi-weak    { background: #fff7ed; border-left: 4px solid #f59e0b; color: #78350f; }

/* ---- Section headers ---- */
.section-hdr {
    font-size: 1.05rem;
    font-weight: 700;
    color: #1e293b;
    margin: 1.4rem 0 0.5rem 0;
    padding-bottom: 0.3rem;
    border-bottom: 2px solid #e2e8f0;
}

/* Sidebar scenario buttons */
section[data-testid="stSidebar"] button[kind="secondary"] {
    background: #1e293b !important;
    border: 1px solid #475569 !important;
    color: #e2e8f0 !important;
    border-radius: 6px !important;
    font-size: 0.8rem !important;
    font-weight: 600 !important;
    padding: 0.35rem 0 !important;
}
section[data-testid="stSidebar"] button[kind="secondary"]:hover {
    background: #334155 !important;
    border-color: #60a5fa !important;
}

/* ---- Misc ---- */
.stDataFrame { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — all inputs
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## FlocBot ROI Estimator")
    st.caption(f"{APP_VERSION}")

    # Reset defaults
    if st.button("Reset defaults", use_container_width=True):
        st.session_state["overfeed_input"] = 0.0
        for k in list(st.session_state.keys()):
            if k != "overfeed_input":
                del st.session_state[k]
        st.rerun()

    # Facility name
    facility_name = st.text_input(
        "Facility name (optional)",
        value="",
        help="Appears on the PDF export if provided.",
    )

    # Quick scenarios
    st.markdown("---")
    st.markdown("**Quick Scenarios**")
    sc = st.columns(3)
    with sc[0]:
        if st.button("5%", key="s5", help="Conservative", use_container_width=True):
            st.session_state["overfeed_input"] = 5.0
    with sc[1]:
        if st.button("10%", key="s10", help="Moderate", use_container_width=True):
            st.session_state["overfeed_input"] = 10.0
    with sc[2]:
        if st.button("15%", key="s15", help="Aggressive", use_container_width=True):
            st.session_state["overfeed_input"] = 15.0

    st.markdown("---")

    # Baseline method
    st.markdown("**Baseline Chemical Cost**")
    baseline_method = st.radio(
        "Method",
        ["Annual spend (recommended)", "Calculate from dose"],
        label_visibility="collapsed",
        help="Choose the most accurate method you have data for.",
    )
    use_spend = baseline_method.startswith("Annual spend")

    if use_spend:
        annual_spend = st.number_input(
            "Annual coagulant spend ($/yr)",
            min_value=0.0, value=0.0, step=10000.0, format="%.0f",
            help="Your plant's total annual coagulant expenditure.",
        )
        flow_mgd = 0.0; coagulant_type = ""; unit_cost = 0.0; current_dose = 0.0
    else:
        annual_spend = 0.0
        flow_mgd = st.number_input(
            "Flow (MGD)", min_value=0.0, value=1.0, step=0.1, format="%.2f",
            help="Average daily flow in million gallons per day.",
        )
        coagulant_type = st.selectbox(
            "Coagulant type",
            ["Alum", "Ferric Chloride", "PAC (Polyaluminum Chloride)", "ACH", "Other"],
        )
        unit_cost = st.number_input(
            "Unit cost ($/lb)", min_value=0.0, value=0.15, step=0.01, format="%.4f",
            help="Cost per pound of coagulant as delivered.",
        )
        current_dose = st.number_input(
            "Current dose (mg/L)", min_value=0.0, value=30.0, step=1.0, format="%.1f",
        )

    st.markdown("---")

    if "overfeed_input" not in st.session_state:
        st.session_state["overfeed_input"] = 0.0
    overfeed_pct = st.number_input(
        "Overdosing (%)",
        min_value=0.0, max_value=100.0,
        step=0.5, format="%.1f",
        help="Estimated % of current coagulant use that is overdosing.",
        key="overfeed_input",
    )

    st.markdown("---")
    st.markdown("**FlocBot Cost**")
    cost_mode = st.radio(
        "Cost basis",
        ["Upfront purchase", "Annual subscription"],
        horizontal=True,
    )
    if cost_mode == "Upfront purchase":
        flocbot_upfront = st.number_input(
            "Upfront cost ($)", min_value=0.0, value=50000.0, step=1000.0, format="%.0f",
        )
        flocbot_annual = 0.0
    else:
        flocbot_annual = st.number_input(
            "Annual cost ($/yr)", min_value=0.0, value=15000.0, step=1000.0, format="%.0f",
        )
        flocbot_upfront = 0.0

    # Advanced
    st.markdown("---")
    with st.expander("Advanced modeling"):
        escalation_pct = st.number_input(
            "Chemical escalation (%/yr)", min_value=0.0, max_value=50.0,
            value=6.0, step=0.5, format="%.1f",
            help="Expected annual increase in chemical prices (~6% industry avg).",
        )
        discount_rate_pct = st.number_input(
            "Discount rate (%)", min_value=0.0, max_value=50.0,
            value=0.0, step=0.5, format="%.1f",
            help="If > 0, future savings are discounted to present value (NPV).",
        )
        operating_days = st.number_input(
            "Operating days/year", min_value=1, max_value=366, value=365, step=1,
        )

# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

lbs_per_day = 0.0
lbs_per_year = 0.0

# Determine whether baseline inputs are ready
inputs_ready = True
missing_inputs = []

if use_spend:
    baseline_cost = annual_spend
    baseline_label = "provided"
    if annual_spend <= 0:
        inputs_ready = False
        missing_inputs.append("Annual coagulant spend")
else:
    if flow_mgd <= 0:
        inputs_ready = False
        missing_inputs.append("Flow (MGD)")
    if unit_cost <= 0:
        inputs_ready = False
        missing_inputs.append("Unit cost ($/lb)")
    if current_dose <= 0:
        inputs_ready = False
        missing_inputs.append("Current dose (mg/L)")
    baseline_cost, lbs_per_day, lbs_per_year = compute_baseline_from_dose(
        current_dose, flow_mgd, unit_cost, operating_days,
    )
    baseline_label = "calculated"

annual_sav = compute_annual_savings(baseline_cost, overfeed_pct)
simple_payback_yrs = compute_payback_years(annual_sav, cost_mode, flocbot_annual, flocbot_upfront)
payback_yrs = compute_payback_cashflow(
    baseline_cost, overfeed_pct, cost_mode,
    flocbot_annual, flocbot_upfront,
    escalation_pct, discount_rate_pct,
)
total_sav_5yr, total_flocbot_5yr, net_5yr_raw = compute_5yr_net(
    baseline_cost, overfeed_pct, cost_mode,
    flocbot_annual, flocbot_upfront,
    escalation_pct, discount_rate_pct,
)
net_5yr = max(net_5yr_raw, 0.0)
break_even = compute_break_even_pct(baseline_cost, cost_mode, flocbot_annual, flocbot_upfront)
cashflow_df = compute_yearly_cashflows(
    baseline_cost, overfeed_pct, cost_mode,
    flocbot_annual, flocbot_upfront,
    escalation_pct, discount_rate_pct,
)
sens_raw = build_sensitivity_data(
    baseline_cost, cost_mode, flocbot_annual, flocbot_upfront,
    escalation_pct, discount_rate_pct,
)

# ---------------------------------------------------------------------------
# Main area — results dashboard
# ---------------------------------------------------------------------------

# Header
st.markdown(
    '<h1 style="margin-bottom:0.1rem;">FlocBot ROI Estimator</h1>',
    unsafe_allow_html=True,
)
if facility_name:
    st.markdown(
        f'<p style="font-size:0.9rem;color:#4a5568;margin:0 0 0.3rem 0;">Facility: {facility_name}</p>',
        unsafe_allow_html=True,
    )
st.caption("Coagulant optimization savings calculator  \u00b7  Adjust inputs in the sidebar")

# ---- Gate: show nothing until all inputs are provided ----
prompts = []
if not inputs_ready:
    prompts.append("Enter your **" + "**, **".join(missing_inputs) + "** in the sidebar")
if overfeed_pct <= 0:
    prompts.append("Select a **Quick Scenario** or set the **Overdosing %**")
if prompts:
    st.info("To generate results: " + " and ".join(prompts) + ".")
    st.stop()

# ---- KPI cards ----
def kpi_card(label, value, sub=""):
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return f"""<div class="kpi-card">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        {sub_html}
    </div>"""

payback_str = format_payback(payback_yrs)
simple_payback_str = format_payback(simple_payback_yrs)
net_label = "5-Year Net Savings (NPV)" if discount_rate_pct > 0 else "5-Year Net Savings"
src_note = "from plant records" if baseline_label == "provided" else "calculated from dose"

# Build payback subtitle
payback_sub = f"Simple (Yr 1): {simple_payback_str}"

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown(kpi_card("Annual Chemical Cost", f"${baseline_cost:,.0f}", src_note), unsafe_allow_html=True)
with k2:
    st.markdown(kpi_card("Est. Annual Savings", f"${annual_sav:,.0f}", f"at {overfeed_pct:.1f}% reduction"), unsafe_allow_html=True)
with k3:
    st.markdown(
        kpi_card(
            'Payback Period <span title="Cashflow payback accounts for escalation &amp; discount rate; simple payback uses Year 1 savings only." style="cursor:help;color:#94a3b8;font-size:0.7rem;">&#9432;</span>',
            payback_str,
            payback_sub,
        ),
        unsafe_allow_html=True,
    )
with k4:
    st.markdown(kpi_card(net_label, f"${net_5yr:,.0f}", f"{escalation_pct:.1f}% escalation" if escalation_pct > 0 else "no escalation"), unsafe_allow_html=True)

# ---- ROI status banner ----
esc_blurb = f" with {escalation_pct:.1f}% chemical escalation" if escalation_pct > 0 else ""

if payback_yrs is not None and payback_yrs <= 2:
    roi_class = "roi-strong"
    roi_text = (
        f"<strong>Strong ROI:</strong> estimated payback ~{format_payback(payback_yrs)} "
        f"at {overfeed_pct:.1f}% reduction{esc_blurb}."
    )
elif payback_yrs is not None and payback_yrs <= 4:
    roi_class = "roi-strong"
    roi_text = (
        f"<strong>Solid ROI:</strong> estimated payback ~{format_payback(payback_yrs)} "
        f"at {overfeed_pct:.1f}% reduction{esc_blurb}."
    )
elif payback_yrs is not None:
    roi_class = "roi-ok"
    roi_text = (
        f"<strong>Positive ROI:</strong> estimated payback ~{format_payback(payback_yrs)} "
        f"at {overfeed_pct:.1f}% reduction{esc_blurb}. "
        f"Higher overdosing reductions accelerate payback."
    )
else:
    roi_class = "roi-ok"
    roi_text = (
        f"<strong>Savings opportunity:</strong> FlocBot delivers <strong>${annual_sav:,.0f}/yr</strong> in chemical savings."
    )

if break_even is not None:
    be_qualifier = " in Year 1" if cost_mode == "Upfront purchase" else ""
    roi_text += f"<br>Break-even overdosing reduction{be_qualifier}: <strong>{break_even:.1f}%</strong>"

st.markdown(f'<div class="roi-banner {roi_class}">{roi_text}</div>', unsafe_allow_html=True)

# ---- Charts row ----
st.markdown('<div class="section-hdr">5-Year Cashflow Projection</div>', unsafe_allow_html=True)

chart_left, chart_right = st.columns([3, 2])

with chart_left:
    # Bar + line combo via Altair
    cf = cashflow_df.copy()
    cf["Year"] = cf["Year"].astype(str)

    bars = alt.Chart(cf).mark_bar(
        cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=36
    ).encode(
        x=alt.X("Year:N", axis=alt.Axis(labelAngle=0, title="Year")),
        y=alt.Y("Net:Q", title="Dollars ($)"),
        color=alt.condition(
            alt.datum.Net >= 0,
            alt.value("#10b981"),
            alt.value("#f59e0b"),
        ),
        tooltip=[
            alt.Tooltip("Year:N"),
            alt.Tooltip("Savings:Q", format="$,.0f", title="Gross Savings"),
            alt.Tooltip("FlocBot Cost:Q", format="$,.0f"),
            alt.Tooltip("Net:Q", format="$,.0f", title="Net Savings"),
            alt.Tooltip("Cumulative:Q", format="$,.0f"),
        ],
    )

    line = alt.Chart(cf).mark_line(
        point=alt.OverlayMarkDef(filled=True, size=50),
        strokeWidth=2.5,
        color="#3b82f6",
    ).encode(
        x="Year:N",
        y=alt.Y("Cumulative:Q"),
        tooltip=[
            alt.Tooltip("Year:N"),
            alt.Tooltip("Cumulative:Q", format="$,.0f", title="Cumulative Net"),
        ],
    )

    zero_rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        strokeDash=[4, 4], color="#94a3b8"
    ).encode(y="y:Q")

    chart = (bars + line + zero_rule).properties(
        height=320,
    ).configure_axis(
        labelFontSize=11, titleFontSize=12,
    ).configure_view(
        strokeWidth=0,
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption("Bars = annual net savings \u00b7 Line = cumulative net")

with chart_right:
    # Sensitivity chart
    sr = sens_raw.copy()
    sr["pct_str"] = sr["Overdosing Reduction (%)"].apply(lambda x: f"{x}%")

    # Highlight point for selected overfeed
    selected_net = None
    for _, row in sr.iterrows():
        if row["Overdosing Reduction (%)"] == overfeed_pct:
            selected_net = row["5-Year Net Savings"]
            break

    sens_line = alt.Chart(sr).mark_line(
        point=alt.OverlayMarkDef(filled=True, size=50),
        strokeWidth=2.5,
        color="#6366f1",
    ).encode(
        x=alt.X("Overdosing Reduction (%):Q", title="Overdosing Reduction (%)",
                 scale=alt.Scale(domain=[0, 14])),
        y=alt.Y("5-Year Net Savings:Q", title="5-Year Net Savings ($)"),
        tooltip=[
            alt.Tooltip("Overdosing Reduction (%):Q", format=".0f", title="Reduction"),
            alt.Tooltip("5-Year Net Savings:Q", format="$,.0f"),
        ],
    )

    sens_zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        strokeDash=[4, 4], color="#94a3b8"
    ).encode(y="y:Q")

    layers = [sens_line, sens_zero]

    # Add selected-point highlight if it matches a sensitivity row
    if selected_net is not None:
        highlight_df = pd.DataFrame({
            "Overdosing Reduction (%)": [overfeed_pct],
            "5-Year Net Savings": [selected_net],
        })
        highlight = alt.Chart(highlight_df).mark_point(
            size=160, filled=True, color="#ef4444",
        ).encode(
            x="Overdosing Reduction (%):Q",
            y="5-Year Net Savings:Q",
        )
        layers.append(highlight)

    sens_chart = alt.layer(*layers).properties(height=320).configure_axis(
        labelFontSize=11, titleFontSize=12,
    ).configure_view(strokeWidth=0)
    st.altair_chart(sens_chart, use_container_width=True)
    st.caption("Sensitivity across overdosing reduction levels")

# ---- Sensitivity table ----
st.markdown('<div class="section-hdr">Sensitivity Table</div>', unsafe_allow_html=True)
st.dataframe(build_sensitivity_table(sens_raw), hide_index=True, use_container_width=True)

# ---- Calculation details ----
with st.expander("Calculation details"):
    st.markdown(f"**Baseline method:** {baseline_method}")
    if use_spend:
        st.markdown(f"Annual coagulant spend (entered): **${baseline_cost:,.2f}**/yr")
    else:
        st.markdown("**Dose-based calculation:**")
        st.code(
            f"lbs/day  = {current_dose:.1f} mg/L  x  {flow_mgd:.2f} MGD  x  8.34  =  {lbs_per_day:,.1f} lb/day\n"
            f"lbs/year = {lbs_per_day:,.1f} lb/day  x  {operating_days} days  =  {lbs_per_year:,.0f} lb/year\n"
            f"Annual cost = {lbs_per_year:,.0f} lb/year  x  ${unit_cost:.4f}/lb  =  ${baseline_cost:,.2f}",
            language=None,
        )
    st.markdown("---")
    cost_str = f"{baseline_cost:,.0f}"
    pct_str = f"{overfeed_pct:.1f}"
    sav_str = f"{annual_sav:,.0f}"
    st.write(f"**Annual savings** = \\${cost_str} \u00d7 {pct_str}% = \\${sav_str}")
    st.markdown("---")
    esc_note = f", {escalation_pct:.1f}% chemical escalation" if escalation_pct > 0 else ""
    disc_note = f", discounted at {discount_rate_pct:.1f}%" if discount_rate_pct > 0 else ""
    st.markdown(f"**5-year projection{esc_note}{disc_note}:**")
    st.latex(
        r"\text{Net} = \sum_{y=0}^{4} "
        r"\frac{\text{Baseline} \times \text{Overdose\%} \times (1{+}r)^{y}}{(1{+}d)^{y}}"
        r" \;-\; \text{FlocBot cost}"
    )
    det_c1, det_c2, det_c3 = st.columns(3)
    det_c1.metric("Gross Savings (5 yr)", f"${total_sav_5yr:,.0f}")
    det_c2.metric("FlocBot Cost (5 yr)", f"${total_flocbot_5yr:,.0f}")
    det_c3.metric("Net Savings (5 yr)", f"${net_5yr:,.0f}")

# ---- Decision Analysis (MCDA) --------------------------------------------------------
st.markdown('<div class="section-hdr">Decision Analysis</div>', unsafe_allow_html=True)

_ALT_FULL = [
    "Conventional Jar Testing",
    "Streaming Current Monitor Guided Control",
    "Zeta Potential Analyzer Guided Control",
    "FlocBot",
]
_ALT_SHORT = ["Conventional", "Streaming Current", "Zeta Potential", "FlocBot"]
_ALT_FULL_MAP = dict(zip(_ALT_SHORT, _ALT_FULL))

# (category, criterion, default_importance, [conv, sc, zp, fb], definition)
_MCDA_CRITERIA = [
    ("Public Health & Safety", "Treatment reliability", 0.18,
     [3, 4, 4, 5],
     "Consistency of meeting target water quality under varying raw water conditions"),
    ("Public Health & Safety", "Risk reduction from underdosing / operator error", 0.16,
     [2, 4, 4, 5],
     "Ability to reduce risks caused by poor dose selection, delayed adjustments, or operator subjectivity"),
    ("Environmental", "Chemical / sludge reduction", 0.14,
     [2, 3, 4, 5],
     "Likelihood of reducing excess coagulant/polymer use, sludge generation, and disposal burden"),
    ("Environmental", "Resource efficiency / secondary environmental impact", 0.10,
     [3, 3, 4, 4],
     "Energy, water, consumables, and downstream environmental effects"),
    ("Practicality", "Ease of implementation", 0.12,
     [5, 3, 2, 3],
     "Ease of deployment, integration, and everyday use at an operating plant"),
    ("Practicality", "Training and workflow burden", 0.10,
     [4, 3, 2, 3],
     "Staff retraining, workflow disruption, calibration burden, and support needs"),
    ("Practicality", "Data visibility / process control insight", 0.10,
     [2, 4, 4, 5],
     "Quality of real-time or near-real-time information available to support operator decisions"),
    ("Operational Value", "Responsiveness to changing raw water conditions", 0.10,
     [2, 4, 4, 5],
     "Ability to detect or adapt to rapid influent variability and changing treatment conditions"),
]

_MCDA_CAT_LABELS = [
    "Health & Safety", "Health & Safety",
    "Environmental", "Environmental",
    "Practicality", "Practicality", "Practicality",
    "Operational Value",
]
_MCDA_CRIT_LABELS = [
    "Treatment reliability",
    "Underdosing / error risk",
    "Chemical / sludge reduction",
    "Resource efficiency",
    "Ease of implementation",
    "Training / workflow burden",
    "Data visibility / process control",
    "Raw water responsiveness",
]

_mcda_default_df = pd.DataFrame({
    "Category": _MCDA_CAT_LABELS,
    "Criterion": _MCDA_CRIT_LABELS,
    "Importance": [c[2] for c in _MCDA_CRITERIA],
    **{short: [c[3][j] for c in _MCDA_CRITERIA] for j, short in enumerate(_ALT_SHORT)},
})

if "mcda_reset_v" not in st.session_state:
    st.session_state.mcda_reset_v = 0


def _mcda_reset():
    st.session_state.mcda_reset_v += 1


# Placeholder so summary renders above expander but is computed after data_editor
_mcda_summary_slot = st.container()

with st.expander("Expand Decision Analysis (MCDA)", expanded=False):

    # ---- 1. Intro note ----
    st.markdown(
        "This multi-criteria decision analysis (MCDA) complements the ROI results above "
        "by providing a structured comparison of operational, environmental, and "
        "public-health tradeoffs across four dosing and process-control approaches "
        "commonly evaluated in water treatment. All weights and scores are fully editable."
    )
    st.markdown(
        "<span style='font-size:0.82rem;color:#64748b;'>"
        "More reliable coagulation and dosing control can reduce risk of inadequate "
        "treatment performance, which can affect finished water quality delivered to "
        "consumers.</span>",
        unsafe_allow_html=True,
    )

    # ---- 2. Controls row ----
    _hdr1, _hdr2 = st.columns([5, 1])
    with _hdr2:
        st.button("Reset defaults", on_click=_mcda_reset, use_container_width=True)
    with _hdr1:
        st.markdown(
            "<span style='font-size:0.82rem;color:#64748b;'>"
            "<b>Score scale:</b> 1 = Poor &middot; 2 = Below average &middot; "
            "3 = Moderate &middot; 4 = Good &middot; 5 = Excellent<br>"
            "Importance weights should sum to 1.00.</span>",
            unsafe_allow_html=True,
        )

    # ---- 3. Editable comparison table ----
    mcda_edited = st.data_editor(
        _mcda_default_df,
        key=f"mcda_ed_{st.session_state.mcda_reset_v}",
        column_config={
            "Category": st.column_config.TextColumn(width=120),
            "Criterion": st.column_config.TextColumn(width=230),
            "Importance": st.column_config.NumberColumn(
                min_value=0.00, max_value=1.00, step=0.01, format="%.2f",
                help="Decimal weight (0.00 - 1.00). Weights should sum to 1.00.",
            ),
            **{
                short: st.column_config.NumberColumn(
                    min_value=1, max_value=5, step=1,
                    help="1 = Poor ... 5 = Excellent",
                )
                for short in _ALT_SHORT
            },
        },
        disabled=["Category", "Criterion"],
        hide_index=True,
        use_container_width=True,
    )

    # ---- Importance total ----
    _w = mcda_edited["Importance"].values.astype(float)
    _w_total = float(_w.sum())
    _w_valid = abs(_w_total - 1.0) < 0.005

    if _w_valid:
        st.markdown(
            f"<span style='font-size:0.82rem;color:#64748b;'>"
            f"<b>Importance total:</b> {_w_total:.2f}</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<span style='font-size:0.82rem;color:#e11d48;'>"
            f"<b>Importance total:</b> {_w_total:.2f} &mdash; "
            f"Importance must sum to 1.00 to calculate results.</span>",
            unsafe_allow_html=True,
        )

    # ---- 4. Criterion definitions ----
    st.markdown(
        "<details style='margin-top:0.3rem;'><summary style='font-size:0.82rem;"
        "color:#64748b;cursor:pointer;'>Criterion definitions</summary>"
        "<ul style='font-size:0.8rem;color:#475569;margin-top:0.4rem;'>"
        + "".join(f"<li><b>{c[1]}</b>: {c[4]}</li>" for c in _MCDA_CRITERIA)
        + "</ul></details>",
        unsafe_allow_html=True,
    )

    # ---- Compute MCDA scores (only when weights are valid) ----
    if _w_valid:
        mcda_totals = {}
        for short in _ALT_SHORT:
            _s = mcda_edited[short].values.astype(float)
            mcda_totals[short] = float((_w * _s).sum())

        mcda_ranked = sorted(mcda_totals.items(), key=lambda x: x[1], reverse=True)
        mcda_best_short, mcda_best_score = mcda_ranked[0]
        mcda_second_short, mcda_second_score = mcda_ranked[1]
        mcda_gap = mcda_best_score - mcda_second_score
        mcda_best_full = _ALT_FULL_MAP[mcda_best_short]

        # ---- 5. Score summary cards ----
        st.markdown("---")
        _rc = st.columns(4)
        for i, (name, score) in enumerate(mcda_ranked):
            _rc[i].metric(f"#{i + 1} {name}", f"{score:.2f}")

        # ---- 6. Recommendation & interpretation ----
        st.markdown(f"**Recommendation:** {mcda_best_full}")

        # Dynamic interpretation - strengths and tradeoffs
        _crit_labels = [c[1].lower() for c in _MCDA_CRITERIA]
        _strengths = []
        _tradeoffs = []
        for ci in range(len(_MCDA_CRITERIA)):
            w_val = float(mcda_edited[mcda_best_short].iloc[ci])
            others = {s: float(mcda_edited[s].iloc[ci]) for s in _ALT_SHORT if s != mcda_best_short}
            max_other = max(others.values())
            if w_val > max_other:
                _strengths.append((_w[ci] * (w_val - max_other), _crit_labels[ci]))
            elif w_val < max_other:
                better = [s for s, v in others.items() if v == max_other]
                _tradeoffs.append((_w[ci] * (max_other - w_val), _crit_labels[ci], better))
        _strengths.sort(reverse=True)
        _tradeoffs.sort(reverse=True)

        _str_names = [s[1] for s in _strengths[:3]]
        if _str_names:
            if len(_str_names) == 1:
                _str_phrase = _str_names[0]
            elif len(_str_names) == 2:
                _str_phrase = f"{_str_names[0]} and {_str_names[1]}"
            else:
                _str_phrase = f"{', '.join(_str_names[:-1])}, and {_str_names[-1]}"
            mcda_interp = (
                f"{mcda_best_full} ranks highest overall due to stronger "
                f"{_str_phrase}."
            )
        else:
            mcda_interp = f"{mcda_best_full} ranks highest overall across the weighted criteria."

        if _tradeoffs:
            _tw_alts = _tradeoffs[0][2]
            _tw_crit = _tradeoffs[0][1]
            _tw_who = " and ".join(_ALT_FULL_MAP[a].lower() for a in _tw_alts)
            mcda_interp += (
                f" Alternatives such as {_tw_who} are easier to implement but"
                f" provide less consistency and weaker decision support under"
                f" changing conditions."
            )

        st.info(mcda_interp)
    else:
        # Weights invalid - show placeholder
        mcda_ranked = [(s, 0.0) for s in _ALT_SHORT]
        mcda_best_short = _ALT_SHORT[-1]
        mcda_best_score = 0.0
        mcda_second_short = _ALT_SHORT[0]
        mcda_gap = 0.0
        mcda_interp = ""

# ---- Summary row (always visible above expander) ----
with _mcda_summary_slot:
    if _w_valid:
        _s1, _s2, _s3 = st.columns([1.2, 1.4, 3.4])
        _s1.markdown(f"**Recommended:** {mcda_best_short}")
        _s2.markdown(f"**Score:** {mcda_best_score:.2f}")
        _s3.markdown(f"*{mcda_interp}*")
    else:
        st.markdown(
            "<span style='font-size:0.85rem;color:#e11d48;'>"
            "Adjust Importance weights to sum to 1.00 to see MCDA results.</span>",
            unsafe_allow_html=True,
        )

# ---- PDF Download ----
st.markdown('<div class="section-hdr">Export</div>', unsafe_allow_html=True)


def generate_pdf():
    pdf = FPDF()
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 14, "FlocBot ROI Estimate", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120, 120, 120)
    pdf_subtitle = f"{APP_VERSION}  |  Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    if facility_name:
        pdf_subtitle = f"{facility_name}  |  {pdf_subtitle}"
    pdf.cell(0, 6, pdf_subtitle, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    def section_header(title):
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)

    def add_row(label, value):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(95, 7, label, new_x="RIGHT")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    # Inputs
    section_header("Inputs")
    add_row("Baseline Method:", "Annual spend" if use_spend else "Flow + dose + unit cost")
    if use_spend:
        add_row("Annual Coagulant Spend:", f"${annual_spend:,.0f}")
    else:
        add_row("Flow:", f"{flow_mgd:.2f} MGD")
        add_row("Coagulant Type:", coagulant_type)
        add_row("Unit Cost:", f"${unit_cost:.4f}/lb")
        add_row("Current Dose:", f"{current_dose:.1f} mg/L")
        add_row("Operating Days:", str(operating_days))
    add_row("Overdosing:", f"{overfeed_pct:.1f}%")
    if cost_mode == "Annual subscription":
        add_row("FlocBot Annual Cost:", f"${flocbot_annual:,.0f}")
    else:
        add_row("FlocBot Upfront Cost:", f"${flocbot_upfront:,.0f}")
    if escalation_pct > 0:
        add_row("Chemical Escalation:", f"{escalation_pct:.1f}%/yr")
    if discount_rate_pct > 0:
        add_row("Discount Rate:", f"{discount_rate_pct:.1f}%")

    # Results
    pdf.ln(4)
    section_header("Results")
    add_row("Annual Chemical Cost:", f"${baseline_cost:,.0f}")
    add_row("Estimated Annual Savings:", f"${annual_sav:,.0f}")
    add_row("Payback Period:", format_payback(payback_yrs))
    net_lbl = "5-Year Net Savings (NPV):" if discount_rate_pct > 0 else "5-Year Net Savings:"
    add_row(net_lbl, f"${net_5yr:,.0f}")
    if break_even is not None:
        be_lbl = "Break-even Overdosing (Year 1):" if cost_mode == "Upfront purchase" else "Break-even Overdosing:"
        add_row(be_lbl, f"{break_even:.1f}%")

    # Cashflow table
    pdf.ln(4)
    section_header("5-Year Cashflow")
    pdf.set_font("Helvetica", "B", 9)
    cw = [18, 38, 38, 38, 42]
    cheaders = ["Year", "Savings", "FlocBot Cost", "Net", "Cumulative"]
    for i, h in enumerate(cheaders):
        pdf.cell(cw[i], 7, h, border=1, align="C", new_x="RIGHT")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for _, row in cashflow_df.iterrows():
        pdf.cell(cw[0], 7, str(int(row["Year"])), border=1, align="C", new_x="RIGHT")
        pdf.cell(cw[1], 7, f"${row['Savings']:,.0f}", border=1, align="R", new_x="RIGHT")
        pdf.cell(cw[2], 7, f"${row['FlocBot Cost']:,.0f}", border=1, align="R", new_x="RIGHT")
        pdf.cell(cw[3], 7, f"${row['Net']:,.0f}", border=1, align="R", new_x="RIGHT")
        pdf.cell(cw[4], 7, f"${row['Cumulative']:,.0f}", border=1, align="R", new_x="RIGHT")
        pdf.ln()

    # Sensitivity table
    pdf.ln(4)
    section_header("Sensitivity (Overdosing Reduction)")
    pdf.set_font("Helvetica", "B", 9)
    col_w = [40, 50, 50]
    headers = ["Overdosing %", "Annual Savings", "5-Yr Net Savings"]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, align="C", new_x="RIGHT")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for _, row in sens_raw.iterrows():
        pdf.cell(col_w[0], 7, f"{row['Overdosing Reduction (%)']:.0f}%", border=1, align="C", new_x="RIGHT")
        pdf.cell(col_w[1], 7, f"${row['Annual Savings']:,.0f}", border=1, align="R", new_x="RIGHT")
        pdf.cell(col_w[2], 7, f"${row['5-Year Net Savings']:,.0f}", border=1, align="R", new_x="RIGHT")
        pdf.ln()

    # Decision Analysis (MCDA)
    pdf.add_page()
    section_header("Decision Analysis (MCDA)")
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5,
        "Multi-Criteria Decision Analysis comparing four dosing/control alternatives "
        "across weighted operational, environmental, and public-health criteria.",
        new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # MCDA ranking
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Ranking", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for rank_i, (r_name, r_score) in enumerate(mcda_ranked):
        pdf.cell(0, 6,
            f"  #{rank_i + 1}  {_ALT_FULL_MAP[r_name]}  -  {r_score:.2f}",
            new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6,
        f"Score gap (1st vs 2nd): +{mcda_gap:.2f}",
        new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # MCDA scores table
    pdf.set_font("Helvetica", "B", 8)
    mcda_cw = [55, 16, 25, 25, 25, 25]
    mcda_hdrs = ["Criterion", "Wt", "Conv.", "Stream.", "Zeta", "FlocBot"]
    for i, h in enumerate(mcda_hdrs):
        pdf.cell(mcda_cw[i], 6, h, border=1, align="C", new_x="RIGHT")
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for ci in range(len(_MCDA_CRITERIA)):
        pdf.cell(mcda_cw[0], 6, _MCDA_CRITERIA[ci][1][:38], border=1, new_x="RIGHT")
        pdf.cell(mcda_cw[1], 6, f"{_w[ci]:.2f}", border=1, align="C", new_x="RIGHT")
        for j in range(4):
            pdf.cell(mcda_cw[2 + j], 6,
                str(int(mcda_edited[_ALT_SHORT[j]].iloc[ci])),
                border=1, align="C", new_x="RIGHT")
        pdf.ln()
    pdf.ln(3)

    # Recommendation
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Recommendation", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    _interp_safe = mcda_interp.encode("latin-1", "replace").decode("latin-1")
    pdf.multi_cell(0, 5, _interp_safe, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 6, "Generated by FlocBot ROI Estimator", align="C")

    return bytes(pdf.output())


exp_c1, exp_c2 = st.columns(2)
with exp_c1:
    pdf_bytes = generate_pdf()
    st.download_button(
        label="Download PDF Summary",
        data=pdf_bytes,
        file_name="flocbot_roi_estimate.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
with exp_c2:
    summary_text = (
        f"FlocBot ROI Estimate ({datetime.now().strftime('%Y-%m-%d')})\n"
        f"{'='*45}\n"
        f"Annual Chemical Cost: ${baseline_cost:,.0f} ({baseline_label})\n"
        f"Overdosing: {overfeed_pct:.1f}%\n"
        f"Estimated Annual Savings: ${annual_sav:,.0f}\n"
        f"Payback Period: {format_payback(payback_yrs)}\n"
        f"5-Year Net Savings: ${net_5yr:,.0f}\n"
    )
    if break_even is not None:
        summary_text += f"Break-even Overdosing: {break_even:.1f}%\n"
    summary_text += f"\nDecision Analysis (MCDA)\n{'-'*45}\n"
    for rank_i, (r_name, r_score) in enumerate(mcda_ranked):
        summary_text += f"  #{rank_i + 1}  {_ALT_FULL_MAP[r_name]}  -  {r_score:.2f}\n"
    summary_text += f"Score gap (1st vs 2nd): +{mcda_gap:.2f}\n"
    summary_text += f"Recommendation: {mcda_interp}\n"
    st.download_button(
        label="Copy Summary (text)",
        data=summary_text,
        file_name="flocbot_roi_summary.txt",
        mime="text/plain",
        use_container_width=True,
    )
