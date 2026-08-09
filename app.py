import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os
import re
import io
import numpy as np

st.set_page_config(layout="wide", page_title="Portfolio Pulse")

# -------------------- MULTI-USER EMAIL + PASSWORD LOGIN --------------------
def check_login():
    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 Portfolio Pulse – Login")
    st.write("Please enter your email and password.")

    with st.form("Login"):
        email = st.text_input("Email").strip().lower()
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

        if submitted:
            try:
                accounts = st.secrets["accounts"]
                accounts_lower = {str(k).lower(): str(v) for k, v in accounts.items()}

                if email in accounts_lower and password == accounts_lower[email]:
                    st.session_state["authenticated"] = True
                    st.session_state["user_email"] = email
                    st.rerun()
                else:
                    st.error("Invalid email or password")
            except Exception as e:
                st.error("Error reading accounts from Secrets")
                st.exception(e)

    return False


if not check_login():
    st.stop()

with st.sidebar:
    st.markdown(f"**Logged in as:** `{st.session_state['user_email']}`")
    if st.button("Log out"):
        for key in ["authenticated", "user_email"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()
# -------------------- END LOGIN --------------------


# --- USER-SPECIFIC FILES ---
def get_user_files():
    email = st.session_state.get("user_email", "anonymous")
    safe_name = email.replace("@", "_at_").replace(".", "_").replace("+", "_")
    holdings_file = f"holdings_{safe_name}.csv"
    history_file = f"history_{safe_name}.csv"
    return holdings_file, history_file


# --- HELPERS ---
def load_holdings():
    holdings_file, _ = get_user_files()
    if os.path.exists(holdings_file):
        return pd.read_csv(holdings_file)
    return pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"])

def save_holdings(df):
    holdings_file, _ = get_user_files()
    df.to_csv(holdings_file, index=False)

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
    _, history_file = get_user_files()
    today = datetime.now().strftime("%Y-%m-%d")

    if os.path.exists(history_file):
        hist_df = pd.read_csv(history_file)
    else:
        hist_df = pd.DataFrame(columns=["Date", "Value"])

    if today in hist_df["Date"].values:
        hist_df.loc[hist_df["Date"] == today, "Value"] = total_val
    else:
        new_entry = pd.DataFrame([{"Date": today, "Value": total_val}])
        hist_df = pd.concat([hist_df, new_entry], ignore_index=True)

    hist_df.to_csv(history_file, index=False)
    return hist_df

def import_brokerage_csv(uploaded_file):
    content = uploaded_file.getvalue().decode("utf-8")
    lines = content.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if "Symbol" in line and ("Qty" in line or "Quantity" in line):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find a header row containing 'Symbol'")

    df = pd.read_csv(io.StringIO(content), skiprows=header_idx)
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
        raise ValueError(f"Missing required columns. Found: {list(df.columns)}")

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
        raise ValueError("No valid positions found")
    return pd.DataFrame(records)

def merge_holdings(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return new.copy()

    combined = pd.concat([existing, new], ignore_index=True)
    combined["TotalCost"] = combined["Quantity"] * combined["Average Cost"]

    merged = combined.groupby(["Symbol", "Type"], as_index=False).agg({
        "Quantity": "sum",
        "TotalCost": "sum"
    })

    merged["Average Cost"] = merged.apply(
        lambda row: row["TotalCost"] / row["Quantity"] if row["Quantity"] != 0 else 0, axis=1
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
                new_row = pd.DataFrame([{"Symbol": sym, "Type": a_type, "Quantity": qty, "Average Cost": cost}])
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

if st.sidebar.button("🗑️ Reset My Data", type="secondary"):
    holdings_file, history_file = get_user_files()
    for f in [holdings_file, history_file]:
        if os.path.exists(f):
            os.remove(f)
    st.rerun()


# --- MAIN DASHBOARD ---
st.title("📈 Portfolio Pulse")

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

    # Historical chart
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

    # Holdings table
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
    total = holdings["Market Value"].sum()
    cash_val = holdings.loc[holdings["Type"] == "Cash", "Market Value"].sum()
    bond_val = holdings.loc[holdings["Symbol"].str.contains("BND|AGG|TLT|IEF|BNDX", case=False, na=False), "Market Value"].sum()
    option_val = holdings.loc[holdings["Type"] == "Option", "Market Value"].sum()
    equity_val = total - cash_val - bond_val - option_val

    cash_pct = cash_val / total * 100 if total > 0 else 0
    bond_pct = bond_val / total * 100 if total > 0 else 0
    option_pct = option_val / total * 100 if total > 0 else 0
    equity_pct = equity_val / total * 100 if total > 0 else 0

    has_leveraged_options = any(
        (holdings["Type"] == "Option") & 
        (holdings["Symbol"].str.contains("TQQQ|SQQQ|UPRO|SPXU|TNA|TZA", case=False, na=False))
    )

    if has_leveraged_options and option_pct > 10:
        style = "Aggressive / High-Beta Growth"
        best_market = "Strong Bull Market"
        description = "This portfolio is built for maximum upside in a rising market, driven by leveraged long calls."
        risks = "High volatility and significant drawdown risk in corrections or bear markets."
    elif equity_pct > 70 and option_pct < 10 and bond_pct < 20:
        style = "Growth-Oriented Equity"
        best_market = "Bull Market"
        description = "Primarily equity-focused with limited defensive holdings."
        risks = "Vulnerable to broad market pullbacks."
    elif bond_pct > 30 or (bond_pct + cash_pct) > 40:
        style = "Balanced / Defensive"
        best_market = "Sideways to Mildly Bearish Markets"
        description = "Meaningful allocation to bonds/cash provides ballast."
        risks = "Will lag significantly in strong bull markets."
    else:
        style = "Moderately Aggressive"
        best_market = "Bull Market with moderate volatility"
        description = "Mix of growth exposure and some defensive elements."
        risks = "Still carries meaningful equity risk."

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

    st.markdown(f"**Analysis:** {description}")
    st.markdown(f"**Key Risk:** {risks}")


# ============================================================
# OUTLOOK SUGGESTIONS
# ============================================================
st.divider()
st.header("🎯 Suggested Portfolio Changes by Market Outlook")

outlook = st.selectbox("Select Market Outlook", ["Bullish", "Neutral", "Bearish"])

has_bnd = any(holdings["Symbol"].str.contains("BND", case=False)) if not holdings.empty else False
has_tqqq_calls = any(
    (holdings["Type"] == "Option") & (holdings["Symbol"].str.contains("TQQQ", case=False))
) if not holdings.empty else False
has_cash = any(holdings["Type"] == "Cash") if not holdings.empty else False
cash_amount = holdings.loc[holdings["Type"] == "Cash", "Market Value"].sum() if has_cash else 0
bond_weight = (
    holdings.loc[holdings["Symbol"].str.contains("BND", case=False), "Market Value"].sum() / total_value * 100
) if has_bnd and total_value > 0 else 0
option_weight = (
    holdings.loc[holdings["Type"] == "Option", "Market Value"].sum() / total_value * 100
) if not holdings.empty and total_value > 0 else 0

if outlook == "Bullish":
    st.subheader("📈 Bullish Recommendations")
    st.success("Goal: Maximize upside participation.")
    suggestions = []
    if has_cash and cash_amount > 1000:
        suggestions.append(f"**Deploy Cash**: You have ~${cash_amount:,.0f} in cash. Consider deploying into growth positions.")
    if has_bnd and bond_weight > 15:
        suggestions.append(f"**Reduce Bonds**: BND is ~{bond_weight:.1f}% of the portfolio. Consider rotating into equities.")
    if has_tqqq_calls:
        suggestions.append("**Keep / Add to TQQQ Calls**: Your long-dated calls are well positioned for upside.")
    else:
        suggestions.append("**Add Leveraged Upside**: Consider long-dated TQQQ or QQQ calls.")
    suggestions.append("**Increase Equity Beta** and avoid new protective puts.")
    for i, s in enumerate(suggestions, 1):
        st.markdown(f"{i}. {s}")

elif outlook == "Neutral":
    st.subheader("⚖️ Neutral Recommendations")
    st.info("Goal: Maintain balance and stay flexible.")
    suggestions = []
    if option_weight > 20:
        suggestions.append(f"**Trim Options**: Options are ~{option_weight:.1f}% of the portfolio. Consider taking partial profits.")
    if has_cash and cash_amount < total_value * 0.05:
        suggestions.append("**Build a small cash buffer** (aim for 5–10%).")
    if has_bnd:
        suggestions.append("**Keep core bond allocation** for stability.")
    suggestions.append("**Rebalance** any position that has grown too large.")
    for i, s in enumerate(suggestions, 1):
        st.markdown(f"{i}. {s}")

else:  # Bearish
    st.subheader("📉 Bearish Recommendations")
    st.warning("Goal: Preserve capital.")
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

# ============================================================
# PROJECTION CALCULATOR
# ============================================================
st.divider()
st.header("🔮 Portfolio Projection Calculator")

col1, col2, col3 = st.columns(3)
with col1:
    annual_contribution = st.slider("Annual Contribution ($)", 0, 100000, 10000, 1000)
with col2:
    expected_return = st.slider("Expected Annual Return (%)", 0.0, 20.0, 8.0, 0.5)
with col3:
    years = st.slider("Time Horizon (Years)", 1, 50, 20)

start_value = total_value if total_value > 0 else 0
r = expected_return / 100
years_list = list(range(0, years + 1))
values = []

for y in years_list:
    if r == 0:
        fv = start_value + annual_contribution * y
    else:
        fv = start_value * (1 + r)**y + annual_contribution * (((1 + r)**y - 1) / r)
    values.append(fv)

final_value = values[-1]
total_contributed = annual_contribution * years
growth = final_value - start_value - total_contributed

m1, m2, m3, m4 = st.columns(4)
m1.metric("Starting Value", f"${start_value:,.0f}")
m2.metric("Total Contributions", f"${total_contributed:,.0f}")
m3.metric("Investment Growth", f"${growth:,.0f}")
m4.metric(f"Value in {years} Years", f"${final_value:,.0f}")

fig_proj = go.Figure()
fig_proj.add_trace(go.Scatter(
    x=years_list, y=values,
    mode="lines+markers", line=dict(color="#00d1b2", width=3),
    fill="tozeroy", name="Projected Value"
))
fig_proj.update_layout(
    template="plotly_dark", height=400,
    margin=dict(l=0, r=0, t=30, b=0),
    xaxis_title="Years from Now", yaxis_title="Portfolio Value ($)",
    title=f"Projected Growth at {expected_return}% annual return",
    xaxis=dict(fixedrange=True), yaxis=dict(fixedrange=True), dragmode=False
)
st.plotly_chart(fig_proj, use_container_width=True, config={"displayModeBar": False})
st.caption("Assumption: Contributions at end of each year. Returns compounded annually.")
