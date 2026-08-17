import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import re
import io
import gspread
from google.oauth2.service_account import Credentials
import requests
import extra_streamlit_components as stx
import hashlib
import hmac

st.set_page_config(layout="wide", page_title="Portfolio Pulse", initial_sidebar_state="expanded")

# ---------- Global Dark Theme (Google Finance style) ----------
st.markdown("""
<style>
    .stApp {
        background-color: #0f0f0f;
        color: #e8eaed;
    }
    section[data-testid="stSidebar"] {
        background-color: #161616 !important;
        border-right: 1px solid #2d2d2d;
    }
    section[data-testid="stSidebar"] * {
        color: #e8eaed !important;
    }
    div[data-testid="stMetricValue"] {
        color: #e8eaed !important;
    }
    .stRadio > label { display: none; }
    div[data-testid="stHorizontalBlock"] button {
        background-color: transparent !important;
        border: none !important;
        color: #9aa0a6 !important;
    }
    h1, h2, h3, h4 { color: #e8eaed !important; }
    .stCaption { color: #9aa0a6 !important; }
</style>
""", unsafe_allow_html=True)


# -------------------- COOKIE AUTH --------------------
def get_cookie_manager():
    return stx.CookieManager(key="portfolio_pulse_cookies")

def create_auth_token(email: str) -> str:
    secret = st.secrets.get("cookie_secret", "portfolio-pulse-secret-key-change-me")
    expiry = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
    message = f"{email}|{expiry}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{message}|{signature}"

def verify_auth_token(token: str):
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
    if st.session_state.get("authenticated"):
        return True
    token = cookie_manager.get("auth_token")
    if token:
        email = verify_auth_token(token)
        if email:
            st.session_state["authenticated"] = True
            st.session_state["user_email"] = email
            return True

    st.title("🔒 Portfolio Pulse – Login")
    with st.form("Login"):
        email = st.text_input("Email").strip().lower()
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Log in"):
            try:
                accounts = st.secrets["accounts"]
                accounts_lower = {str(k).lower(): str(v) for k, v in accounts.items()}
                if email in accounts_lower and password == accounts_lower[email]:
                    st.session_state["authenticated"] = True
                    st.session_state["user_email"] = email
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

with st.sidebar:
    st.markdown(f"**Logged in as:** `{st.session_state['user_email']}`")
    if st.button("Log out"):
        cookie_manager = get_cookie_manager()
        cookie_manager.delete("auth_token")
        for key in ["authenticated", "user_email"]:
            st.session_state.pop(key, None)
        st.rerun()
    st.markdown("---")

page = st.sidebar.radio("Navigation", ["🏠 Home", "📰 News"], index=0)

# -------------------- GOOGLE SHEETS --------------------
@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(st.secrets["google_service_account"], scopes=scopes)
    return gspread.authorize(creds)

def get_spreadsheet():
    return get_gspread_client().open_by_key(st.secrets["google_sheets"]["spreadsheet_id"])

def get_or_create_worksheet(spreadsheet, title, headers):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=10)
        ws.append_row(headers)
        return ws

def load_holdings():
    user = st.session_state["user_email"]
    try:
        ws = get_or_create_worksheet(get_spreadsheet(), "Holdings",
                                     ["User", "Symbol", "Type", "Quantity", "Average Cost"])
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
        ws = get_or_create_worksheet(get_spreadsheet(), "Holdings",
                                     ["User", "Symbol", "Type", "Quantity", "Average Cost"])
        all_values = ws.get_all_values()
        if len(all_values) > 1:
            rows_to_delete = [idx for idx, row in enumerate(all_values[1:], start=2)
                              if row and row[0].lower() == user.lower()]
            for row_idx in reversed(rows_to_delete):
                ws.delete_rows(row_idx)
        if not df.empty:
            rows = [[user, r["Symbol"], r["Type"], float(r["Quantity"]), float(r["Average Cost"])]
                    for _, r in df.iterrows()]
            ws.append_rows(rows)
    except Exception as e:
        st.error(f"Error saving holdings: {e}")

