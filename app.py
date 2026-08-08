import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os
import re
import io
import numpy as np

st.set_page_config(layout="wide", page_title="Pro Portfolio Tracker")

# -------------------- SIMPLE PASSWORD LOGIN --------------------
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
        return False
    else:
        return True

if not check_password():
    st.stop()
# -------------------- END LOGIN --------------------

# --- FILE PATHS ---
HOLDINGS_FILE = "my_holdings.csv"
HISTORY_FILE = "portfolio_history.csv"

# --- HELPERS ---
def load_holdings():
    if os.path.exists(HOLDINGS_FILE):
        return pd.read_csv(HOLDINGS_FILE)
    return pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"])

def save_holdings(df):
    df.to_csv(HOLDINGS_FILE, index=False)

def convert_option_to_occ(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if re.match(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$", symbol):
        return symbol

    patterns = [
        r"([A-Z]+)\s+(\d{1,2})/(\d{1,2})/(\d{2,4})\s+([\d.]+)\s*([CP])",
        r"([A-Z]+)\s+(\d{1,2})-(\d{1,2})-(\d{2,4})\s+([\d.]+)\s*([CP])",
    ]
    for pat in patterns:
        match = re.search(pat, symbol)
        if match:
            root, month, day, year, strike, cp = match.groups()
            year = int(year)
            if year < 100:
                year += 2000
            yy = f"{year % 100:02d}"
            mm = f"{int(month):02d}"
            dd = f"{int(day):02d}"
            strike_int = int(float(strike) * 1000)
            strike_str = f"{strike_int:08d}"
            return f"{root}{yy}{mm}{dd}{cp}{strike_str}"
    return symbol

def get_mark_price(symbol, asset_type):
    if asset_type == "Cash":
        return 1.0
    try:
        if asset_type == "Option":
            symbol = convert_option_to_occ(symbol)
        ticker = yf.Ticker(symbol)
        if asset_type == "Option":
            info = ticker.info
            last = info.get("regularMarketPrice") or info.get("previousClose") or 0
            bid = info.get("bid") or 0
            ask = info.get("ask") or 0
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return float(last) if last else 0.0
        else:
            return float(ticker.fast_info.last_price)
    except Exception:
        return 0.0

def update_history(total_val):
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(HISTORY_FILE):
        hist_df = pd.read_csv(HISTORY_FILE)
    else:
        hist_df = pd.DataFrame(columns=["Date", "Value"])

    if today in hist_df["Date"].values:
        hist_df.loc[hist_df["Date"] == today, "Value"] = total_val
    else:
        new_entry = pd.DataFrame([{"Date": today, "Value": total_val}])
        hist_df = pd.concat([hist_df, new_entry], ignore_index=True)

    hist_df.to_csv(HISTORY_FILE, index=False)
    return hist_df

def import_brokerage_csv(uploaded_file):
    content = uploaded_file.getvalue().decode("utf-8")
    lines = content.splitlines()

    # Find the real header row
    header_idx = None
    for i, line in enumerate(lines):
        if "Symbol" in line and ("Qty" in line or "Quantity" in line):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find a header row containing 'Symbol' and 'Qty/Quantity'")

    df = pd.read_csv(io.StringIO(content), skiprows=header_idx)
    
    # Clean column names (remove extra spaces and make lower for matching)
    df.columns = [str(c).strip() for c in df.columns]
    cols_lower = {c.lower(): c for c in df.columns}

    def find_col(*keywords):
        for key, original in cols_lower.items():
            if any(kw in key for kw in keywords):
                return original
        return None

    symbol_col = find_col("symbol")
    qty_col    = find_col("qty", "quantity")
    cost_col   = find_col("cost basis", "cost")
    type_col   = find_col("asset type", "type")
    mkt_col    = find_col("mkt val", "market value", "mkt")

    if not symbol_col or not qty_col or not cost_col:
        raise ValueError(
            f"Missing required columns.\n"
            f"Found columns: {list(df.columns)}\n"
            f"Symbol: {symbol_col}, Qty: {qty_col}, Cost: {cost_col}"
        )

    records = []

    for _, row in df.iterrows():
        symbol = str(row[symbol_col]).strip()
        if not symbol or symbol.lower() in ["nan", "positions total", "--", ""]:
            continue

        asset_type_raw = str(row[type_col]).lower() if type_col else ""
        desc = str(row.get("Description", "")).lower()

        if "cash" in asset_type_raw or "cash" in symbol.lower() or "cash" in desc:
            h_type = "Cash"
        elif "option" in asset_type_raw:
            h_type = "Option"
        elif "etf" in asset_type_raw or "closed end" in asset_type_raw:
            h_type = "ETF"
        else:
            h_type = "Stock"

        if h_type == "Cash":
            raw_val = str(row[mkt_col]) if mkt_col else "0"
            qty = float(str(raw_val).replace("$", "").replace(",", "").strip() or 0)
            avg_cost = 1.0
            symbol = "CASH"
        else:
            qty_str = str(row[qty_col]).replace(",", "").strip()
            try:
                qty = float(qty_str)
            except:
                continue
            if qty == 0:
                continue

            cost_str = str(row[cost_col]).replace("$", "").replace(",", "").strip()
            try:
                total_cost = float(cost_str)
            except:
                total_cost = 0.0

            if h_type == "Option":
                symbol = convert_option_to_occ(symbol)
                avg_cost = total_cost / (qty * 100) if qty > 0 else 0
            else:
                avg_cost = total_cost / qty if qty > 0 else 0

        records.append({
            "Symbol": symbol,
            "Type": h_type,
            "Quantity": qty,
            "Average Cost": round(avg_cost, 4)
        })

    if not records:
        raise ValueError("No valid positions found in the file")

    return pd.DataFrame(records)

def merge_holdings(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Add new positions to existing ones. Same symbol → sum quantities + weighted average cost."""
    if existing.empty:
        return new.copy()

    combined = pd.concat([existing, new], ignore_index=True)

    # Calculate total cost for weighted average
    combined["TotalCost"] = combined["Quantity"] * combined["Average Cost"]

    merged = combined.groupby(["Symbol", "Type"], as_index=False).agg({
        "Quantity": "sum",
        "TotalCost": "sum"
    })

    # Recalculate average cost
    merged["Average Cost"] = merged.apply(
        lambda row: row["TotalCost"] / row["Quantity"] if row["Quantity"] != 0 else 0,
        axis=1
    )

    merged = merged.drop(columns=["TotalCost"])
    merged["Average Cost"] = merged["Average Cost"].round(4)

    return merged

# --- SIDEBAR ---
st.sidebar.header("📥 Portfolio Management")

with st.sidebar.expander("➕ Add Asset", expanded=False):
    with st.form("add_asset"):
        a_type = st.selectbox("Asset Type", ["Stock", "ETF", "Option", "Cash"])
        if a_type == "Cash":
            st.caption("Quantity = dollar amount")
            default_sym = "CASH"
            default_cost = 1.0
        else:
            default_sym = ""
            default_cost = 0.0

        sym = st.text_input("Symbol", value=default_sym).upper().strip()
        qty = st.number_input("Quantity", min_value=0.0, step=1.0, value=0.0)
        cost = st.number_input("Avg Cost (per share/contract)", min_value=0.0, value=default_cost)

        if st.form_submit_button("Add to Portfolio"):
            if a_type != "Cash" and not sym:
                st.error("Symbol required")
            else:
                if a_type == "Cash":
                    sym = "CASH"
                    cost = 1.0
                if a_type == "Option":
                    sym = convert_option_to_occ(sym)

                df = load_holdings()
                new_row = pd.DataFrame([{
                    "Symbol": sym, "Type": a_type,
                    "Quantity": qty, "Average Cost": cost
                }])
                save_holdings(merge_holdings(df, new_row))
                st.success(f"Added {sym}")
                st.rerun()

with st.sidebar.expander("📂 Upload Brokerage CSV", expanded=True):
    file = st.file_uploader("Upload CSV (Fidelity, Schwab, etc.)", type=["csv"], key="brokerage_uploader")

    if file is not None:
        st.write(f"Selected: **{file.name}**")
        if st.button("🚀 Import & Add to Portfolio", type="primary", use_container_width=True):
            try:
                df_new = import_brokerage_csv(file)
                existing = load_holdings()
                merged = merge_holdings(existing, df_new)
                save_holdings(merged)
                st.success(f"Added {len(df_new)} positions (total now {len(merged)})")
                st.dataframe(df_new, use_container_width=True)
                st.balloons()
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")
                st.exception(e)

holdings_preview = load_holdings()
if not holdings_preview.empty:
    with st.sidebar.expander("🗑️ Delete Holding"):
        to_delete = st.selectbox("Select symbol to delete", holdings_preview["Symbol"].tolist())
        if st.button("Delete selected", type="primary"):
            holdings_preview = holdings_preview[holdings_preview["Symbol"] != to_delete]
            save_holdings(holdings_preview)
            st.success(f"Deleted {to_delete}")
            st.rerun()

if st.sidebar.button("🗑️ Reset All Data", type="secondary"):
    for f in [HOLDINGS_FILE, HISTORY_FILE]:
        if os.path.exists(f):
            os.remove(f)
    st.rerun()

# --- MAIN DASHBOARD ---
st.title("📈 Professional Investment Tracker")

holdings = load_holdings()
total_value = 0.0

if not holdings.empty:
    with st.spinner("Updating live prices..."):
        holdings["Price"] = holdings.apply(lambda x: get_mark_price(x["Symbol"], x["Type"]), axis=1)
        holdings["Multiplier"] = holdings["Type"].apply(lambda t: 100 if t == "Option" else 1)
        holdings["Market Value"] = holdings["Price"] * holdings["Quantity"] * holdings["Multiplier"]
        holdings["P&L ($)"] = (holdings["Price"] - holdings["Average Cost"]) * holdings["Quantity"] * holdings["Multiplier"]

        total_value = holdings["Market Value"].sum()
        holdings["Weight (%)"] = (holdings["Market Value"] / total_value * 100) if total_value > 0 else 0
        total_pnl = holdings["P&L ($)"].sum()

    hist_df = update_history(total_value)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Value", f"${total_value:,.2f}")

    if len(hist_df) >= 2:
        prev_val = hist_df.iloc[-2]["Value"]
        day_change = total_value - prev_val
        day_pct = (day_change / prev_val * 100) if prev_val else 0
        m2.metric("Day Change", f"${day_change:,.2f}", delta=f"{day_pct:.2f}%")
    else:
        m2.metric("Day Change", "N/A (need 2+ days)")

    m3.metric("Total P&L", f"${total_pnl:,.2f}",
              delta=f"{(total_pnl / (total_value - total_pnl) * 100):.1f}%" if (total_value - total_pnl) != 0 else None)
    m4.metric("Holdings", len(holdings))

    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Historical Performance Chart
    st.subheader("Portfolio Performance (History)")
    timeframe = st.select_slider("Select Range", options=["1W", "1M", "6M", "YTD", "1Y", "Lifetime"], key="hist_range")

    hist_df["Date"] = pd.to_datetime(hist_df["Date"])
    now = datetime.now()
    if timeframe == "1W":
        start_date = now - timedelta(days=7)
    elif timeframe == "1M":
        start_date = now - timedelta(days=30)
    elif timeframe == "6M":
        start_date = now - timedelta(days=180)
    elif timeframe == "YTD":
        start_date = datetime(now.year, 1, 1)
    elif timeframe == "1Y":
        start_date = now - timedelta(days=365)
    else:
        start_date = hist_df["Date"].min()

    filtered_hist = hist_df[hist_df["Date"] >= start_date]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=filtered_hist["Date"], y=filtered_hist["Value"],
        mode="lines+markers", line=dict(color="#00d1b2", width=3),
        fill="tozeroy", name="Total Value"
    ))
    fig.update_layout(
        template="plotly_dark", height=350,
        margin=dict(l=0, r=0, t=20, b=0),
        yaxis_title="Portfolio Value ($)",
        xaxis=dict(fixedrange=True), yaxis=dict(fixedrange=True), dragmode=False
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Holdings Table
    st.subheader("Current Positions")
    display_cols = [c for c in holdings.columns if c != "Multiplier"]
    st.dataframe(
        holdings[display_cols].style.format({
            "Price": "${:,.2f}", "Market Value": "${:,.2f}",
            "Weight (%)": "{:.1f}%", "P&L ($)": "${:,.2f}",
            "Average Cost": "${:,.4f}", "Quantity": "{:,.2f}"
        }),
        use_container_width=True, hide_index=True
    )

else:
    st.info("No holdings found. Upload a brokerage CSV or add assets manually.")

# ============================================================
# CURRENT PORTFOLIO ANALYSIS
# ============================================================
st.divider()
st.header("🔍 Current Portfolio Analysis")

if holdings.empty:
    st.info("Add holdings to see portfolio analysis.")
else:
    # Calculate weights
    total = holdings["Market Value"].sum()
    cash_val = holdings.loc[holdings["Type"] == "Cash", "Market Value"].sum()
    bond_val = holdings.loc[holdings["Symbol"].str.contains("BND|AGG|TLT|IEF|BNDX", case=False, na=False), "Market Value"].sum()
    option_val = holdings.loc[holdings["Type"] == "Option", "Market Value"].sum()
    equity_val = total - cash_val - bond_val - option_val

    cash_pct = cash_val / total * 100 if total > 0 else 0
    bond_pct = bond_val / total * 100 if total > 0 else 0
    option_pct = option_val / total * 100 if total > 0 else 0
    equity_pct = equity_val / total * 100 if total > 0 else 0

    # Detect characteristics
    has_leveraged_options = any(
        (holdings["Type"] == "Option") & 
        (holdings["Symbol"].str.contains("TQQQ|SQQQ|UPRO|SPXU|TNA|TZA", case=False, na=False))
    )
    has_long_calls = any(
        (holdings["Type"] == "Option") & 
        (holdings["Symbol"].str.contains("C", case=False, na=False))
    )
    high_option_concentration = option_pct > 15
    high_bond = bond_pct > 25
    high_cash = cash_pct > 15

    # Determine overall style
    if has_leveraged_options and option_pct > 10:
        style = "Aggressive / High-Beta Growth"
        best_market = "Strong Bull Market"
        color = "green"
        description = (
            "This portfolio is constructed for maximum upside in a rising market. "
            "The combination of leveraged long calls (especially TQQQ) gives it high sensitivity to tech/Nasdaq moves. "
            "It will perform best in a sustained bull market with positive momentum."
        )
        risks = "High volatility and significant drawdown risk in corrections or bear markets. Leveraged options can decay or go to zero."
    elif equity_pct > 70 and option_pct < 10 and bond_pct < 20:
        style = "Growth-Oriented Equity"
        best_market = "Bull Market or Mild Bull Market"
        color = "green"
        description = "Primarily equity-focused with limited defensive holdings. Best suited for rising markets."
        risks = "Vulnerable to broad market pullbacks."
    elif bond_pct > 30 or (bond_pct + cash_pct) > 40:
        style = "Balanced / Defensive"
        best_market = "Sideways to Mildly Bearish Markets"
        color = "orange"
        description = (
            "Meaningful allocation to bonds and/or cash provides ballast. "
            "This structure is better suited for uncertain, sideways, or moderately declining markets."
        )
        risks = "Will lag significantly in strong bull markets."
    else:
        style = "Moderately Aggressive"
        best_market = "Bull Market with moderate volatility"
        color = "blue"
        description = "Mix of growth exposure and some defensive elements. Performs reasonably in rising markets while offering limited protection."
        risks = "Still carries meaningful equity risk."

    # Display results
    col1, col2 = st.columns(2)

    with col1:
        st.metric("Portfolio Style", style)
        st.metric("Best Suited Market", best_market)

    with col2:
        st.markdown("**Current Allocation**")
        st.write(f"• Equity / Other: **{equity_pct:.1f}%**")
        st.write(f"• Options: **{option_pct:.1f}%**")
        st.write(f"• Bonds: **{bond_pct:.1f}%**")
        st.write(f"• Cash: **{cash_pct:.1f}%**")

    st.markdown("---")
    st.markdown(f"**Analysis:** {description}")
    st.markdown(f"**Key Risk:** {risks}")

    # Extra notes based on specific holdings
    notes = []
    if has_leveraged_options:
        notes.append("• Presence of leveraged ETF options (TQQQ etc.) significantly increases bullish bias and volatility.")
    if high_option_concentration:
        notes.append(f"• Options make up {option_pct:.1f}% of the portfolio — this is a high-conviction, high-risk stance.")
    if high_bond:
        notes.append(f"• Bond allocation ({bond_pct:.1f}%) provides meaningful downside cushion.")
    if high_cash:
        notes.append(f"• Elevated cash ({cash_pct:.1f}%) gives flexibility but creates cash drag in strong rallies.")

    if notes:
        st.markdown("**Additional Observations:**")
        for note in notes:
            st.markdown(note)
# ============================================================
# PROJECTION CALCULATOR
# ============================================================
st.divider()
st.header("🔮 Portfolio Projection Calculator")

col1, col2, col3 = st.columns(3)

with col1:
    annual_contribution = st.slider(
        "Annual Contribution ($)", 
        min_value=0, max_value=100000, value=10000, step=1000
    )

with col2:
    expected_return = st.slider(
        "Expected Annual Return (%)", 
        min_value=0.0, max_value=20.0, value=8.0, step=0.5
    )

with col3:
    years = st.slider(
        "Time Horizon (Years)", 
        min_value=1, max_value=50, value=20, step=1
    )

# Starting value = current portfolio (or 0 if empty)
start_value = total_value if total_value > 0 else 0

# Calculate projection
r = expected_return / 100
years_list = list(range(0, years + 1))
values = []

for y in years_list:
    if r == 0:
        fv = start_value + annual_contribution * y
    else:
        fv = start_value * (1 + r)**y + annual_contribution * (((1 + r)**y - 1) / r)
    values.append(fv)

# Summary metrics
final_value = values[-1]
total_contributed = annual_contribution * years
growth = final_value - start_value - total_contributed

m1, m2, m3, m4 = st.columns(4)
m1.metric("Starting Value", f"${start_value:,.0f}")
m2.metric("Total Contributions", f"${total_contributed:,.0f}")
m3.metric("Investment Growth", f"${growth:,.0f}")
m4.metric(f"Value in {years} Years", f"${final_value:,.0f}", delta=f"+{(final_value/start_value-1)*100:.1f}%" if start_value > 0 else None)

# Projection Chart
fig_proj = go.Figure()
fig_proj.add_trace(go.Scatter(
    x=years_list, y=values,
    mode="lines+markers",
    line=dict(color="#00d1b2", width=3),
    fill="tozeroy",
    name="Projected Value"
))
fig_proj.update_layout(
    template="plotly_dark",
    height=400,
    margin=dict(l=0, r=0, t=30, b=0),
    xaxis_title="Years from Now",
    yaxis_title="Portfolio Value ($)",
    title=f"Projected Growth at {expected_return}% annual return",
    xaxis=dict(fixedrange=True),
    yaxis=dict(fixedrange=True),
    dragmode=False
)
st.plotly_chart(fig_proj, use_container_width=True, config={"displayModeBar": False})

st.caption("Assumption: Contributions are made at the end of each year. Returns are compounded annually.")

# ============================================================
# PORTFOLIO OUTLOOK SUGGESTIONS
# ============================================================
st.divider()
st.header("🎯 Suggested Portfolio Changes by Market Outlook")

outlook = st.selectbox(
    "Select Market Outlook",
    options=["Bullish", "Neutral", "Bearish"],
    index=0
)

# Analyze current holdings
has_bnd = any(holdings["Symbol"].str.contains("BND", case=False)) if not holdings.empty else False
has_tqqq_calls = any((holdings["Type"] == "Option") & (holdings["Symbol"].str.contains("TQQQ", case=False))) if not holdings.empty else False
has_cash = any(holdings["Type"] == "Cash") if not holdings.empty else False
cash_amount = holdings.loc[holdings["Type"] == "Cash", "Market Value"].sum() if has_cash else 0
total_value = holdings["Market Value"].sum() if not holdings.empty else 0
bond_weight = (holdings.loc[holdings["Symbol"].str.contains("BND", case=False), "Market Value"].sum() / total_value * 100) if has_bnd and total_value > 0 else 0
option_weight = (holdings.loc[holdings["Type"] == "Option", "Market Value"].sum() / total_value * 100) if not holdings.empty and total_value > 0 else 0

if outlook == "Bullish":
    st.subheader("📈 Bullish Outlook Recommendations")
    st.success("Goal: Maximize upside participation while keeping some risk controls.")

    suggestions = []

    if has_cash and cash_amount > 1000:
        suggestions.append(f"**Deploy Cash**: You currently have ~${cash_amount:,.0f} in cash. Consider putting a large portion into growth-oriented positions (e.g. QQQ, TQQQ, or high-conviction individual stocks).")

    if has_bnd and bond_weight > 15:
        suggestions.append(f"**Reduce Bond Exposure**: BND is currently ~{bond_weight:.1f}% of the portfolio. In a strong bull market, consider trimming BND and rotating into equities or leveraged ETFs.")

    if has_tqqq_calls:
        suggestions.append("**Keep / Add to TQQQ Calls**: Your long-dated TQQQ calls are well-positioned for a bullish move. Consider adding more contracts on dips or rolling to higher strikes if the move is strong.")

    if not has_tqqq_calls:
        suggestions.append("**Add Leveraged Upside**: Consider initiating or adding long-dated TQQQ or QQQ call options to increase bullish convexity.")

    suggestions.append("**Increase Equity Beta**: Favor growth/tech heavy positions over defensive ones.")
    suggestions.append("**Avoid new protective puts** unless used for very short-term hedges.")

    for i, s in enumerate(suggestions, 1):
        st.markdown(f"{i}. {s}")

elif outlook == "Neutral":
    st.subheader("⚖️ Neutral Outlook Recommendations")
    st.info("Goal: Maintain balance, harvest gains where appropriate, and stay flexible.")

    suggestions = []

    if option_weight > 20:
        suggestions.append(f"**Trim Some Options**: Options currently make up ~{option_weight:.1f}% of the portfolio. Consider taking partial profits on TQQQ calls to reduce volatility.")

    if has_cash and cash_amount < total_value * 0.05:
        suggestions.append("**Build a small cash buffer**: Aim for 5–10% cash so you can buy dips without selling core holdings.")

    if has_bnd:
        suggestions.append("**Keep core bond allocation**: BND provides ballast. Maintain or slightly increase it for stability.")

    suggestions.append("**Rebalance if needed**: Bring any single position that has grown >25–30% of the portfolio back toward target weight.")
    suggestions.append("**Prefer defined-risk strategies**: Use spreads instead of naked long calls if adding new option exposure.")
    suggestions.append("**Stay patient**: Avoid chasing extended moves. Wait for better entry points.")

    for i, s in enumerate(suggestions, 1):
        st.markdown(f"{i}. {s}")

elif outlook == "Bearish":
    st.subheader("📉 Bearish Outlook Recommendations")
    st.warning("Goal: Preserve capital and reduce downside risk.")

    suggestions = []

    if has_tqqq_calls:
        suggestions.append("**Reduce or exit TQQQ Calls**: Leveraged long calls can lose significant value quickly in a sell-off. Strongly consider closing or heavily trimming these positions.")

    if has_cash and cash_amount < total_value * 0.15:
        suggestions.append(f"**Increase Cash**: Raise cash to at least 15–25% of the portfolio (currently ~${cash_amount:,.0f}).")

    if has_bnd and bond_weight < 25:
        suggestions.append(f"**Increase Bond Allocation**: BND is a defensive holding. Consider adding more (current weight ~{bond_weight:.1f}%).")

    suggestions.append("**Reduce overall equity exposure**: Trim high-beta stocks and growth positions.")
    suggestions.append("**Consider protective puts** on major indices (QQQ or SPY) if you want to keep some equity exposure.")
    suggestions.append("**Avoid new leveraged long positions** until the trend clearly turns.")

    for i, s in enumerate(suggestions, 1):
        st.markdown(f"{i}. {s}")

st.caption("These are general suggestions based on your current holdings and the selected outlook. They are not personalized financial advice.")
