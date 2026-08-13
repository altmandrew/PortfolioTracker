import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os
import re
import io
import numpy as np
import gspread
from google.oauth2.service_account import Credentials
import requests
import extra_streamlit_components as stx
from datetime import datetime, timedelta
import hashlib
import hmac

st.set_page_config(layout="wide", page_title="Portfolio Pulse")



# -------------------- COOKIE-BASED AUTH --------------------
def get_cookie_manager():
    return stx.CookieManager(key="portfolio_pulse_cookies")

def create_auth_token(email: str) -> str:
    """Create a simple signed token"""
    secret = st.secrets.get("cookie_secret", "portfolio-pulse-secret-key-change-me")
    expiry = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
    message = f"{email}|{expiry}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{message}|{signature}"

def verify_auth_token(token: str):
    """Verify token and return email if valid"""
    try:
        secret = st.secrets.get("cookie_secret", "portfolio-pulse-secret-key-change-me")
        email, expiry, signature = token.split("|")
        message = f"{email}|{expiry}"
        expected_sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return None
        if datetime.utcnow() > datetime.strptime(expiry, "%Y-%m-%d"):
            return None
        return email
    except Exception:
        return None

def check_login():
    cookie_manager = get_cookie_manager()

    # Already authenticated in this session?
    if st.session_state.get("authenticated"):
        return True

    # Check for existing cookie
    token = cookie_manager.get("auth_token")
    if token:
        email = verify_auth_token(token)
        if email:
            st.session_state["authenticated"] = True
            st.session_state["user_email"] = email
            return True

    # Show login form
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
                    # Set session
                    st.session_state["authenticated"] = True
                    st.session_state["user_email"] = email

                    # Set long-lived cookie (30 days)
                    token = create_auth_token(email)
                    cookie_manager.set("auth_token", token, expires_at=datetime.utcnow() + timedelta(days=30))

                    st.success("Login successful!")
                    st.rerun()
                else:
                    st.error("Invalid email or password")
            except Exception as e:
                st.error("Error reading accounts from Secrets")
                st.exception(e)

    return False


if not check_login():
    st.stop()

# Sidebar user info + logout
with st.sidebar:
    st.markdown(f"**Logged in as:** `{st.session_state['user_email']}`")
    if st.button("Log out"):
        cookie_manager = get_cookie_manager()
        cookie_manager.delete("auth_token")
        for key in ["authenticated", "user_email"]:
            st.session_state.pop(key, None)
        st.rerun()
# -------------------- END LOGIN --------------------

# -------------------- PAGE NAVIGATION --------------------
page = st.sidebar.radio("Navigation", ["🏠 Home", "📰 News"], index=0)
st.sidebar.markdown("---")

# -------------------- GOOGLE SHEETS HELPERS --------------------
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["google_service_account"],
        scopes=scopes
    )
    return gspread.authorize(creds)

def get_spreadsheet():
    client = get_gspread_client()
    return client.open_by_key(st.secrets["google_sheets"]["spreadsheet_id"])

def get_or_create_worksheet(spreadsheet, title, headers):
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=10)
        worksheet.append_row(headers)
    return worksheet

def load_holdings():
    user = st.session_state["user_email"]
    try:
        spreadsheet = get_spreadsheet()
        ws = get_or_create_worksheet(
            spreadsheet, "Holdings",
            ["User", "Symbol", "Type", "Quantity", "Average Cost"]
        )
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        if df.empty:
            return pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"])
        
        user_df = df[df["User"].str.lower() == user.lower()].copy()
        if user_df.empty:
            return pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"])
        
        return user_df[["Symbol", "Type", "Quantity", "Average Cost"]].reset_index(drop=True)
    except Exception as e:
        st.error(f"Error loading holdings: {e}")
        return pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"])

