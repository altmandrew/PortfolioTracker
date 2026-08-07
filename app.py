import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os

st.set_page_config(layout="wide", page_title="Pro Portfolio Tracker")

# --- FILE PATHS ---
HOLDINGS_FILE = "my_holdings.csv"
HISTORY_FILE = "portfolio_history.csv"

# --- DATA HELPERS ---
def load_holdings():
    if os.path.exists(HOLDINGS_FILE):
        return pd.read_csv(HOLDINGS_FILE)
    return pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"])

def save_holdings(df):
    df.to_csv(HOLDINGS_FILE, index=False)

def get_mark_price(symbol, asset_type):
    if asset_type == "Cash":
        return 1.0
    try:
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
    today = datetime.now().strftime('%Y-%m-%d')
    if os.path.exists(HISTORY_FILE):
        hist_df = pd.read_csv(HISTORY_FILE)
    else:
        hist_df = pd.DataFrame(columns=["Date", "Value"])

    if today not in hist_df['Date'].values:
        new_entry = pd.DataFrame([{"Date": today, "Value": total_val}])
        hist_df = pd.concat([hist_df, new_entry], ignore_index=True)
        hist_df.to_csv(HISTORY_FILE, index=False)
    return hist_df

# --- SIDEBAR: INPUTS ---
st.sidebar.header("📥 Portfolio Management")

with st.sidebar.expander("➕ Add Asset", expanded=True):
    with st.form("add_asset"):
        a_type = st.selectbox("Asset Type", ["Stock", "ETF", "Option", "Cash"])
        
        # Dynamic help text for Cash
        if a_type == "Cash":
            st.caption("For Cash: Quantity = dollar amount (e.g. 5000 for $5,000)")
            default_sym = "CASH"
            default_cost = 1.0
        else:
            default_sym = ""
            default_cost = 0.0

        sym = st.text_input("Symbol (e.g., AAPL or MSFT240920C00400000)", value=default_sym).upper().strip()
        qty = st.number_input("Quantity", min_value=0.0, step=1.0, value=0.0)
        cost = st.number_input("Avg Cost", min_value=0.0, value=default_cost)

        if st.form_submit_button("Add to Portfolio"):
            if a_type != "Cash" and not sym:
                st.error("Symbol is required for stocks, ETFs, and options")
            else:
                # Force clean values for Cash
                if a_type == "Cash":
                    sym = sym if sym else "CASH"
                    cost = 1.0

                df = load_holdings()
                new_row = pd.DataFrame([{
                    "Symbol": sym,
                    "Type": a_type,
                    "Quantity": qty,
                    "Average Cost": cost
                }])
                save_holdings(pd.concat([df, new_row], ignore_index=True))
                st.success(f"Added {sym} ({a_type})")
                st.rerun()

with st.sidebar.expander("📂 Upload Spreadsheet"):
    file = st.file_uploader("Upload CSV (Symbol, Type, Quantity, Average Cost)")
    if file:
        try:
            df_upload = pd.read_csv(file)
            required = {"Symbol", "Type", "Quantity", "Average Cost"}
            if not required.issubset(df_upload.columns):
                st.error(f"CSV must contain columns: {required}")
            else:
                save_holdings(df_upload)
                st.success("Uploaded successfully!")
                st.rerun()
        except Exception as e:
            st.error(f"Upload failed: {e}")

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
    if os.path.exists(HOLDINGS_FILE):
        os.remove(HOLDINGS_FILE)
    if os.path.exists(HISTORY_FILE):
        os.remove(HISTORY_FILE)
    st.rerun()

# --- MAIN DASHBOARD ---
st.title("📈 Professional Investment Tracker")

holdings = load_holdings()

if not holdings.empty:
    with st.spinner("Updating Mark Values..."):
        holdings['Price'] = holdings.apply(lambda x: get_mark_price(x['Symbol'], x['Type']), axis=1)

        # Options ×100, everything else ×1
        holdings['Multiplier'] = holdings['Type'].apply(lambda t: 100 if t == "Option" else 1)
        holdings['Market Value'] = holdings['Price'] * holdings['Quantity'] * holdings['Multiplier']
        holdings['P&L ($)'] = (holdings['Price'] - holdings['Average Cost']) * holdings['Quantity'] * holdings['Multiplier']

        total_value = holdings['Market Value'].sum()
        holdings['Weight (%)'] = (holdings['Market Value'] / total_value * 100) if total_value > 0 else 0
        total_pnl = holdings['P&L ($)'].sum()

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

    st.subheader("Portfolio Performance")
    timeframe = st.select_slider(
        "Select Range",
        options=["1W", "1M", "6M", "YTD", "1Y", "Lifetime"]
    )

    hist_df['Date'] = pd.to_datetime(hist_df['Date'])
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
        start_date = hist_df['Date'].min()

    filtered_hist = hist_df[hist_df['Date'] >= start_date]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=filtered_hist['Date'], y=filtered_hist['Value'],
        mode='lines+markers',
        line=dict(color='#00d1b2', width=3),
        fill='tozeroy',
        name="Total Value"
    ))
    fig.update_layout(
        template="plotly_dark",
        height=400,
        margin=dict(l=0, r=0, t=20, b=0),
        yaxis_title="Portfolio Value ($)"
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Current Positions")
    display_cols = [c for c in holdings.columns if c != "Multiplier"]
    st.dataframe(
        holdings[display_cols].style.format({
            "Price": "${:,.2f}",
            "Market Value": "${:,.2f}",
            "Weight (%)": "{:.1f}%",
            "P&L ($)": "${:,.2f}",
            "Average Cost": "${:,.2f}",
            "Quantity": "{:,.2f}"
        }),
        use_container_width=True,
        hide_index=True
    )

else:
    st.info("No holdings found. Add assets in the sidebar or upload a CSV.")