def update_history(total_val):
    user = st.session_state["user_email"]
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        ws = get_or_create_worksheet(get_spreadsheet(), "History", ["User", "Date", "Value"])
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        if not df.empty:
            mask = (df["User"].str.lower() == user.lower()) & (df["Date"] == today)
            if mask.any():
                all_values = ws.get_all_values()
                rows_to_delete = [idx for idx, row in enumerate(all_values[1:], start=2)
                                  if row and row[0].lower() == user.lower() and row[1] == today]
                for row_idx in reversed(rows_to_delete):
                    ws.delete_rows(row_idx)
        ws.append_row([user, today, float(total_val)])
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        return df[df["User"].str.lower() == user.lower()][["Date", "Value"]].reset_index(drop=True)
    except Exception as e:
        st.error(f"Error updating history: {e}")
        return pd.DataFrame(columns=["Date", "Value"])


# -------------------- HELPERS --------------------
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
            if year < 100: year += 2000
            yy = f"{year % 100:02d}"
            mm = f"{int(month):02d}"
            dd = f"{int(day):02d}"
            strike_str = f"{int(float(strike) * 1000):08d}"
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
        return float(ticker.fast_info.last_price)
    except Exception:
        return 0.0

def merge_holdings(existing, new):
    if existing.empty:
        return new.copy()
    combined = pd.concat([existing, new], ignore_index=True)
    combined["TotalCost"] = combined["Quantity"] * combined["Average Cost"]
    merged = combined.groupby(["Symbol", "Type"], as_index=False).agg({"Quantity": "sum", "TotalCost": "sum"})
    merged["Average Cost"] = merged.apply(lambda r: r["TotalCost"] / r["Quantity"] if r["Quantity"] != 0 else 0, axis=1)
    return merged.drop(columns=["TotalCost"]).assign(**{"Average Cost": lambda x: x["Average Cost"].round(4)})

def import_transactions_csv(uploaded_file):
    content = uploaded_file.getvalue().decode("utf-8")
    df = pd.read_csv(io.StringIO(content))
    df.columns = [c.strip() for c in df.columns]
    required = ["Date", "Action", "Symbol", "Quantity", "Price"]
    if any(c not in df.columns for c in required):
        raise ValueError("Missing required columns")

    def clean_number(val):
        if pd.isna(val) or str(val).strip() in ["", "--", "nan"]:
            return 0.0
        s = str(val).replace("$", "").replace(",", "").replace("(", "-").replace(")", "").strip()
        try: return float(s)
        except: return 0.0

    df["Date"] = pd.to_datetime(df["Date"].astype(str).str.split(" as of ").str[0], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

    holdings = load_holdings().copy()
    if holdings.empty:
        holdings = pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"])

    pos = {}
    for _, row in holdings.iterrows():
        key = (row["Symbol"], row["Type"])
        pos[key] = {"Quantity": float(row["Quantity"]), "Average Cost": float(row["Average Cost"]),
                    "Total Cost": float(row["Quantity"]) * float(row["Average Cost"])}

    cash_key = ("CASH", "Cash")
    if cash_key not in pos:
        pos[cash_key] = {"Quantity": 0.0, "Average Cost": 1.0, "Total Cost": 0.0}

    for _, row in df.iterrows():
        action = str(row["Action"]).strip().lower()
        symbol_raw = str(row.get("Symbol", "")).strip()
        qty = clean_number(row["Quantity"])
        price = clean_number(row["Price"])
        amount = clean_number(row.get("Amount", 0))

        if any(x in action for x in ["dividend", "interest", "bank interest"]):
            if amount != 0:
                pos[cash_key]["Quantity"] += amount
                pos[cash_key]["Total Cost"] = pos[cash_key]["Quantity"]
            continue

        if not symbol_raw or symbol_raw.lower() in ["nan", "", "--"]:
            continue

        if ("option" in action) or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}.*[CP]", symbol_raw, re.I):
            h_type = "Option"
            symbol = convert_option_to_occ(symbol_raw)
            multiplier = 100
        else:
            h_type = "ETF" if any(x in symbol_raw.upper() for x in ["BND","AGG","TLT","QQQ","SPY","TQQQ","SQQQ"]) else "Stock"
            symbol = symbol_raw.upper()
            multiplier = 1

        key = (symbol, h_type)
        is_buy = any(x in action for x in ["buy", "buy to open", "buy to close"])
        is_sell = any(x in action for x in ["sell", "sell to close", "sell to open"])
        if not (is_buy or is_sell):
            continue

        signed_qty = abs(qty) if is_buy else -abs(qty)
        if key not in pos:
            pos[key] = {"Quantity": 0.0, "Average Cost": 0.0, "Total Cost": 0.0}

        current_qty = pos[key]["Quantity"]
        current_total = pos[key]["Total Cost"]
        new_qty = current_qty + signed_qty

        if signed_qty > 0:
            trade_cost = abs(signed_qty) * price
            new_total = current_total + trade_cost
            new_avg = new_total / new_qty if new_qty != 0 else 0.0
        else:
            new_avg = pos[key]["Average Cost"]
            new_total = new_avg * new_qty

        if abs(new_qty) < 1e-8:
            pos.pop(key, None)
        else:
            pos[key] = {"Quantity": new_qty, "Average Cost": round(new_avg, 4), "Total Cost": new_total}

        cash_change = amount if amount != 0 else -signed_qty * price * multiplier
        pos[cash_key]["Quantity"] += cash_change
        pos[cash_key]["Total Cost"] = pos[cash_key]["Quantity"]

    records = []
    for (sym, typ), data in pos.items():
        if abs(data["Quantity"]) < 1e-8 and typ != "Cash":
            continue
        records.append({"Symbol": sym, "Type": typ, "Quantity": round(data["Quantity"], 4),
                        "Average Cost": round(data["Average Cost"], 4)})
    return pd.DataFrame(records)