def save_holdings(df):
    user = st.session_state["user_email"]
    try:
        spreadsheet = get_spreadsheet()
        ws = get_or_create_worksheet(
            spreadsheet, "Holdings",
            ["User", "Symbol", "Type", "Quantity", "Average Cost"]
        )

        # Delete existing rows for this user
        all_values = ws.get_all_values()
        if len(all_values) > 1:
            rows_to_delete = []
            for idx, row in enumerate(all_values[1:], start=2):  # skip header
                if row and row[0].lower() == user.lower():
                    rows_to_delete.append(idx)
            
            # Delete from bottom to top so indices don't shift
            for row_idx in reversed(rows_to_delete):
                ws.delete_rows(row_idx)

        # Append new rows
        if not df.empty:
            rows = []
            for _, r in df.iterrows():
                rows.append([
                    user,
                    r["Symbol"],
                    r["Type"],
                    float(r["Quantity"]),
                    float(r["Average Cost"])
                ])
            ws.append_rows(rows)
    except Exception as e:
        st.error(f"Error saving holdings: {e}")

def update_history(total_val):
    user = st.session_state["user_email"]
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        spreadsheet = get_spreadsheet()
        ws = get_or_create_worksheet(
            spreadsheet, "History",
            ["User", "Date", "Value"]
        )

        records = ws.get_all_records()
        df = pd.DataFrame(records)

        # Remove today's entry for this user if it exists
        if not df.empty:
            mask = (df["User"].str.lower() == user.lower()) & (df["Date"] == today)
            if mask.any():
                all_values = ws.get_all_values()
                rows_to_delete = []
                for idx, row in enumerate(all_values[1:], start=2):
                    if row and row[0].lower() == user.lower() and row[1] == today:
                        rows_to_delete.append(idx)
                for row_idx in reversed(rows_to_delete):
                    ws.delete_rows(row_idx)

        # Add new entry
        ws.append_row([user, today, float(total_val)])

        # Return history for this user
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        user_hist = df[df["User"].str.lower() == user.lower()][["Date", "Value"]].copy()
        return user_hist.reset_index(drop=True)
    except Exception as e:
        st.error(f"Error updating history: {e}")
        return pd.DataFrame(columns=["Date", "Value"])


# --- REST OF THE HELPERS (option conversion, price fetch, import, merge) ---
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

def import_brokerage_csv(uploaded_file):
    content = uploaded_file.getvalue().decode("utf-8")
    lines = content.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "Symbol" in line and ("Qty" in line or "Quantity" in line):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row")

    df = pd.read_csv(io.StringIO(content), skiprows=header_idx)
    df.columns = [str(c).strip() for c in df.columns]
    cols_lower = {c.lower(): c for c in df.columns}

    def find_col(*keywords):
        for key, original in cols_lower.items():
            if any(kw in key for kw in keywords):
                return original
        return None

    symbol_col = find_col("symbol")
    qty_col = find_col("qty", "quantity")
    cost_col = find_col("cost basis", "cost")
    type_col = find_col("asset type", "type")
    mkt_col = find_col("mkt val", "market value", "mkt")

    if not all([symbol_col, qty_col, cost_col]):
        raise ValueError(f"Missing columns. Found: {list(df.columns)}")

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
            try:
                qty = float(str(row[qty_col]).replace(",", "").strip())
            except:
                continue
            if qty == 0:
                continue
            try:
                total_cost = float(str(row[cost_col]).replace("$", "").replace(",", "").strip())
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

def merge_holdings(existing, new):
    if existing.empty:
        return new.copy()
    combined = pd.concat([existing, new], ignore_index=True)
    combined["TotalCost"] = combined["Quantity"] * combined["Average Cost"]
    merged = combined.groupby(["Symbol", "Type"], as_index=False).agg({
        "Quantity": "sum",
        "TotalCost": "sum"
    })
    merged["Average Cost"] = merged.apply(
        lambda r: r["TotalCost"] / r["Quantity"] if r["Quantity"] != 0 else 0, axis=1
    )
    merged = merged.drop(columns=["TotalCost"])
    merged["Average Cost"] = merged["Average Cost"].round(4)
    return merged


# --- SIDEBAR ---
st.sidebar.header("📥 Portfolio Management")

with st.sidebar.expander("➕ Add Asset", expanded=False):
    with st.form("add_asset"):
        a_type = st.selectbox("Asset Type", ["Stock", "ETF", "Option", "Cash"])
        default_sym = "CASH" if a_type == "Cash" else ""
        default_cost = 1.0 if a_type == "Cash" else 0.0
        if a_type == "Cash":
            st.caption("Quantity = dollar amount")

        sym = st.text_input("Symbol", value=default_sym).upper().strip()
        qty = st.number_input("Quantity", min_value=0.0, step=1.0, value=0.0)
        cost = st.number_input("Avg Cost", min_value=0.0, value=default_cost)

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
    file = st.file_uploader("Upload CSV", type=["csv"])
    if file is not None:
        st.write(f"Selected: **{file.name}**")
        if st.button("🚀 Import & Add to Portfolio", type="primary", use_container_width=True):
            try:
                df_new = import_brokerage_csv(file)
                existing = load_holdings()
                merged = merge_holdings(existing, df_new)
                save_holdings(merged)
                st.success(f"Added {len(df_new)} positions")
                st.balloons()
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")
                st.exception(e)

holdings_preview = load_holdings()
if not holdings_preview.empty:
    with st.sidebar.expander("🗑️ Delete Holding"):
        to_delete = st.selectbox("Select symbol", holdings_preview["Symbol"].tolist())
        if st.button("Delete selected", type="primary"):
            holdings_preview = holdings_preview[holdings_preview["Symbol"] != to_delete]
            save_holdings(holdings_preview)
            st.success(f"Deleted {to_delete}")
            st.rerun()