# -------------------- SIDEBAR --------------------
st.sidebar.header("📥 Portfolio Management")

with st.sidebar.expander("➕ Add Asset", expanded=False):
    a_type = st.selectbox("Asset Type", ["Stock", "ETF", "Option", "Cash"], key="add_asset_type")
    with st.form("add_asset_form"):
        if a_type == "Option":
            st.caption("Premium is **per share**. One contract = 100 shares.")
            underlying = st.text_input("Underlying", value="").upper().strip()
            exp_date = st.date_input("Expiration", value=datetime.now().date() + timedelta(days=30),
                                     min_value=datetime.now().date())
            strike = st.number_input("Strike", min_value=0.01, value=100.0, step=0.5, format="%.2f")
            cp = st.selectbox("Call / Put", ["Call", "Put"])
            position = st.selectbox("Bought / Sold", ["Bought (Long)", "Sold (Short)"])
            yy, mm, dd = f"{exp_date.year%100:02d}", f"{exp_date.month:02d}", f"{exp_date.day:02d}"
            strike_str = f"{int(round(strike*1000)):08d}"
            cp_letter = "C" if cp == "Call" else "P"
            preview = f"{underlying}{yy}{mm}{dd}{cp_letter}{strike_str}" if underlying else "..."
            st.code(f"OCC → {preview}")
            qty = st.number_input("Contracts", min_value=0.0, step=1.0, value=1.0)
            cost = st.number_input("Premium per share", min_value=0.0, value=0.0, step=0.01, format="%.4f")
        elif a_type == "Cash":
            qty = st.number_input("Amount ($)", min_value=0.0, step=100.0, value=0.0)
            cost = 1.0
        else:
            sym = st.text_input("Symbol", value="").upper().strip()
            qty = st.number_input("Shares", min_value=0.0, step=1.0, value=0.0)
            cost = st.number_input("Avg Cost", min_value=0.0, value=0.0, step=0.01, format="%.4f")

        if st.form_submit_button("Add to Portfolio", type="primary"):
            if a_type == "Option":
                if not underlying:
                    st.error("Underlying required")
                    st.stop()
                yy, mm, dd = f"{exp_date.year%100:02d}", f"{exp_date.month:02d}", f"{exp_date.day:02d}"
                strike_str = f"{int(round(strike*1000)):08d}"
                cp_letter = "C" if cp == "Call" else "P"
                sym = f"{underlying}{yy}{mm}{dd}{cp_letter}{strike_str}"
                qty = -abs(qty) if position == "Sold (Short)" else abs(qty)
            elif a_type == "Cash":
                sym = "CASH"
            elif not sym:
                st.error("Symbol required")
                st.stop()
            if qty == 0:
                st.error("Quantity cannot be zero")
                st.stop()
            df = load_holdings()
            new_row = pd.DataFrame([{"Symbol": sym, "Type": a_type, "Quantity": qty, "Average Cost": cost}])
            save_holdings(merge_holdings(df, new_row))
            st.success(f"Added {sym}")
            st.rerun()