if st.sidebar.button("🗑️ Reset My Data", type="secondary"):
    save_holdings(pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"]))
    st.success("Your data has been reset")
    st.rerun()

if page == "🏠 Home":
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
            prev = hist_df.iloc[-2]["Value"]
            change = total_value - prev
            pct = (change / prev * 100) if prev else 0
            m2.metric("Day Change", f"${change:,.2f}", delta=f"{pct:.2f}%")
        else:
            m2.metric("Day Change", "N/A")
        m3.metric("Total P&L", f"${total_pnl:,.2f}")
        m4.metric("Holdings", len(holdings))
    
        st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Data stored in Google Sheets")
    
        # Historical Performance Chart
    st.subheader("Portfolio Performance")
    
    # Initialize selected timeframe in session state
    if "hist_timeframe" not in st.session_state:
        st.session_state.hist_timeframe = "1M"
    
    # Button row
    cols = st.columns(6)
    timeframes = ["1W", "1M", "6M", "YTD", "1Y", "Lifetime"]
    
    for i, tf in enumerate(timeframes):
        if cols[i].button(tf, key=f"tf_{tf}", use_container_width=True,
                          type="primary" if st.session_state.hist_timeframe == tf else "secondary"):
            st.session_state.hist_timeframe = tf
            st.rerun()
    
    timeframe = st.session_state.hist_timeframe
    
    # Filter data based on selected timeframe
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
    else:  # Lifetime
        start_date = hist_df["Date"].min()
    
    filtered_hist = hist_df[hist_df["Date"] >= start_date]
    
    # Chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=filtered_hist["Date"],
        y=filtered_hist["Value"],
        mode="lines+markers",
        line=dict(color="#00d1b2", width=3),
        fill="tozeroy",
        name="Total Value"
    ))
    fig.update_layout(
        template="plotly_dark",
        height=350,
        margin=dict(l=0, r=0, t=20, b=0),
        yaxis_title="Portfolio Value ($)",
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True),
        dragmode=False
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    
    
    # Analysis + Outlook + Projection sections remain the same as before
    # (You can keep the ones you already have or ask me to include them again)
    # ------------------ HOLDINGS TABLE ------------------
    st.subheader("Current Positions")
    
    if not holdings.empty:
        display_df = holdings[[
            "Symbol", "Type", "Quantity", "Average Cost",
            "Price", "Market Value", "Weight (%)", "P&L ($)"
        ]].copy()
    
        st.dataframe(
            display_df.style.format({
                "Quantity": "{:,.2f}",
                "Average Cost": "${:,.4f}",
                "Price": "${:,.2f}",
                "Market Value": "${:,.2f}",
                "Weight (%)": "{:.1f}%",
                "P&L ($)": "${:,.2f}"
            }),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No holdings to display.")
    
    # ============================================================
    # CNN FEAR & GREED INDEX (Last 30 Days)
    # ============================================================
    st.divider()
    st.subheader("😱 CNN Fear & Greed Index – Last 90 Days")
    
    @st.cache_data(ttl=3600)
    def get_cnn_fear_greed_30d():
        try:
            import fear_greed
            history = fear_greed.get_history(last="90")   # last 90 days
            
            df = pd.DataFrame(history)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            st.warning(f"Could not load CNN Fear & Greed data: {e}")
            return pd.DataFrame()
    
    fg_df = get_cnn_fear_greed_30d()
    
    if not fg_df.empty:
        current_value = fg_df.iloc[-1]["score"]
        current_label = fg_df.iloc[-1].get("rating", "").title()
    
        # Color coding
        if current_value <= 25:
            color = "#e74c3c"
        elif current_value <= 45:
            color = "#e67e22"
        elif current_value <= 55:
            color = "#f1c40f"
        elif current_value <= 75:
            color = "#2ecc71"
        else:
            color = "#27ae60"
    
        col1, col2 = st.columns([1, 3])
        with col1:
            st.metric("Current CNN Fear & Greed", f"{current_value:.0f}", delta=current_label)
    
        with col2:
            fig_fg = go.Figure()
            fig_fg.add_trace(go.Scatter(
                x=fg_df["date"],
                y=fg_df["score"],
                mode="lines+markers",
                line=dict(color=color, width=3),
                fill="tozeroy",
                name="CNN Fear & Greed"
            ))
    
            # Reference lines
            fig_fg.add_hline(y=25, line_dash="dot", line_color="red", annotation_text="Extreme Fear")
            fig_fg.add_hline(y=45, line_dash="dot", line_color="orange", annotation_text="Fear")
            fig_fg.add_hline(y=55, line_dash="dot", line_color="gray", annotation_text="Neutral")
            fig_fg.add_hline(y=75, line_dash="dot", line_color="green", annotation_text="Greed")
    
            fig_fg.update_layout(
                template="plotly_dark",
                height=350,
                margin=dict(l=0, r=0, t=20, b=0),
                yaxis=dict(range=[0, 100], title="Index (0-100)", fixedrange=True),
                xaxis=dict(fixedrange=True),
                dragmode=False,
                showlegend=False
            )
            st.plotly_chart(fig_fg, use_container_width=True, config={"displayModeBar": False})
    
        st.caption("Source: CNN Fear & Greed Index")
    else:
        st.info("CNN Fear & Greed data temporarily unavailable.")
    
    # Need to import requests at the top of your file if not already there
    # import requests
    
    
    
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
    # PROJECTION CALCULATOR (with Run button)
    # ============================================================
    st.divider()
    st.header("🔮 Portfolio Projection Calculator")
    
    with st.form("projection_form"):
        col1, col2, col3 = st.columns(3)
    
        with col1:
            annual_contribution = st.slider(
                "Annual Contribution ($)", 0, 100000, 10000, 1000
            )
        with col2:
            expected_return = st.slider(
                "Expected Annual Return (%)", 0.0, 20.0, 8.0, 0.5
            )
        with col3:
            years = st.slider(
                "Time Horizon (Years)", 1, 50, 20
            )
    
        run_projection = st.form_submit_button("▶ Run Projection", type="primary")
    
    if run_projection:
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
        st.caption("Assumption: Contributions at end of each year. Returns compounded annually.")
    else:
        st.info("Adjust the sliders above, then click **Run Projection**.")

elif page == "📰 News":
    st.title("📰 Market News")

    try:
        import feedparser
    except ImportError:
        st.error("`feedparser` is not installed. Add it to requirements.txt and reboot the app.")
        st.stop()

    CHANNELS = {
    "Meet Kevin": {
        "channel_id": "UCUvvj5lwue7PspotMDjk5UA",
        "count": 3
    },
    "Bravos Research": {
        "channel_id": "UCOHxDwCcOzBaLkeTazanwcw",
        "count": 2
    },
    "FX Evolution": {
        "channel_id": "UCvJZEG5x-DVYZKTz--pS39w",
        "count": 1
    }
}

    def get_recent_videos(channel_id: str, max_results: int = 3):
        """
        Tries multiple methods to get recent videos because 
        YouTube's official RSS is unreliable from cloud servers.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
    
        # Method 1: Uploads playlist (UC → UU)
        playlist_id = "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id
        urls_to_try = [
            f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}",
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            f"https://openrss.org/feeds/youtube/{channel_id}",           # fallback proxy
        ]
    
        for url in urls_to_try:
            try:
                response = requests.get(url, headers=headers, timeout=12)
                if response.status_code != 200:
                    continue
    
                feed = feedparser.parse(response.content)
                if not feed.entries:
                    continue
    
                videos = []
                for entry in feed.entries[:max_results]:
                    video_id = None
                    if hasattr(entry, "yt_videoid"):
                        video_id = entry.yt_videoid
                    elif "v=" in entry.get("link", ""):
                        video_id = entry.link.split("v=")[-1].split("&")[0]
    
                    if not video_id:
                        continue
    
                    videos.append({
                        "title": entry.get("title", "No title"),
                        "link": entry.get("link", f"https://youtube.com/watch?v={video_id}"),
                        "published": str(entry.get("published", ""))[:10],
                        "video_id": video_id,
                        "thumbnail": f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                    })
    
                if videos:


elif page == "📰 News":
    st.title("📰 Market News")

    YOUTUBE_API_KEY = st.secrets.get("youtube", {}).get("api_key")

    if not YOUTUBE_API_KEY:
        st.error("YouTube API key missing in secrets.")
        st.stop()

    CHANNELS = {
        "Meet Kevin": {
            "channel_id": "UCUvvj5lwue7PspotMDjk5UA",
            "count": 3
        },
        "Bravos Research": {
            "channel_id": "UCOHxDwCcOzBaLkeTazanwcw",
            "count": 2
        },
        "FX Evolution": {
            "channel_id": "UCvJZEG5x-DVYZKTz--pS39w",
            "count": 1
        }
    }

    @st.cache_data(ttl=1800)
    def get_recent_videos(channel_id: str, max_results: int = 3):
        # Convert channel ID to uploads playlist ID (UC → UU)
        uploads_playlist_id = "UU" + channel_id[2:]

        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {
            "key": YOUTUBE_API_KEY,
            "playlistId": uploads_playlist_id,
            "part": "snippet",
            "maxResults": max_results
        }

        try:
            response = requests.get(url, params=params, timeout=15)
            data = response.json()

            if "error" in data:
                return []

            videos = []
            for item in data.get("items", []):
                snippet = item["snippet"]
                video_id = snippet["resourceId"]["videoId"]
                videos.append({
                    "title": snippet["title"],
                    "link": f"https://www.youtube.com/watch?v={video_id}",
                    "published": snippet["publishedAt"][:10],
                    "video_id": video_id,
                    "thumbnail": snippet["thumbnails"]["medium"]["url"]
                })
            return videos
        except Exception:
            return []

    for channel_name, config in CHANNELS.items():
        st.subheader(channel_name)

        videos = get_recent_videos(config["channel_id"], config["count"])

        if not videos:
            st.info(f"Could not load videos for {channel_name} right now.")
            continue

        for video in videos:
            col1, col2 = st.columns([1, 3])

            with col1:
                st.image(video["thumbnail"], use_container_width=True)

            with col2:
                st.markdown(f"### [{video['title']}]({video['link']})")
                st.caption(f"Published: {video['published']}")

                with st.expander("🧠 AI Summary", expanded=False):
                    try:
                        from youtube_transcript_api import YouTubeTranscriptApi
                        transcript = YouTubeTranscriptApi.get_transcript(video["video_id"])
                        full_text = " ".join([t["text"] for t in transcript])

                        words = full_text.split()
                        if len(words) > 650:
                            summary = " ".join(words[:380]) + "\n\n...\n\n" + " ".join(words[-180:])
                        else:
                            summary = full_text

                        st.write(summary)
                    except Exception as e:
                        st.warning("Summary not available.")
                        st.caption(str(e)[:180])

            st.markdown("---")
                    