with st.sidebar.expander("📜 Upload Transactions CSV", expanded=True):
    file_tx = st.file_uploader("Transactions CSV", type=["csv"], key="tx_uploader")
    if file_tx is not None:
        if st.button("🚀 Process Transactions", type="primary", use_container_width=True):
            try:
                updated = import_transactions_csv(file_tx)
                save_holdings(updated)
                st.success("Holdings + Cash updated")
                st.balloons()
                st.rerun()
            except Exception as e:
                st.error(str(e))

holdings_preview = load_holdings()
if not holdings_preview.empty:
    with st.sidebar.expander("🗑️ Delete Holding"):
        to_delete = st.selectbox("Symbol", holdings_preview["Symbol"].tolist())
        if st.button("Delete", type="primary"):
            save_holdings(holdings_preview[holdings_preview["Symbol"] != to_delete])
            st.rerun()

if st.sidebar.button("🗑️ Reset My Data", type="secondary"):
    save_holdings(pd.DataFrame(columns=["Symbol", "Type", "Quantity", "Average Cost"]))
    st.rerun()


# -------------------- HOME --------------------
if page == "🏠 Home":
    st.title("Portfolio Pulse")

    holdings = load_holdings()
    total_value = 0.0
    hist_df = pd.DataFrame(columns=["Date", "Value"])

    if not holdings.empty:
        with st.spinner("Updating live prices..."):
            holdings["Price"] = holdings.apply(lambda x: get_mark_price(x["Symbol"], x["Type"]), axis=1)
            holdings["Multiplier"] = holdings["Type"].apply(lambda t: 100 if t == "Option" else 1)
            holdings["Market Value"] = holdings["Price"] * holdings["Quantity"] * holdings["Multiplier"]
            holdings["P&L ($)"] = (holdings["Price"] - holdings["Average Cost"]) * holdings["Quantity"] * holdings["Multiplier"]
            total_value = holdings["Market Value"].sum()
            holdings["Weight (%)"] = (holdings["Market Value"] / total_value * 100) if total_value else 0
            total_pnl = holdings["P&L ($)"].sum()

        hist_df = update_history(total_value)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Value", f"${total_value:,.2f}")
        if len(hist_df) >= 2:
            prev = hist_df.iloc[-2]["Value"]
            chg = total_value - prev
            pct = (chg / prev * 100) if prev else 0
            c2.metric("Day Change", f"${chg:,.2f}", delta=f"{pct:.2f}%")
        else:
            c2.metric("Day Change", "—")
        c3.metric("Total P&L", f"${total_pnl:,.2f}")
        c4.metric("Positions", len(holdings))
        st.caption(f"Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Portfolio Performance
    st.subheader("Portfolio Performance")
    timeframes = ["1W", "1M", "6M", "YTD", "1Y", "Lifetime"]
    tf = st.radio("tf_port", timeframes, horizontal=True, label_visibility="collapsed",
                  key="hist_timeframe", index=timeframes.index(st.session_state.get("hist_timeframe", "1M")))

    if not hist_df.empty:
        hist_df["Date"] = pd.to_datetime(hist_df["Date"])
        now = datetime.now()
        start_map = {"1W": now-timedelta(days=7), "1M": now-timedelta(days=30),
                     "6M": now-timedelta(days=180), "YTD": datetime(now.year,1,1),
                     "1Y": now-timedelta(days=365), "Lifetime": hist_df["Date"].min()}
        filtered = hist_df[hist_df["Date"] >= start_map[tf]]
        fig = go.Figure(go.Scatter(x=filtered["Date"], y=filtered["Value"], mode="lines",
                                   line=dict(color="#00d1b2", width=2.5),
                                   fill="tozeroy", fillcolor="rgba(0,209,178,0.08)"))
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          height=340, margin=dict(l=10,r=10,t=10,b=10),
                          xaxis=dict(showgrid=False, fixedrange=True),
                          yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", fixedrange=True),
                          showlegend=False)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Current Positions
    st.subheader("Current Positions")
    if holdings.empty:
        st.info("No holdings yet.")
    else:
        display_df = holdings[["Symbol","Type","Quantity","Average Cost","Price","Market Value","Weight (%)","P&L ($)"]].copy()
        selection = st.dataframe(
            display_df.style.format({
                "Quantity":"{:,.2f}", "Average Cost":"${:,.4f}", "Price":"${:,.2f}",
                "Market Value":"${:,.2f}", "Weight (%)":"{:.1f}%", "P&L ($)":"${:,.2f}"
            }),
            use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key="pos_table"
        )

        selected_symbol = None
        if selection and selection.selection.rows:
            selected_symbol = display_df.iloc[selection.selection.rows[0]]["Symbol"]
        else:
            selected_symbol = st.session_state.get("last_chart_symbol", display_df.iloc[0]["Symbol"])
        st.session_state["last_chart_symbol"] = selected_symbol

    # ============================================================
    # EQUITY CHART
    # ============================================================
    st.markdown("---")
    st.subheader("Equity Chart")
    
    # Text box so you can type any symbol
    col_sym, col_tf, col_type = st.columns([2, 3, 1])
    
    with col_sym:
        # Pre-fill with the last selected / clicked symbol
        default_sym = st.session_state.get("last_chart_symbol", 
                                           display_df.iloc[0]["Symbol"] if not holdings.empty else "AAPL")
        typed_symbol = st.text_input(
            "Symbol",
            value=default_sym,
            key="chart_symbol_input",
            label_visibility="collapsed",
            placeholder="e.g. AAPL, TQQQ, SPY..."
        ).upper().strip()
    
    with col_tf:
        tf_eq = st.radio(
            "tf_eq",
            timeframes,
            horizontal=True,
            label_visibility="collapsed",
            key="equity_timeframe",
            index=timeframes.index(st.session_state.get("equity_timeframe", "1M"))
        )
    
    with col_type:
        chart_type = st.selectbox(
            "Chart",
            ["Line", "Candlestick"],
            label_visibility="collapsed",
            key="chart_type"
        )
    
    # Use the typed symbol (table click still works because it updates session state)
    selected_symbol = typed_symbol if typed_symbol else default_sym
    st.session_state["last_chart_symbol"] = selected_symbol
    
    st.caption(f"Showing: **{selected_symbol}**")
    
    # ---- Chart rendering (same as before) ----
    try:
        period_map = {
            "1W": ("7d", "1h"),
            "1M": ("1mo", "1d"),
            "6M": ("6mo", "1d"),
            "YTD": ("ytd", "1d"),
            "1Y": ("1y", "1d"),
            "Lifetime": ("max", "1wk")
        }
        period, interval = period_map[tf_eq]
        hist = yf.Ticker(selected_symbol).history(period=period, interval=interval)
    
        if hist.empty:
            st.warning(f"No price data found for {selected_symbol}")
        else:
            if chart_type == "Line":
                fig2 = go.Figure(go.Scatter(
                    x=hist.index, y=hist["Close"],
                    mode="lines",
                    line=dict(color="#00d1b2", width=2.2),
                    fill="tozeroy",
                    fillcolor="rgba(0, 209, 178, 0.07)"
                ))
            else:
                fig2 = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                     vertical_spacing=0.03, row_heights=[0.72, 0.28])
                fig2.add_trace(go.Candlestick(
                    x=hist.index,
                    open=hist["Open"], high=hist["High"],
                    low=hist["Low"], close=hist["Close"],
                    increasing_line_color="#00d1b2",
                    decreasing_line_color="#ff6b6b",
                    name="Price"
                ), row=1, col=1)
                fig2.add_trace(go.Bar(
                    x=hist.index, y=hist["Volume"],
                    marker_color="rgba(0, 209, 178, 0.35)",
                    name="Volume"
                ), row=2, col=1)
                fig2.update_xaxes(rangeslider_visible=False)
    
            fig2.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=420,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(showgrid=False, fixedrange=True),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", fixedrange=True),
                showlegend=False
            )
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
    
            # Quick stats
            last = hist["Close"].iloc[-1]
            prev = hist["Close"].iloc[-2] if len(hist) > 1 else last
            chg = last - prev
            pct = (chg / prev * 100) if prev else 0
            c1, c2, c3 = st.columns(3)
            c1.metric("Last", f"${last:,.2f}")
            c2.metric("Change", f"${chg:,.2f}", delta=f"{pct:.2f}%")
            c3.metric("Range", f"${hist['Low'].min():,.2f} – ${hist['High'].max():,.2f}")
    
    except Exception as e:
        st.warning(f"Could not load chart for {selected_symbol}: {e}")

        # ============================================================
    # CURRENT PORTFOLIO ANALYSIS
    # ============================================================
    st.markdown("---")
    st.subheader("Portfolio Analysis")

    if holdings.empty:
        st.info("Add holdings to see analysis.")
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
            description = "Built for maximum upside, driven by leveraged long calls."
            risks = "High volatility and large drawdown risk in corrections."
        elif equity_pct > 70 and option_pct < 10 and bond_pct < 20:
            style = "Growth-Oriented Equity"
            best_market = "Bull Market"
            description = "Primarily equity-focused with limited defensive holdings."
            risks = "Vulnerable to broad market pullbacks."
        elif bond_pct > 30 or (bond_pct + cash_pct) > 40:
            style = "Balanced / Defensive"
            best_market = "Sideways to Mildly Bearish"
            description = "Meaningful allocation to bonds/cash provides ballast."
            risks = "Will lag in strong bull markets."
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
    # PORTFOLIO PROJECTION (auto-runs with defaults)
    # ============================================================
    st.markdown("---")
    st.subheader("Portfolio Projection")

    col1, col2, col3 = st.columns(3)
    with col1:
        annual_contribution = st.slider(
            "Annual Contribution ($)", 0, 100000, 22000, 1000
        )
    with col2:
        expected_return = st.slider(
            "Expected Annual Return (%)", 0.0, 25.0, 12.0, 0.5
        )
    with col3:
        years = st.slider(
            "Time Horizon (Years)", 1, 50, 25
        )

    # Auto-run on every load / change
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
        x=years_list,
        y=values,
        mode="lines",
        line=dict(color="#00d1b2", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(0, 209, 178, 0.08)",
        name="Projected Value"
    ))
    fig_proj.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=380,
        margin=dict(l=10, r=10, t=20, b=10),
        xaxis_title="Years from Now",
        yaxis_title=None,
        xaxis=dict(showgrid=False, fixedrange=True),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", fixedrange=True),
        showlegend=False
    )
    st.plotly_chart(fig_proj, use_container_width=True, config={"displayModeBar": False})
    st.caption("Assumptions: contributions at end of each year • returns compounded annually")

# -------------------- NEWS --------------------
elif page == "📰 News":
    st.title("Market News")

    YOUTUBE_API_KEY = st.secrets.get("youtube", {}).get("api_key")
    if not YOUTUBE_API_KEY:
        st.error("YouTube API key missing in secrets.")
        st.stop()

    CHANNELS = {
        "Meet Kevin": {"channel_id": "UCUvvj5lwue7PspotMDjk5UA", "count": 3},
        "Bravos Research": {"channel_id": "UCOHxDwCcOzBaLkeTazanwcw", "count": 2},
        "FX Evolution": {"channel_id": "UCvJZEG5x-DVYZKTz--pS39w", "count": 1}
    }

    @st.cache_data(ttl=1800)
    def get_recent_videos(channel_id: str, max_results: int = 3):
        uploads_playlist_id = "UU" + channel_id[2:]
        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {"key": YOUTUBE_API_KEY, "playlistId": uploads_playlist_id,
                  "part": "snippet", "maxResults": max_results}
        try:
            data = requests.get(url, params=params, timeout=15).json()
            if "error" in data:
                return []
            videos = []
            for item in data.get("items", []):
                sn = item["snippet"]
                vid = sn["resourceId"]["videoId"]
                videos.append({
                    "title": sn["title"],
                    "link": f"https://www.youtube.com/watch?v={vid}",
                    "published": sn["publishedAt"][:10],
                    "video_id": vid,
                    "thumbnail": sn["thumbnails"]["medium"]["url"]
                })
            return videos
        except Exception:
            return []

    for name, cfg in CHANNELS.items():
        st.subheader(name)
        videos = get_recent_videos(cfg["channel_id"], cfg["count"])
        if not videos:
            st.info(f"Could not load {name}")
            continue
        for v in videos:
            c1, c2 = st.columns([1, 3])
            with c1:
                st.image(v["thumbnail"], use_container_width=True)
            with c2:
                st.markdown(f"### [{v['title']}]({v['link']})")
                st.caption(f"Published: {v['published']}")
                with st.expander("AI Summary", expanded=False):
                    try:
                        from youtube_transcript_api import YouTubeTranscriptApi
                        transcript = YouTubeTranscriptApi().fetch(v["video_id"])
                        text = " ".join([s.text for s in transcript])
                        words = text.split()
                        summary = " ".join(words[:380]) + "\n\n...\n\n" + " ".join(words[-180:]) if len(words) > 650 else text
                        st.write(summary)
                    except Exception as e:
                        st.caption("Summary unavailable")
            st.markdown("---")
                    

  
                    
